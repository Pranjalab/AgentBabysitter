"""Phase 3 — wait bar countdown with keystroke override."""

from __future__ import annotations

import asyncio

import pytest

from cldx.wait_bar import WaitResult, countdown_wait


async def test_wait_bar_auto_approves_after_interval():
    """If the event is never set, the wait completes after `interval`."""
    event = asyncio.Event()
    result = await countdown_wait(0.05, event)
    assert result.overridden is False
    assert result.elapsed == pytest.approx(0.05, abs=0.05)


async def test_wait_bar_keystroke_cancels_auto_action():
    """If the event fires during the wait, override=True and we return early."""
    event = asyncio.Event()

    async def fire_event():
        await asyncio.sleep(0.02)
        event.set()

    asyncio.create_task(fire_event())
    result = await countdown_wait(1.0, event)
    assert result.overridden is True
    assert result.elapsed < 0.5  # came back well before timeout


async def test_wait_bar_zero_means_instant_auto():
    """`wait_interval_seconds: 0` skips the wait entirely."""
    event = asyncio.Event()
    result = await countdown_wait(0.0, event)
    assert result.overridden is False
    assert result.elapsed == 0.0


async def test_wait_bar_negative_interval_also_skips():
    """Negative interval is treated like zero."""
    event = asyncio.Event()
    result = await countdown_wait(-1.0, event)
    assert result.overridden is False
    assert result.elapsed == 0.0


async def test_wait_bar_respects_per_profile_interval():
    """Different intervals produce different elapsed values."""
    e1 = asyncio.Event()
    e2 = asyncio.Event()
    short = await countdown_wait(0.02, e1)
    long = await countdown_wait(0.10, e2)
    assert short.elapsed < long.elapsed


async def test_pre_set_event_returns_immediately():
    """If the event is already set when called, override fires at once."""
    event = asyncio.Event()
    event.set()
    result = await countdown_wait(5.0, event)
    assert result.overridden is True
    assert result.elapsed < 0.1


async def test_waitresult_is_immutable():
    """WaitResult should be frozen so callers can't mutate it post-return."""
    result = WaitResult(overridden=True, elapsed=1.0)
    with pytest.raises((AttributeError, Exception)):
        result.overridden = False  # type: ignore[misc]
