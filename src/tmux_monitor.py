"""Poll a tmux pane and emit callbacks when its visible content changes."""

from __future__ import annotations

import asyncio
import re
from typing import Awaitable, Callable


# Strips ANSI escape codes — CSI, OSC, and a few stray single-byte escapes.
_ANSI_RE = re.compile(
    r"""
    \x1B
    (?: \[ [0-?]* [ -/]* [@-~]                # CSI sequence
      | \] [^\x07\x1B]* (?: \x07 | \x1B\\ )   # OSC sequence
      | [PX^_] [^\x1B]*? \x1B\\               # DCS/PM/APC/SOS
      | [@A-Z\\\-_]                           # single-byte escape (no `]`)
    )
    """,
    re.VERBOSE | re.DOTALL,
)


class TmuxMonitorError(RuntimeError):
    pass


ChangeCallback = Callable[[str, str], Awaitable[None]]
StableCallback = Callable[[str], Awaitable[None]]


class TmuxMonitor:
    """Async watcher for a single tmux pane.

    `watch()` polls every `poll_interval` seconds and, on change, invokes
    the callback with `(new_content, full_snapshot)`, both ANSI-stripped.
    """

    def __init__(
        self,
        pane: str,
        poll_interval: float = 1.0,
        capture_lines: int = 200,
        stable_polls: int = 2,
    ):
        self.pane = pane
        self.poll_interval = poll_interval
        self.capture_lines = capture_lines
        self.stable_polls = stable_polls
        self.last_snapshot: str = ""
        self._stable_count: int = 0
        self._stopped = asyncio.Event()

    async def capture(self) -> str:
        """Run `tmux capture-pane -p -t <pane> -S -<N>` and return the text."""
        proc = await asyncio.create_subprocess_exec(
            "tmux",
            "capture-pane",
            "-p",
            "-t",
            self.pane,
            "-S",
            f"-{self.capture_lines}",
            "-e",  # include escape sequences so we can strip consistently
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise TmuxMonitorError(
                f"tmux capture-pane failed: {stderr.decode().strip()}"
            )
        return stdout.decode(errors="replace")

    @staticmethod
    def strip_ansi(text: str) -> str:
        return _ANSI_RE.sub("", text)

    @staticmethod
    def diff_tail(old: str, new: str) -> str:
        """Return only the lines that are new at the tail of `new`."""
        if not old:
            return new
        if new.startswith(old):
            return new[len(old):]
        # Pane scrolled or rewrote in place — fall back to the last few lines.
        return "\n".join(new.splitlines()[-10:])

    def stop(self) -> None:
        self._stopped.set()

    @property
    def is_stable(self) -> bool:
        return self._stable_count >= self.stable_polls

    async def watch(
        self,
        on_change: ChangeCallback | None = None,
        on_stable: StableCallback | None = None,
    ) -> None:
        """Poll forever (or until `stop()`).

        - `on_change(diff, snapshot)` fires on every detected diff.
        - `on_stable(snapshot)` fires once each time the pane goes quiet
          (no change for `stable_polls` cycles in a row).
        """
        stable_fired = False
        while not self._stopped.is_set():
            raw = await self.capture()
            snapshot = self.strip_ansi(raw)

            if snapshot != self.last_snapshot:
                new_content = self.diff_tail(self.last_snapshot, snapshot)
                self.last_snapshot = snapshot
                self._stable_count = 0
                stable_fired = False
                if on_change is not None:
                    await on_change(new_content, snapshot)
            else:
                self._stable_count += 1
                if (
                    not stable_fired
                    and on_stable is not None
                    and self._stable_count >= self.stable_polls
                ):
                    stable_fired = True
                    await on_stable(snapshot)

            try:
                await asyncio.wait_for(
                    self._stopped.wait(), timeout=self.poll_interval
                )
            except asyncio.TimeoutError:
                continue
