"""Typed model for Claude Code tool calls and their results.

Claude Code's TUI surfaces every action as ``⏺ Tool(args)`` followed
by an indented ``⎿ result`` block. Rather than treat each one as a
generic ``extracted_command`` string, this module parses the call into
a structured ``ToolCall`` carrying:

- **name** — the canonical tool identifier (``Bash``, ``Write``, …).
- **args** — the raw argument string from inside the parens.
- **category** — high-level grouping (``read``, ``write``, ``exec``,
  ``search``, ``fetch``, ``agent``, ``meta``, ``other``).
- **risk** — ``safe`` / ``normal`` / ``elevated`` / ``destructive``.
  For ``Bash``, risk is refined by inspecting the command string for
  known dangerous patterns (``rm -rf``, ``dd``, ``mkfs``, ``chmod -R``,
  …). For ``Write``/``Edit``, paths under ``/etc``, ``~/.ssh``,
  ``/var/`` get bumped to ``destructive``.
- **icon** — one emoji for terminal + Telegram rendering.
- **summary** — one-line human description (``run npm install``,
  ``edit src/foo.py``).

The registry lives in ``TOOL_REGISTRY``; ``parse_tool_call`` is the
classifier entrypoint; ``parse_tool_results`` extracts every
completed ⏺/⎿ block from a snapshot.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable


# --- registry -------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    """Static metadata for a Claude Code tool."""
    category: str   # "read" | "write" | "exec" | "search" | "fetch" | "agent" | "meta"
    risk: str       # "safe" | "normal" | "elevated" | "destructive"
    icon: str
    verb: str       # used to build the one-line ``summary`` ("run", "edit", "read", …)


# Canonical names match what Claude Code prints in its pane. New tools
# can be added here without touching the parser.
TOOL_REGISTRY: dict[str, ToolSpec] = {
    # --- read ---
    "Read":          ToolSpec("read",   "safe",      "📖", "read"),
    "BashOutput":    ToolSpec("read",   "safe",      "📖", "tail shell"),
    "NotebookRead":  ToolSpec("read",   "safe",      "📓", "read notebook"),

    # --- search / list ---
    "Glob":          ToolSpec("search", "safe",      "🔍", "glob"),
    "Grep":          ToolSpec("search", "safe",      "🔍", "search"),
    "LS":            ToolSpec("search", "safe",      "📁", "list"),
    "WebSearch":     ToolSpec("search", "safe",      "🌐", "search web"),

    # --- write / edit ---
    "Write":         ToolSpec("write",  "elevated",  "✏️",  "create file"),
    "Edit":          ToolSpec("write",  "elevated",  "✏️",  "edit file"),
    "MultiEdit":     ToolSpec("write",  "elevated",  "✏️",  "edit multiple"),
    "NotebookEdit":  ToolSpec("write",  "elevated",  "📓", "edit notebook"),

    # --- exec ---
    "Bash":          ToolSpec("exec",   "normal",    "▶️",  "run"),
    "Run":           ToolSpec("exec",   "normal",    "▶️",  "run"),
    "KillShell":     ToolSpec("exec",   "normal",    "⏹️",  "kill shell"),

    # --- network ---
    "WebFetch":      ToolSpec("fetch",  "elevated",  "🌐", "fetch URL"),

    # --- meta / sub-agents ---
    "Task":          ToolSpec("agent",  "elevated",  "🤖", "spawn agent"),
    "TodoWrite":     ToolSpec("meta",   "safe",      "📋", "update tasks"),
    "Skill":         ToolSpec("meta",   "safe",      "🛠️", "invoke skill"),
    "ToolSearch":    ToolSpec("meta",   "safe",      "🔧", "search tools"),
    "SlashCommand":  ToolSpec("meta",   "safe",      "⌨️",  "slash command"),
    "ExitPlanMode":  ToolSpec("meta",   "safe",      "📋", "exit plan mode"),
}


# Default for tools we don't know yet — keeps the system extensible.
_UNKNOWN_SPEC = ToolSpec("other", "normal", "🔧", "invoke")


def lookup(name: str) -> ToolSpec:
    """Return the spec for ``name``, or a sensible default."""
    return TOOL_REGISTRY.get(name, _UNKNOWN_SPEC)


# --- risk refinement for Bash --------------------------------------------

# Patterns that mark a Bash command as destructive. Order matters only for
# readability — risks are OR'd together, so the first match wins.
_DESTRUCTIVE_BASH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brm\s+(?:-[a-zA-Z]*[rRfF][a-zA-Z]*\s+|-{1,2}recursive\b)"),
    re.compile(r"\bdd\b\s+if="),
    re.compile(r"\bmkfs(?:\.\w+)?\b"),
    re.compile(r"\bshred\b"),
    re.compile(r"\bchmod\s+-R\b"),
    re.compile(r"\bchown\s+-R\b"),
    re.compile(r"\bgit\s+(?:reset\s+--hard|push\s+(?:--force|-f)|clean\s+-[fdx]+)"),
    re.compile(r":\(\)\s*\{[^}]*:\|[:&]\s*\}\s*;"),  # fork bomb
    re.compile(r"\b/dev/sd[a-z][0-9]*\b"),
    re.compile(r"\bsudo\b"),
)

# Bash patterns that are elevated (not destructive but worth noting).
_ELEVATED_BASH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bcurl\s.*\|\s*(?:bash|sh|zsh)\b"),      # pipe-to-shell
    re.compile(r"\bwget\s.*\|\s*(?:bash|sh|zsh)\b"),
    re.compile(r"\bpip\s+install\b"),
    re.compile(r"\bnpm\s+install\b"),
    re.compile(r"\bbrew\s+install\b"),
    re.compile(r"\bdocker\s+(?:run|exec)\b"),
)


def _refine_bash_risk(args: str) -> str:
    if not args:
        return "normal"
    for pat in _DESTRUCTIVE_BASH_PATTERNS:
        if pat.search(args):
            return "destructive"
    for pat in _ELEVATED_BASH_PATTERNS:
        if pat.search(args):
            return "elevated"
    return "normal"


# --- risk refinement for file-mutating tools -----------------------------

_SENSITIVE_PATH_PREFIXES = (
    "/etc/", "/var/", "/usr/", "/boot/", "/sys/", "/proc/",
)
_SENSITIVE_HOME_SUFFIXES = (
    ".ssh", "/.ssh/", ".aws", "/.aws/", ".kube",
    "/.gnupg/", "/Library/Keychains/",
)


def _refine_file_risk(args: str) -> str:
    """Bump Write/Edit/MultiEdit to destructive when touching system paths."""
    if not args:
        return "elevated"
    path = args.split(",", 1)[0].strip().strip('"').strip("'")
    expanded = os.path.expanduser(path)
    abs_path = os.path.abspath(expanded)
    if abs_path.startswith(_SENSITIVE_PATH_PREFIXES):
        return "destructive"
    home = os.path.expanduser("~")
    if expanded.startswith(home):
        rest = expanded[len(home):]
        for suffix in _SENSITIVE_HOME_SUFFIXES:
            if suffix in rest:
                return "destructive"
    return "elevated"


# --- the dataclass --------------------------------------------------------


@dataclass(frozen=True)
class ToolCall:
    """A parsed Claude Code tool invocation."""
    name: str
    args: str
    category: str
    risk: str
    icon: str
    summary: str

    @property
    def label(self) -> str:
        """Short display label used in panels and Telegram cards."""
        return f"{self.icon} {self.name} · {self.category}"

    def with_risk(self, new_risk: str) -> "ToolCall":
        # Frozen dataclasses don't support __setattr__; rebuild instead.
        return ToolCall(
            name=self.name,
            args=self.args,
            category=self.category,
            risk=new_risk,
            icon=self.icon,
            summary=self.summary,
        )


# --- parsing --------------------------------------------------------------

# A permissive ⏺-line matcher. Tool names may be **multi-word** in
# Claude Code's pane display — ``Web Search(...)`` / ``Web Fetch(...)``
# / ``Tool Search(...)`` / ``Slash Command(...)`` — even though their
# canonical identifiers are single-word (``WebSearch``, ``WebFetch``,
# ``ToolSearch``, ``SlashCommand``). We accept one-or-more CamelCase
# tokens separated by single spaces, then collapse the spaces when
# looking the name up in the registry.
_TOOL_LINE_RE = re.compile(
    r"^\s*(?:⏺\s*)?"
    r"(?P<tool>[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*)*)"
    r"\s*\(\s*(?P<args>.*?)\s*\)\s*$",
)


def _canonicalize_tool_name(name: str) -> str:
    """Collapse display-style spaces in tool names so registry lookups
    succeed for the multi-word display form ``Web Search`` (canonical
    ``WebSearch``)."""
    return name.replace(" ", "")

# Lines that look like part of an indented result block.
_RESULT_LINE_RE = re.compile(r"^\s*(?:⎿|↳|\|)\s?(?P<body>.*)$")


def parse_tool_call(text: str) -> ToolCall | None:
    """Pull the first ``Tool(args)`` from ``text``.

    Returns ``None`` if no recognised tool-call line is present. When
    multiple tools appear, the LAST one wins — that's the most recent
    action in the pane and the one the user is being asked to approve.
    """
    if not text:
        return None
    last_match: ToolCall | None = None
    for line in text.splitlines():
        m = _TOOL_LINE_RE.match(line)
        if not m:
            continue
        last_match = _build_tool_call(
            _canonicalize_tool_name(m.group("tool")),
            m.group("args") or "",
        )
    return last_match


def _build_tool_call(name: str, args: str) -> ToolCall:
    spec = lookup(name)
    risk = spec.risk
    if name in ("Bash", "Run"):
        risk = _refine_bash_risk(args)
    elif name in ("Write", "Edit", "MultiEdit"):
        risk = _refine_file_risk(args)

    summary = _summarize(name, args, spec)
    return ToolCall(
        name=name,
        args=args,
        category=spec.category,
        risk=risk,
        icon=spec.icon,
        summary=summary,
    )


def _summarize(name: str, args: str, spec: ToolSpec) -> str:
    """One-line human-readable description of the call."""
    arg_preview = args.strip()
    if len(arg_preview) > 80:
        arg_preview = arg_preview[:77].rstrip() + "…"
    if not arg_preview:
        return spec.verb
    return f"{spec.verb} {arg_preview}"


# --- result parsing -------------------------------------------------------


@dataclass(frozen=True)
class ToolResult:
    """A completed tool call paired with its outcome."""
    tool: ToolCall
    outcome: str            # "success" | "error" | "partial" | "unknown"
    summary: str            # one-line of result body
    body: str = ""          # raw indented block (may be multi-line)


_ERROR_MARKERS = (
    "error", "failed", "permission denied", "no such file",
    "exception", "traceback", "fatal:",
)
_PARTIAL_MARKERS = (
    "waiting", "pending", "in progress", "interrupted",
    "esc to interrupt", "running",
)


def parse_tool_results(snapshot: str) -> list[ToolResult]:
    """Extract every completed ``⏺ Tool(args)`` + ``⎿ <body>`` pair.

    Walks forward through ``snapshot``. When a ⏺ line is seen, the
    following indented lines (starting with ``⎿`` or whitespace) form
    its result body. The outcome is classified from the body content:

    - "error"   if any error-ish keyword appears in the body
    - "partial" if "running" / "interrupted" / "esc to interrupt" appears
    - "success" if a body exists with none of the above
    - "unknown" if no body was found before the next ⏺ line

    The list is returned newest-last, so callers can take ``[-1]`` to
    get the most recent completed tool.
    """
    if not snapshot:
        return []
    lines = snapshot.splitlines()
    results: list[ToolResult] = []
    current_tool: ToolCall | None = None
    current_body: list[str] = []

    def flush() -> None:
        nonlocal current_tool, current_body
        if current_tool is None:
            return
        body = "\n".join(current_body).strip()
        outcome = _classify_outcome(body)
        summary = body.splitlines()[0].strip() if body else ""
        if len(summary) > 200:
            summary = summary[:197].rstrip() + "…"
        results.append(ToolResult(
            tool=current_tool, outcome=outcome,
            summary=summary, body=body,
        ))
        current_tool = None
        current_body = []

    for line in lines:
        stripped = line.lstrip()
        m = _TOOL_LINE_RE.match(line)
        if m and stripped.startswith("⏺"):
            # New tool call — flush whatever came before.
            flush()
            current_tool = _build_tool_call(
                _canonicalize_tool_name(m.group("tool")),
                m.group("args") or "",
            )
            continue
        if current_tool is None:
            continue
        # Inside a tool block. Either a ⎿ marker line, or whitespace
        # indented continuation. Anything else ends the block.
        rm = _RESULT_LINE_RE.match(line)
        if rm:
            current_body.append(rm.group("body"))
            continue
        if line.startswith(("    ", "\t")) or not line.strip():
            # Indented continuation or blank — keep collecting.
            current_body.append(line.rstrip())
            continue
        flush()
    flush()
    return results


def _classify_outcome(body: str) -> str:
    if not body:
        return "unknown"
    low = body.lower()
    for marker in _ERROR_MARKERS:
        if marker in low:
            return "error"
    for marker in _PARTIAL_MARKERS:
        if marker in low:
            return "partial"
    return "success"


# --- convenience for callers ---------------------------------------------


def categories() -> Iterable[str]:
    """Distinct categories present in the registry — useful for the
    policy file's per-category configuration section."""
    return sorted({spec.category for spec in TOOL_REGISTRY.values()})
