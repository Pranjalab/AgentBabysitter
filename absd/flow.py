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

# Permission modes (D5). "normal" = default prompts; "away" = acceptEdits.
MODE_NORMAL = "normal"
MODE_AWAY = "away"


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
) -> list[ProjectOption]:
    """Build the ordered project-option list for the keyboard.

    ``registered`` is ``[(path, label), …]`` from the registry (already resolved).
    Order (PLAN.md Step 1.5): registered projects first (in registry order,
    existing directories only), then the direct children of ``workspace_root``
    not already registered, then a single "➕ New folder" sentinel (only when a
    workspace root is configured — without one there is nowhere jail-safe to
    create a folder, D6).
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

    return options


def build_project_keyboard(options: list[ProjectOption]) -> dict[str, Any]:
    """Inline keyboard for the project step — one button per option, one per row
    (project labels can be long). Callback data per the module grammar."""
    rows: list[list[dict[str, str]]] = []
    for i, opt in enumerate(options):
        data = CB_NEWFOLDER if opt.kind == "newfolder" else f"{CB_PROJECT_PREFIX}{i}"
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
        if data.startswith(CB_PROJECT_PREFIX):
            idx = _parse_int(data[len(CB_PROJECT_PREFIX):])
            if idx is not None and 0 <= idx < len(options):
                return options[idx]
        return None
    n = _parse_int(text)
    if n is not None and 1 <= n <= len(options):
        return options[n - 1]
    return None


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


def build_launcher_argv(script_path: str, profile: str, away: bool) -> list[str]:
    """The exact launcher the engine runs (PLAN.md 4.2): reuse ``abs.sh`` via
    ``bash <SCRIPT_PATH> --profile <p> --daemon-start [--away]`` — never a Python
    reimplementation of the launcher."""
    argv = ["bash", script_path, "--profile", profile, "--daemon-start"]
    if away:
        argv.append("--away")
    return argv
