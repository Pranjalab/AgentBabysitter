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


def _auto_detect(panes: list[Pane]) -> Pane | None:
    """Return the most likely Claude Code pane, or None."""
    candidates = [p for p in panes if _looks_like_claude(p)]
    if not candidates:
        return None
    # Prefer a pane whose command literally contains "claude" over a generic node.
    candidates.sort(key=lambda p: ("claude" not in p.current_command.lower(), p.target))
    return candidates[0]


def _interactive(panes: list[Pane]) -> Pane:
    if not panes:
        raise SessionPickerError("no tmux panes available — start tmux first")

    print("\nAvailable tmux panes:")
    for i, pane in enumerate(panes, start=1):
        print(f"  [{i}] {pane}")

    while True:
        raw = input("\nSelect pane number: ").strip()
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
        1. `cli_arg` if provided.
        2. `auto_detect=True` scans panes for the Claude Code process.
        3. Otherwise, prompt the user interactively.
    """
    if cli_arg:
        return cli_arg

    panes = list_panes()

    if auto_detect:
        found = _auto_detect(panes)
        if found is None:
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
        return found.target

    return _interactive(panes).target
