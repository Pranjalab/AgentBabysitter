"""Plain-text interaction log for one cldx run.

Complements ``cldx.session_store`` (which writes a machine-replayable
JSONL). The interaction log is what you ``cat`` or ``tail -f`` when you
want to *read* what happened in a session — every keystroke the user
typed, every message Telegram delivered, every decision cldx made,
and every chunk of pane output Claude produced.

Layout::

    ~/.cldx/logs/
        2026-05-27/
            14-32-15_auto-approve_0-0.0.log

Filename parts: ``HH-MM-SS_<profile>_<pane>``. The date directory is
created lazily on first write; the file is opened lazily on the first
event so constructing an InteractionLog never touches disk.

Each line uses a fixed-width prefix so grep / column-mode editors can
filter cleanly::

    [2026-05-27T14:32:15Z] terminal-in   y
    [2026-05-27T14:32:15Z] cldx-decision Auto-approved Bash(ls)
    [2026-05-27T14:32:15Z] cldx-action   sent 'y'
    [2026-05-27T14:32:30Z] telegram-out  approval needed: Bash(rm -rf /tmp)
    [2026-05-27T14:32:42Z] telegram-in   y
    [2026-05-27T14:33:01Z] claude-out    ⏺ Bash(rm -rf /tmp) → Done.

Multi-line messages are indented by two spaces on continuation lines
so the prefix column stays clean.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cldx._paths import cldx_home


_SAFE = re.compile(r"[^0-9A-Za-z_\-]")

# Channels recognised by ``log()``. Kept open-set in code — this tuple
# is just the canonical list for tests and documentation.
CHANNELS = ("terminal", "telegram", "cldx", "claude")


def _now_iso_z() -> str:
    """ISO-8601 UTC with trailing ``Z`` — matches what most log readers expect."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe(value: str) -> str:
    """Make ``value`` safe for use in a filename segment."""
    return _SAFE.sub("_", value) or "_"


def logs_root() -> Path:
    """Root directory for all interaction logs. Created lazily."""
    root = cldx_home() / "logs"
    root.mkdir(parents=True, exist_ok=True)
    return root


class InteractionLog:
    """One-file plain-text log of a single cldx run.

    Parameters
    ----------
    profile:
        Active policy profile (``auto-approve``, ``yolo``, …) — embedded
        in the filename so the same date directory holds multiple sessions
        cleanly.
    pane:
        tmux pane target string (``0:0.0``) — also embedded in the
        filename. ``None`` for off-pane invocations.
    root:
        Override the logs root. Tests use a temp dir; the default is
        ``~/.cldx/logs``.
    """

    # Width of the ``channel-direction`` column. Picked so the longest
    # canonical tag (``cldx-decision``, 13 chars) still has one trailing
    # space before the message starts.
    _TAG_WIDTH = 14

    def __init__(
        self,
        profile: str = "default",
        pane: Optional[str] = None,
        root: Optional[Path] = None,
    ) -> None:
        self.profile = _safe(profile)
        self.pane = _safe(pane) if pane else None
        self._root = root or logs_root()
        self._path: Optional[Path] = None
        self._fh = None
        self._event_count = 0

    # --- path / file management ---

    @property
    def path(self) -> Path:
        """Where this log will be written (created on first event)."""
        if self._path is None:
            now = datetime.now(timezone.utc)
            date_dir = self._root / now.strftime("%Y-%m-%d")
            time_part = now.strftime("%H-%M-%S")
            suffix = f"{time_part}_{self.profile}"
            if self.pane:
                suffix += f"_{self.pane}"
            self._path = date_dir / f"{suffix}.log"
            self._path.parent.mkdir(parents=True, exist_ok=True)
        return self._path

    @property
    def event_count(self) -> int:
        return self._event_count

    def _ensure_open(self) -> None:
        if self._fh is None:
            # Line-buffered so ``tail -f`` works without polling and so
            # crash-mid-run still leaves a useful trailing partial line.
            self._fh = open(self.path, "a", buffering=1, encoding="utf-8")
            header = (
                f"# cldx session log — profile={self.profile} "
                f"pane={self.pane or '-'} started={_now_iso_z()}\n"
            )
            self._fh.write(header)

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.write(f"# session ended {_now_iso_z()} — "
                               f"{self._event_count} events\n")
                self._fh.close()
            finally:
                self._fh = None

    def __enter__(self) -> "InteractionLog":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- core write ---

    def log(self, channel: str, direction: str, message: str) -> None:
        """Append one line.

        ``channel`` is one of ``CHANNELS`` (e.g. ``"telegram"``);
        ``direction`` is ``"in"`` / ``"out"`` / a verb like ``"decision"`` /
        ``"action"`` for cldx-originated events.

        Newlines in ``message`` are preserved but continuation lines are
        indented two spaces so the prefix column stays scannable.
        """
        self._ensure_open()
        tag = f"{channel}-{direction}"
        # Pad/truncate so columns line up.
        padded = tag.ljust(self._TAG_WIDTH)[: self._TAG_WIDTH]
        prefix = f"[{_now_iso_z()}] {padded} "
        text = message or ""
        # Strip trailing newline from the message itself so we control the
        # one we emit.
        text = text.rstrip("\n")
        lines = text.split("\n") if text else [""]
        out = [prefix + lines[0]]
        # Continuation lines indent under the message column.
        cont_indent = " " * len(prefix)
        for extra in lines[1:]:
            out.append(cont_indent + extra)
        self._fh.write("\n".join(out) + "\n")  # type: ignore[union-attr]
        self._event_count += 1

    # --- convenience helpers ---
    #
    # These pin the (channel, direction) pair so calling sites don't
    # have to remember the canonical strings. The same data also goes
    # to ``SessionStore`` as JSON — the InteractionLog is the
    # human-readable mirror.

    def terminal_in(self, message: str) -> None:
        """User typed something in the cldx terminal."""
        self.log("terminal", "in", message)

    def terminal_out(self, message: str) -> None:
        """cldx wrote something to the user's terminal (panels, prompts)."""
        self.log("terminal", "out", message)

    def telegram_in(self, message: str) -> None:
        """User sent a Telegram message to the bot."""
        self.log("telegram", "in", message)

    def telegram_out(self, message: str) -> None:
        """cldx pushed a message to the Telegram chat."""
        self.log("telegram", "out", message)

    def cldx_decision(self, message: str) -> None:
        """A policy/engine decision was made."""
        self.log("cldx", "decision", message)

    def cldx_action(self, message: str) -> None:
        """cldx sent keys/text into the tmux pane."""
        self.log("cldx", "action", message)

    def cldx_note(self, message: str) -> None:
        """Free-form note (warnings, lifecycle, errors)."""
        self.log("cldx", "note", message)

    def claude_out(self, message: str) -> None:
        """A chunk of Claude's pane output was captured."""
        self.log("claude", "out", message)
