"""Send keys/text to a tmux pane."""

from __future__ import annotations

import asyncio


class TmuxControllerError(RuntimeError):
    pass


class TmuxController:
    """Thin wrapper around `tmux send-keys -t <pane>`."""

    def __init__(self, pane: str):
        self.pane = pane

    async def _send_keys(self, *keys: str, literal: bool = False) -> None:
        cmd = ["tmux", "send-keys"]
        if literal:
            cmd.append("-l")
        cmd += ["-t", self.pane, *keys]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise TmuxControllerError(
                f"tmux send-keys failed: {stderr.decode().strip()}"
            )

    # --- Approval helpers ---------------------------------------------------

    async def send_yes(self) -> None:
        await self._send_keys("y", "Enter")

    async def send_no(self) -> None:
        await self._send_keys("n", "Enter")

    async def send_enter(self) -> None:
        await self._send_keys("Enter")

    # --- Text + raw key helpers --------------------------------------------

    async def send_text(self, text: str, submit: bool = True) -> None:
        """Type free-form text. Uses `-l` so special characters don't bind."""
        if text:
            await self._send_keys(text, literal=True)
        if submit:
            await self._send_keys("Enter")

    async def send_raw_keys(self, keys: str) -> None:
        """Send named keys (e.g. 'C-c', 'Escape', 'Up')."""
        await self._send_keys(keys)

    async def send_escape(self) -> None:
        await self._send_keys("Escape")

    async def send_digit(self, digit: int) -> None:
        """Press a single digit key (used to pick from a numbered menu)."""
        if not 0 <= digit <= 9:
            raise ValueError(f"digit out of range: {digit}")
        await self._send_keys(str(digit))

    async def send_arrow_select(self, option_index: int, direction: str = "Down") -> None:
        """Arrow-key menu selection: press `direction` N times, then Enter.

        `direction` must be 'Up' or 'Down'. `option_index` is 0-based and counts
        how many times to press the arrow from the currently-highlighted entry.
        """
        if direction not in ("Up", "Down"):
            raise ValueError("direction must be 'Up' or 'Down'")
        for _ in range(max(0, option_index)):
            await self._send_keys(direction)
        await self._send_keys("Enter")
