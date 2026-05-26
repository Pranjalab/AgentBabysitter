"""Cancellable countdown for the policy-driven wait bar.

Used by `BridgeUI` whenever a policy decision is going to auto-fire after
a configurable interval (per `policy.yml:profiles.<name>.wait_interval_seconds`).
Any keystroke the user types during the wait sets the override event, which
causes the wait to return early with ``overridden=True``.

The async function is intentionally pure (no I/O, no globals) so it's
unit-testable in isolation.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class WaitResult:
    overridden: bool
    elapsed: float


async def countdown_wait(
    interval_seconds: float,
    override_event: asyncio.Event,
) -> WaitResult:
    """Sleep `interval_seconds`, returning early if `override_event` fires.

    Returns a ``WaitResult(overridden, elapsed)``:
        - ``overridden=True`` means the event was set before the timer elapsed.
        - ``overridden=False`` means the full interval passed without interruption.
        - ``elapsed`` is wall-clock seconds waited (capped at ``interval_seconds``).

    A non-positive interval is treated as "no wait" and returns
    ``WaitResult(overridden=False, elapsed=0.0)`` immediately.
    """
    if interval_seconds <= 0:
        return WaitResult(overridden=False, elapsed=0.0)

    loop = asyncio.get_event_loop()
    start = loop.time()
    try:
        await asyncio.wait_for(override_event.wait(), timeout=interval_seconds)
        return WaitResult(overridden=True, elapsed=loop.time() - start)
    except asyncio.TimeoutError:
        return WaitResult(overridden=False, elapsed=interval_seconds)
