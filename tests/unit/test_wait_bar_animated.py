"""animated_countdown_wait: tick callbacks + override + degrade-on-error."""

from __future__ import annotations

import asyncio

import pytest

from cldx.wait_bar import WaitResult, animated_countdown_wait


async def test_animated_wait_fires_ticks_until_timer_expires():
    event = asyncio.Event()
    ticks = []

    def on_tick(remaining, total):
        ticks.append((remaining, total))

    result = await animated_countdown_wait(
        0.3, event, on_tick=on_tick, tick_interval=0.05,
    )
    assert result.overridden is False
    assert len(ticks) >= 3  # several ticks fit in 0.3s
    # Remaining values must be monotonically decreasing.
    remainings = [r for r, _ in ticks]
    assert all(a >= b for a, b in zip(remainings, remainings[1:]))


async def test_animated_wait_override_returns_early_with_overridden_true():
    event = asyncio.Event()
    ticks = []

    def on_tick(remaining, total):
        ticks.append(remaining)
        if len(ticks) == 2:
            event.set()

    result = await animated_countdown_wait(
        2.0, event, on_tick=on_tick, tick_interval=0.05,
    )
    assert result.overridden is True
    assert result.elapsed < 0.5


async def test_animated_wait_zero_interval_short_circuits():
    event = asyncio.Event()
    called = []
    result = await animated_countdown_wait(
        0.0, event, on_tick=lambda r, t: called.append(r),
    )
    assert result.overridden is False
    assert result.elapsed == 0.0
    assert called == []  # no tick for zero interval


async def test_animated_wait_swallows_callback_exceptions():
    event = asyncio.Event()

    def bad_tick(remaining, total):
        raise RuntimeError("renderer exploded")

    # Must not propagate; timer still completes.
    result = await animated_countdown_wait(
        0.1, event, on_tick=bad_tick, tick_interval=0.03,
    )
    assert result.overridden is False


async def test_animated_wait_accepts_async_callback():
    event = asyncio.Event()
    seen = []

    async def async_tick(remaining, total):
        await asyncio.sleep(0)
        seen.append(remaining)

    result = await animated_countdown_wait(
        0.15, event, on_tick=async_tick, tick_interval=0.05,
    )
    assert result.overridden is False
    assert len(seen) >= 2


async def test_animated_wait_no_callback_is_fine():
    """Callers should be able to omit the tick callback entirely."""
    event = asyncio.Event()
    result = await animated_countdown_wait(0.05, event)
    assert result.overridden is False
