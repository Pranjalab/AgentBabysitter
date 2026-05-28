"""Cancellable countdown for the policy-driven wait bar.

Used by ``BridgeUI`` whenever a policy decision is going to auto-fire
after a configurable interval (per
``policy.yml:profiles.<name>.wait_interval_seconds``). Any keystroke the
user types during the wait sets the override event, which causes the
wait to return early with ``overridden=True``.

The async functions are intentionally pure (no I/O, no globals) so they
unit-test in isolation. ``countdown_wait`` is the minimal building block;
``animated_countdown_wait`` adds an optional periodic tick callback so
callers can paint a progress line / log heartbeat without touching the
core race logic.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass(frozen=True)
class WaitResult:
    overridden: bool
    elapsed: float


TickFn = Callable[[float, float], None | Awaitable[None]]


async def countdown_wait(
    interval_seconds: float,
    override_event: asyncio.Event,
) -> WaitResult:
    """Sleep ``interval_seconds``, returning early if ``override_event`` fires.

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


async def animated_countdown_wait(
    interval_seconds: float,
    override_event: asyncio.Event,
    on_tick: TickFn | None = None,
    tick_interval: float = 0.5,
) -> WaitResult:
    """Like ``countdown_wait`` but calls ``on_tick(remaining, total)`` periodically.

    The tick callback runs at most every ``tick_interval`` seconds. It may
    be sync or async (we ``await`` whatever it returns). Exceptions raised
    by the callback are swallowed so a misbehaving renderer can't kill the
    wait logic.

    For zero / negative ``interval_seconds`` this returns immediately
    without firing the callback (consistent with ``countdown_wait``).
    """
    if interval_seconds <= 0:
        return WaitResult(overridden=False, elapsed=0.0)

    loop = asyncio.get_event_loop()
    start = loop.time()
    end = start + interval_seconds

    while True:
        now = loop.time()
        remaining = end - now
        if remaining <= 0:
            return WaitResult(overridden=False, elapsed=interval_seconds)

        if on_tick is not None:
            try:
                ret = on_tick(remaining, interval_seconds)
                if asyncio.iscoroutine(ret):
                    await ret
            except Exception:  # noqa: BLE001 — never break the wait on a bad renderer
                pass

        chunk = min(tick_interval, remaining)
        try:
            await asyncio.wait_for(override_event.wait(), timeout=chunk)
            return WaitResult(overridden=True, elapsed=loop.time() - start)
        except asyncio.TimeoutError:
            continue
