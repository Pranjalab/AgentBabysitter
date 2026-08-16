"""Pure ABS START conversation helpers (PLAN.md Step 1.5 / 4.5).

The daemon-side ABS START flow is: ``ABS START`` → pick a project → (optionally
name a new folder) → pick a permission mode → HANDOFF. This module holds the
*pure* pieces of that flow — option enumeration, keyboard/menu rendering,
callback-data grammar, numbered-text parsing, and the D6 folder-name jail — so
the flow logic is unit-testable without a running poller, a network, or Telegram
(PLAN.md 4.4). The stateful driving (send/edit messages, launch the engine) lives
in :class:`absd.daemon.Poller`; everything here is a value transform.

Callback-data grammar (kept well under Telegram's 64-byte limit, and namespaced
so a stray tap on an old keyboard can never collide with another feature):

  ``as:p:<i>``  — pick project option ``i`` (0-based, indexes the flow's option list)
  ``as:nf``     — the "➕ New folder" option
  ``as:m:n``    — permission mode Normal
  ``as:m:a``    — permission mode Away (acceptEdits, D5)

Numbered-text fallback (PLAN.md 4.5): the same menus are rendered as a numbered
list, and replying with the number selects the same option — so a client whose
inline keyboard misbehaves still drives the whole flow.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# D6 folder-name jail: the ONLY names a Telegram-created folder may take. Exactly
# the Step 1.5 regex — letters, digits, dot, underscore, hyphen; 1..64 chars.
FOLDER_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")

# Callback-data tokens (see module docstring).
CB_NEWFOLDER = "as:nf"
CB_PROJECT_PREFIX = "as:p:"
CB_MODE_NORMAL = "as:m:n"
CB_MODE_AWAY = "as:m:a"
# The "🏖 Sandbox…" project entry, and the sandbox sub-picker (3.2).
CB_SANDBOX = "as:sbx"
CB_SANDBOX_NEW = "as:sb:new"
CB_SANDBOX_PREFIX = "as:sb:"

# Permission modes (D5). "normal" = default prompts; "away" = acceptEdits.
MODE_NORMAL = "normal"
MODE_AWAY = "away"

# The in-container launcher (baked into the sandbox image, 3.2).
SANDBOX_LAUNCHER = "absd-session"


@dataclass
class ProjectOption:
    """One row of the project keyboard.

    ``kind`` is ``"project"`` (a concrete directory) or ``"newfolder"`` (the
    "➕ New folder" sentinel). ``path`` is the absolute directory for a project,
    ``None`` for the sentinel.
    """

    kind: str
    label: str
    path: str | None = None


def validate_folder_name(name: str) -> tuple[bool, str]:
    """Validate a Telegram-supplied new-folder name (D6). Returns
    ``(ok, error_message)``; ``error_message`` is empty when ok.

    Rejects (in order): empty/whitespace, path separators or ``..``/``.``,
    anything the regex does not allow (spaces, unicode, slashes, over 64 chars),
    and leading dots beyond what the regex permits. The check is deliberately
    strict and allow-list based — a name is valid only if it is a single, plain
    path segment that can never escape the workspace root.
    """
    raw = (name or "").strip()
    if not raw:
        return False, "Folder name can't be empty."
    if "/" in raw or "\\" in raw or "\x00" in raw:
        return False, "Folder name can't contain path separators."
    if raw in (".", ".."):
        return False, "Folder name can't be '.' or '..'."
    if not FOLDER_NAME_RE.match(raw):
        return False, (
            "Use only letters, digits, dot, underscore or hyphen (max 64 chars)."
        )
    return True, ""


def list_workspace_children(root: Path | None) -> list[Path]:
    """Direct child directories of ``root`` (sorted), or ``[]`` if root is unset
    or unreadable. Not recursive — only the immediate children are start targets
    (D6). Hidden dirs (dot-prefixed) are skipped as noise."""
    if root is None:
        return []
    try:
        children = [
            entry
            for entry in os.scandir(root)
            if entry.is_dir() and not entry.name.startswith(".")
        ]
    except OSError:
        return []
    return sorted((Path(e.path) for e in children), key=lambda p: p.name.lower())


def enumerate_project_options(
    registered: list[tuple[str, str]],
    workspace_root: Path | None,
    with_sandbox: bool = False,
) -> list[ProjectOption]:
    """Build the ordered project-option list for the keyboard.

    ``registered`` is ``[(path, label), …]`` from the registry (already resolved).
    Order (PLAN.md Step 1.5): registered projects first (in registry order,
    existing directories only), then the direct children of ``workspace_root``
    not already registered, then a single "➕ New folder" sentinel (only when a
    workspace root is configured — without one there is nowhere jail-safe to
    create a folder, D6). ``with_sandbox`` (3.2) appends a "🏖 Sandbox…" entry that
    branches into the sandbox picker.
    """
    seen: set[str] = set()
    options: list[ProjectOption] = []

    for path, label in registered:
        if not Path(path).is_dir():
            continue
        rp = str(Path(path).resolve())
        if rp in seen:
            continue
        seen.add(rp)
        options.append(ProjectOption(kind="project", label=label or Path(rp).name, path=rp))

    for child in list_workspace_children(workspace_root):
        rp = str(child.resolve())
        if rp in seen:
            continue
        seen.add(rp)
        options.append(ProjectOption(kind="project", label=child.name, path=rp))

    if workspace_root is not None:
        options.append(ProjectOption(kind="newfolder", label="➕ New folder", path=None))

    if with_sandbox:
        options.append(ProjectOption(kind="sandbox", label="🏖 Sandbox…", path=None))

    return options


def build_project_keyboard(options: list[ProjectOption]) -> dict[str, Any]:
    """Inline keyboard for the project step — one button per option, one per row
    (project labels can be long). Callback data per the module grammar."""
    rows: list[list[dict[str, str]]] = []
    for i, opt in enumerate(options):
        if opt.kind == "newfolder":
            data = CB_NEWFOLDER
        elif opt.kind == "sandbox":
            data = CB_SANDBOX
        else:
            data = f"{CB_PROJECT_PREFIX}{i}"
        rows.append([{"text": opt.label, "callback_data": data}])
    return {"inline_keyboard": rows}


def render_project_menu(options: list[ProjectOption]) -> str:
    """Numbered-text rendering of the project step (the keyboard fallback)."""
    if not options:
        return (
            "No projects registered and no workspace root set.\n"
            "From the terminal: abs project add <dir>  or  abs config "
            "workspace-root <dir>"
        )
    lines = ["📂 Which project? Tap a button below, or reply with its number:"]
    for i, opt in enumerate(options, start=1):
        lines.append(f"{i}. {opt.label}")
    return "\n".join(lines)


def build_mode_keyboard() -> dict[str, Any]:
    """Inline keyboard for the permission step (D5)."""
    return {
        "inline_keyboard": [
            [{"text": "🟢 Normal", "callback_data": CB_MODE_NORMAL}],
            [{"text": "🟡 Away (auto-accept edits)", "callback_data": CB_MODE_AWAY}],
        ]
    }


MODE_MENU_TEXT = (
    "🔐 Permission mode? Tap a button, or reply 1 / 2:\n"
    "1. 🟢 Normal — asks before file edits\n"
    "2. 🟡 Away — auto-accepts edits while you're out"
)

NEW_FOLDER_PROMPT = (
    "📝 Name the new folder (letters, digits, dot, underscore, hyphen; max 64).\n"
    "It will be created inside your workspace root."
)


def _parse_int(text: str) -> int | None:
    text = (text or "").strip()
    if not text.isdigit():
        return None
    try:
        return int(text)
    except ValueError:
        return None


def choose_project(
    data: str | None, text: str, options: list[ProjectOption]
) -> ProjectOption | None:
    """Resolve a project-step input to an option, or ``None`` if it doesn't
    select one.

    ``data`` is the callback data (``None`` for a text message); ``text`` is the
    message text for the numbered fallback. Callback wins when present.
    """
    if data:
        if data == CB_NEWFOLDER:
            for opt in options:
                if opt.kind == "newfolder":
                    return opt
            return None
        if data == CB_SANDBOX:
            for opt in options:
                if opt.kind == "sandbox":
                    return opt
            return None
        if data.startswith(CB_PROJECT_PREFIX):
            idx = _parse_int(data[len(CB_PROJECT_PREFIX):])
            if idx is not None and 0 <= idx < len(options):
                return options[idx]
        return None
    n = _parse_int(text)
    if n is not None and 1 <= n <= len(options):
        return options[n - 1]
    return None


# --------------------------------------------------------------------------- #
# Sandbox picker + launcher (3.2)
# --------------------------------------------------------------------------- #


def build_sandbox_keyboard(sandboxes: list[tuple[str, str]]) -> dict[str, Any]:
    """Inline keyboard for the sandbox sub-picker: one row per existing sandbox
    (``[(name, state), …]``) + a "➕ New sandbox" row. Callback ``as:sb:<i>`` /
    ``as:sb:new``."""
    rows: list[list[dict[str, str]]] = []
    for i, (name, state) in enumerate(sandboxes):
        rows.append([{"text": f"🏖 {name} ({state})", "callback_data": f"{CB_SANDBOX_PREFIX}{i}"}])
    rows.append([{"text": "➕ New sandbox", "callback_data": CB_SANDBOX_NEW}])
    return {"inline_keyboard": rows}


def render_sandbox_menu(sandboxes: list[tuple[str, str]]) -> str:
    lines = ["🏖 Which sandbox? Tap a button, or reply with a number:"]
    n = 0
    for name, state in sandboxes:
        n += 1
        lines.append(f"{n}. 🏖 {name} ({state})")
    lines.append(f"{n + 1}. ➕ New sandbox")
    return "\n".join(lines)


NEW_SANDBOX_PROMPT = (
    "📝 Name the new sandbox (letters, digits, dot, underscore, hyphen; max 64).\n"
    "A dedicated folder is created for it and Claude runs inside a container."
)


def choose_sandbox(data: str | None, text: str, count: int) -> "int | str | None":
    """Resolve a sandbox-picker input: a 0-based index, ``"new"``, or ``None``."""
    if data:
        if data == CB_SANDBOX_NEW:
            return "new"
        if data.startswith(CB_SANDBOX_PREFIX):
            idx = _parse_int(data[len(CB_SANDBOX_PREFIX):])
            if idx is not None and 0 <= idx < count:
                return idx
        return None
    n = _parse_int(text)
    if n is None:
        return None
    if 1 <= n <= count:
        return n - 1
    if n == count + 1:
        return "new"
    return None


def build_sandbox_launcher_argv(
    profile: str,
    away: bool,
    resume: bool = False,
    initial_prompt: str | None = None,
    model: str | None = None,
    restricted: bool = False,
    append_system_prompt: str | None = None,
) -> list[str]:
    """The in-container launcher args (after ``absd-session``): the profile, then
    the session options, then the claude passthrough flags.

    ``absd-session`` consumes ``--restricted`` (selects the bundled restricted
    system prompt inside the image), ``--model <m>`` and ``--append-system-prompt
    <text>`` itself, and forwards everything else to ``claude``. Away →
    ``--permission-mode acceptEdits`` (inside the box, no abs.sh ABS_AWAY); resume →
    ``--continue``; a pool initial prompt is a bare claude positional (flags first).
    ``session_exec_argv`` prepends ``docker exec -it <container> absd-session``.

    3.2-gap fix / Stage 3: ``model`` + ``restricted`` + ``append_system_prompt`` are
    the new, general capability — the restricted assistant is the first user
    (``model="haiku", restricted=True``); a normal sandbox session passes none and
    the argv is byte-for-byte what 3.2 built."""
    argv = [profile]
    if restricted:
        argv.append("--restricted")
    if model:
        argv += ["--model", model]
    if append_system_prompt:
        argv += ["--append-system-prompt", append_system_prompt]
    if away:
        # bypassPermissions, not acceptEdits: Away means nobody is at the desk,
        # and the thing that actually halts a session is a Bash approval, not a
        # file edit. `absd-session` forces the command guard on for an away
        # launch, and PreToolUse hooks still fire under bypassPermissions, so
        # the guard remains the backstop. Matches abs.sh's host path.
        argv += ["--permission-mode", "bypassPermissions"]
    if resume:
        argv.append("--continue")
    if initial_prompt:
        argv.append(initial_prompt)
    return argv


def choose_mode(data: str | None, text: str) -> str | None:
    """Resolve a mode-step input to ``MODE_NORMAL`` / ``MODE_AWAY`` / ``None``."""
    if data:
        if data == CB_MODE_NORMAL:
            return MODE_NORMAL
        if data == CB_MODE_AWAY:
            return MODE_AWAY
        return None
    n = _parse_int(text)
    if n == 1:
        return MODE_NORMAL
    if n == 2:
        return MODE_AWAY
    return None


def safe_join_under_root(root: Path, name: str) -> Path | None:
    """Return ``root/name`` only if it is provably inside ``root`` (D6 path jail).

    ``name`` must already have passed :func:`validate_folder_name`. This is the
    second, structural gate: it resolves the candidate and confirms the resolved
    path's parent is exactly ``root`` — so even a name that somehow slipped the
    regex could not escape. Returns ``None`` if the candidate would land outside
    ``root``.
    """
    root_resolved = root.resolve()
    candidate = (root_resolved / name).resolve()
    if candidate == root_resolved:
        return None
    try:
        if candidate.parent != root_resolved:
            return None
    except (OSError, ValueError):
        return None
    return candidate


def build_launcher_argv(
    script_path: str,
    profile: str,
    away: bool,
    resume: bool = False,
    initial_prompt: str | None = None,
) -> list[str]:
    """The exact launcher the engine runs (PLAN.md 4.2): reuse ``abs.sh`` via
    ``bash <SCRIPT_PATH> --profile <p> --daemon-start [--away] [--continue]
    [--prompt <text>]`` — never a Python reimplementation of the launcher.

    ``resume`` appends ``--continue``. ``abs.sh``'s arg parser treats
    ``--profile``/``--daemon-start``/``--away``/``--prompt`` as its own global
    flags; ``--continue`` and the ``--prompt`` VALUE reach ``claude`` (the value as
    claude's initial positional prompt). ``initial_prompt`` is passed as ONE argv
    element (``--prompt`` + the text) — no shell rejoining — so quotes, newlines and
    emoji survive intact through the engine command path (Step 1.7 pool forwarding).

    Why ``--prompt <text>`` and not a bare positional: a bare trailing positional
    (``bash abs.sh … "text"``) would be read by ``abs.sh``'s dispatch as an unknown
    *command* and die; the flag routes cleanly, and ``cmd_run`` re-emits the value
    as claude's initial positional prompt."""
    argv = ["bash", script_path, "--profile", profile, "--daemon-start"]
    if away:
        argv.append("--away")
    if resume:
        argv.append("--continue")
    if initial_prompt:
        argv += ["--prompt", initial_prompt]
    return argv


# --------------------------------------------------------------------------- #
# Pool forwarding selection (Step 1.7, before HANDOFF)
# --------------------------------------------------------------------------- #

# Callback data for the pool-selection step.
CB_POOL_ALL = "as:pool:all"
CB_POOL_SKIP = "as:pool:skip"
CB_POOL_SEND = "as:pool:send"
CB_POOL_TOGGLE_PREFIX = "as:pool:t:"

# Preview truncation for the pool-selection screen.
POOL_PREVIEW_TRUNC = 60
#: Preview truncation INSIDE a toggle button. Much shorter than the message body:
#: Telegram wraps long button labels into unreadable blocks, and the full text is
#: already listed above the keyboard.
POOL_BUTTON_TRUNC = 28
#: How many messages get their own toggle button. Beyond this the keyboard becomes
#: a wall, so the screen falls back to "Send all / Skip" plus the text protocol
#: (`send 1,3`), which has no such limit.
POOL_TOGGLE_MAX = 8

# Prefix for the forwarded messages delivered as claude's initial prompt.
OFFLINE_PROMPT_PREFIX = "Messages received while you were offline:"


def pool_toggles_fit(count: int) -> bool:
    """True when ``count`` pooled messages get individual toggle buttons."""
    return 0 < count <= POOL_TOGGLE_MAX


def build_pool_keyboard(
    texts: list[str] | None = None, selected: set[int] | None = None
) -> dict[str, Any]:
    """Inline keyboard for the pool-selection step (Step 2.2).

    With a small enough pool, every message gets a ``☐``/``☑`` toggle row, so the
    operator picks on a phone by tapping instead of typing ``send 1,3``. The action
    row then reads "Send N" once anything is ticked, and "Send all" while nothing
    is — so a single tap still does the common thing, which is what the text
    protocol was good at and what a pure multi-select would have taken away.

    Called with no arguments (or an oversized pool) it degrades to the original
    two-button Send all / Skip row.

    ``selected`` holds 1-based indices, matching the numbering shown to the user
    and accepted by :func:`parse_pool_choice`.
    """
    texts = texts or []
    selected = selected or set()
    rows: list[list[dict[str, Any]]] = []
    if pool_toggles_fit(len(texts)):
        for i, t in enumerate(texts, start=1):
            mark = "☑" if i in selected else "☐"
            rows.append(
                [
                    {
                        "text": f"{mark} {i}. {_truncate(t, POOL_BUTTON_TRUNC)}",
                        "callback_data": f"{CB_POOL_TOGGLE_PREFIX}{i}",
                    }
                ]
            )
    if selected:
        send = {"text": f"📤 Send {len(selected)}", "callback_data": CB_POOL_SEND}
    else:
        send = {"text": "📤 Send all", "callback_data": CB_POOL_ALL}
    rows.append([send, {"text": "🙈 Skip", "callback_data": CB_POOL_SKIP}])
    return {"inline_keyboard": rows}


def _truncate(text: str, limit: int = POOL_PREVIEW_TRUNC) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def render_pool_selection(texts: list[str], selected: set[int] | None = None) -> str:
    """The pool-selection prompt: numbered, truncated previews of the unforwarded
    messages, with the send/skip guidance (keyboard + numbered-text fallback).

    ``selected`` mirrors the keyboard's ticks in the text body, so the state is
    still legible where inline keyboards are not rendered (a desktop client with
    images off, a screen reader, a copy-pasted transcript).
    """
    selected = selected or set()
    lines = [f"📨 {len(texts)} pooled message(s) waiting:"]
    for i, t in enumerate(texts, start=1):
        mark = "☑ " if i in selected else ""
        lines.append(f"{mark}{i}. {_truncate(t)}")
    lines.append("")
    if pool_toggles_fit(len(texts)):
        lines.append(
            "Tap to tick the ones to send, then 📤 — or reply `send all`, "
            "`send 1,3`, or `skip`."
        )
    else:
        lines.append(
            "Tap 📤 Send all / 🙈 Skip — or reply `send all`, `send 1,3`, or `skip`."
        )
    return "\n".join(lines)


def parse_pool_choice(
    data: str | None, text: str, count: int
) -> "str | tuple[str, int] | list[int] | None":
    """Resolve a pool-selection input. Returns ``"all"``, ``"skip"``, ``"send"``,
    ``("toggle", i)``, a list of 1-based indices to send (deduped, in range,
    sorted), or ``None`` if it selects nothing (re-prompt).

    Callback: ``as:pool:all`` / ``as:pool:skip`` / ``as:pool:send`` /
    ``as:pool:t:<i>``. Text: ``send all`` / ``skip`` / ``send 1,3`` (also bare
    ``1,3``)."""
    if data:
        if data == CB_POOL_ALL:
            return "all"
        if data == CB_POOL_SKIP:
            return "skip"
        if data == CB_POOL_SEND:
            return "send"
        if data.startswith(CB_POOL_TOGGLE_PREFIX):
            raw = data[len(CB_POOL_TOGGLE_PREFIX):]
            # An out-of-range or non-numeric index is a stale keyboard from an
            # earlier, larger pool — ignore it rather than toggling something else.
            if raw.isdigit() and 1 <= int(raw) <= count:
                return ("toggle", int(raw))
        return None
    norm = (text or "").strip().lower()
    if not norm:
        return None
    if norm in ("skip", "none"):
        return "skip"
    if norm in ("send all", "all"):
        return "all"
    body = norm[len("send"):].strip() if norm.startswith("send") else norm
    picks: set[int] = set()
    for part in body.replace(" ", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            return None
        n = int(part)
        if 1 <= n <= count:
            picks.add(n)
    return sorted(picks) if picks else None


def build_offline_prompt(texts: list[str]) -> str:
    """Join selected pooled texts into claude's initial prompt (one string)."""
    return OFFLINE_PROMPT_PREFIX + "\n" + "\n".join(texts)


# --------------------------------------------------------------------------- #
# Resume-first recents screen (Step 2.2 pulled forward)
# --------------------------------------------------------------------------- #

# Callback data for the recents screen (namespaced, well under 64 bytes).
CB_NEW_SESSION = "as:new"
CB_RECENT_PREFIX = "as:r:"

# How many recents the resume screen offers (the store keeps up to 5).
RECENTS_SHOWN = 3


def humanize_age(started_at: str, now: "datetime | None" = None) -> str:
    """Coarse human age of an ISO-8601 ``Z`` timestamp: ``just now`` / ``12m`` /
    ``3h`` / ``5d``. Unparseable/empty → empty string."""
    from datetime import datetime as _dt
    from datetime import timezone as _tz

    try:
        then = _dt.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_tz.utc)
    except (ValueError, TypeError):
        return ""
    now = now or _dt.now(_tz.utc)
    secs = int((now - then).total_seconds())
    if secs < 0:
        secs = 0
    if secs < 60:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"


def _recent_button_label(entry: Any, now: "datetime | None" = None) -> str:
    age = humanize_age(getattr(entry, "started_at", ""), now=now)
    suffix = f" ({age})" if age else ""
    return f"▶ Resume {getattr(entry, 'label', '?')}{suffix}"


def build_recents_keyboard(
    recents: list[Any], now: "datetime | None" = None
) -> dict[str, Any]:
    """Inline keyboard: up to :data:`RECENTS_SHOWN` resume buttons + New session."""
    rows: list[list[dict[str, str]]] = []
    for i, entry in enumerate(recents[:RECENTS_SHOWN]):
        rows.append(
            [{"text": _recent_button_label(entry, now), "callback_data": f"{CB_RECENT_PREFIX}{i}"}]
        )
    rows.append([{"text": "🆕 New session", "callback_data": CB_NEW_SESSION}])
    return {"inline_keyboard": rows}


def render_recents_menu(recents: list[Any], now: "datetime | None" = None) -> str:
    """Numbered-text rendering of the resume screen (keyboard fallback)."""
    lines = ["🔁 Resume a recent session, or start fresh — tap a button or reply with a number:"]
    n = 0
    for entry in recents[:RECENTS_SHOWN]:
        n += 1
        age = humanize_age(getattr(entry, "started_at", ""), now=now)
        suffix = f" ({age})" if age else ""
        lines.append(f"{n}. ▶ Resume {getattr(entry, 'label', '?')}{suffix}")
    lines.append(f"{n + 1}. 🆕 New session")
    return "\n".join(lines)


def choose_recent(
    data: str | None, text: str, count: int
) -> "int | str | None":
    """Resolve a recents-screen input. Returns a recent index (0-based), the
    string ``"new"`` for New session, or ``None`` if it selects nothing.

    Numbered fallback: ``1..count`` pick recents, ``count+1`` is New session."""
    if data:
        if data == CB_NEW_SESSION:
            return "new"
        if data.startswith(CB_RECENT_PREFIX):
            idx = _parse_int(data[len(CB_RECENT_PREFIX):])
            if idx is not None and 0 <= idx < count:
                return idx
        return None
    n = _parse_int(text)
    if n is None:
        return None
    if 1 <= n <= count:
        return n - 1
    if n == count + 1:
        return "new"
    return None
