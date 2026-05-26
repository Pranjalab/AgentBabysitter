"""Discover and select a tmux pane to monitor.

Supports three discovery modes:
- Explicit:    pass `session:window.pane` via CLI.
- Auto-detect: scan all panes for one running `claude` (or `node`).
- Interactive: numbered picker over all available panes.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from dataclasses import dataclass


CLAUDE_COMMAND_HINTS = ("claude", "node")

# Some Claude Code builds report `pane_current_command` as a bare version
# string like `2.1.150` instead of the binary name. Match that shape too.
_VERSION_CMD_RE = re.compile(r"^\d+(?:\.\d+){1,3}$")

# Claude Code prefixes the pane title with "✳" (sometimes followed by the
# current task description). This is the most reliable signal we have.
_CLAUDE_TITLE_PREFIX = "✳"


class SessionPickerError(RuntimeError):
    pass


@dataclass(frozen=True)
class Pane:
    target: str               # "session:window.pane"
    current_command: str
    title: str = ""

    def __str__(self) -> str:
        return f"{self.target}  [{self.current_command}]  {self.title}".rstrip()


async def _run(cmd: list[str]) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise SessionPickerError(
            f"command failed ({' '.join(cmd)}): {stderr.decode().strip()}"
        )
    return stdout.decode()


def _run_sync(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SessionPickerError(
            f"command failed ({' '.join(cmd)}): {result.stderr.strip()}"
        )
    return result.stdout


def list_panes() -> list[Pane]:
    """Enumerate every pane in every tmux session on the host."""
    fmt = "#{session_name}:#{window_index}.#{pane_index}\t#{pane_current_command}\t#{pane_title}"
    output = _run_sync(["tmux", "list-panes", "-a", "-F", fmt])
    panes: list[Pane] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        target, cmd = parts[0], parts[1]
        title = parts[2] if len(parts) > 2 else ""
        panes.append(Pane(target=target, current_command=cmd, title=title))
    return panes


def _looks_like_claude(p: Pane) -> bool:
    cmd_low = p.current_command.lower()
    if any(h in cmd_low for h in CLAUDE_COMMAND_HINTS):
        return True
    if _VERSION_CMD_RE.match(p.current_command):
        # Bare version string — Claude Code reports itself this way.
        return True
    title = p.title or ""
    if _CLAUDE_TITLE_PREFIX in title:
        # Claude Code's title is always prefixed with ✳, even as the task
        # description changes mid-session.
        return True
    return "claude" in title.lower()


def find_claude_panes(panes: list[Pane] | None = None) -> list[Pane]:
    """Return every tmux pane that looks like a Claude Code session.

    Sorted so the most "literally claude" matches come first (a pane whose
    command literally contains 'claude' beats one matched only by title).
    """
    panes = panes if panes is not None else list_panes()
    candidates = [p for p in panes if _looks_like_claude(p)]
    candidates.sort(
        key=lambda p: ("claude" not in p.current_command.lower(), p.target)
    )
    return candidates


def _auto_detect(panes: list[Pane]) -> Pane | None:
    """Return the single most-likely Claude pane, or None when ambiguous.

    Returns:
        - The single matching pane when exactly one looks like Claude.
        - ``None`` when there are no candidates OR multiple candidates —
          callers should fall through to the interactive picker.
    """
    candidates = find_claude_panes(panes)
    if len(candidates) == 1:
        return candidates[0]
    return None


def _interactive(panes: list[Pane], header: str | None = None,
                  input_fn=None) -> Pane:
    """Numbered picker over `panes`. `header` overrides the default heading."""
    if not panes:
        raise SessionPickerError("no tmux panes available — start tmux first")
    # Resolve `input` dynamically so monkeypatches in tests apply.
    if input_fn is None:
        input_fn = input

    print(header or "\nAvailable tmux panes:")
    for i, pane in enumerate(panes, start=1):
        print(f"  [{i}] {pane}")

    while True:
        raw = input_fn("\nSelect pane number: ").strip()
        if not raw:
            continue
        try:
            idx = int(raw)
        except ValueError:
            print("  not a number, try again")
            continue
        if 1 <= idx <= len(panes):
            return panes[idx - 1]
        print(f"  out of range (1..{len(panes)})")


def pick_session(cli_arg: str | None = None, auto_detect: bool = False) -> str:
    """Resolve a tmux pane target string.

    Precedence:
        1. ``cli_arg`` if provided.
        2. ``auto_detect=True``:
           - 0 candidates → raise SessionPickerError with available panes
           - 1 candidate  → use it
           - 2+ candidates → narrow the interactive picker to the
             Claude-looking ones and ask the user to pick.
        3. Otherwise, full interactive picker over every tmux pane.
    """
    if cli_arg:
        return cli_arg

    panes = list_panes()

    if auto_detect:
        candidates = find_claude_panes(panes)
        if not candidates:
            available = ", ".join(
                f"{p.target} (cmd={p.current_command}, title={p.title or '-'})"
                for p in panes
            ) or "<none>"
            raise SessionPickerError(
                "auto-detect found no pane running Claude Code.\n"
                f"  Available panes: {available}\n"
                "  Hint: start `claude` inside a tmux pane, then retry — "
                "or run `--list-panes` and pass `--session <target>` directly."
            )
        if len(candidates) == 1:
            return candidates[0].target
        # Ambiguous — fall through to the picker scoped to Claude panes.
        return _interactive(
            candidates,
            header=(
                f"\nFound {len(candidates)} Claude panes — pick one "
                f"(or pass `--session <target>` to skip this next time):"
            ),
        ).target

    return _interactive(panes).target
