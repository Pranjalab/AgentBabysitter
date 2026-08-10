"""Step 2.1 — the blocked/done notification decision (``absd.agentstatus``).

Everything here is pure: the watcher is fed ``(status, now)`` and asked what to
send. Time is a plain number, so a twenty-second debounce costs no wall clock.

The rules being pinned are the ones that decide whether the operator gets a
useful ping or a notification loop:
  - once per blocked EPISODE, never per sample;
  - a positive non-blocked status ends the episode and re-arms;
  - ``unknown`` is "no information" and must neither start nor end an episode —
    herdr reports it during redraws, and letting one bad sample cancel a real
    pending block is exactly how this feature would go silent when it matters.
"""

from __future__ import annotations

import pytest

from absd.agentstatus import (
    AGENT_BLOCKED,
    AGENT_DONE,
    AGENT_IDLE,
    AGENT_UNKNOWN,
    AGENT_WORKING,
    NOTICE_BLOCKED,
    NOTICE_DONE,
    Notice,
    StatusWatcher,
    normalize,
)


# ---- normalize ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [AGENT_IDLE, AGENT_WORKING, AGENT_BLOCKED, AGENT_DONE, AGENT_UNKNOWN],
)
def test_normalize_passes_known_statuses_through(raw: str) -> None:
    assert normalize(raw) == raw


@pytest.mark.parametrize("raw", [None, "", "BLOCKED", "waiting", 7, object(), b"blocked"])
def test_normalize_maps_everything_else_to_unknown(raw: object) -> None:
    """A herdr that grows a new status, or a probe that failed, must never be read
    as a positive statement — least of all as "not blocked"."""
    assert normalize(raw) == AGENT_UNKNOWN


# ---- the blocked debounce ----------------------------------------------------


def test_blocked_fires_once_after_the_debounce() -> None:
    w = StatusWatcher(debounce_s=20.0)
    assert w.feed(AGENT_BLOCKED, 100.0) is None  # episode starts
    assert w.feed(AGENT_BLOCKED, 115.0) is None  # 15s — not yet
    notice = w.feed(AGENT_BLOCKED, 120.0)  # exactly 20s — fires
    assert notice == Notice(kind=NOTICE_BLOCKED, status=AGENT_BLOCKED, blocked_for_s=20.0)


def test_blocked_never_repeats_within_one_episode() -> None:
    """The operator was told. Repeating it every 3s is a notification loop."""
    w = StatusWatcher(debounce_s=10.0)
    w.feed(AGENT_BLOCKED, 0.0)
    assert w.feed(AGENT_BLOCKED, 10.0) is not None
    for t in (13.0, 16.0, 60.0, 3600.0):
        assert w.feed(AGENT_BLOCKED, t) is None


def test_a_short_block_answered_at_the_desk_never_pings() -> None:
    w = StatusWatcher(debounce_s=20.0)
    w.feed(AGENT_BLOCKED, 0.0)
    w.feed(AGENT_BLOCKED, 5.0)
    assert w.feed(AGENT_WORKING, 8.0) is None  # answered before the debounce
    assert not w.blocked_pending


def test_a_second_block_is_a_new_episode() -> None:
    w = StatusWatcher(debounce_s=10.0)
    w.feed(AGENT_BLOCKED, 0.0)
    assert w.feed(AGENT_BLOCKED, 10.0) is not None
    w.feed(AGENT_WORKING, 12.0)  # episode over
    w.feed(AGENT_BLOCKED, 20.0)  # new one
    assert w.feed(AGENT_BLOCKED, 30.0) is not None


@pytest.mark.parametrize("ending", [AGENT_IDLE, AGENT_WORKING, AGENT_DONE])
def test_any_positive_status_ends_an_episode(ending: str) -> None:
    w = StatusWatcher(debounce_s=20.0)
    w.feed(AGENT_BLOCKED, 0.0)
    w.feed(ending, 5.0)
    assert not w.blocked_pending
    assert w.feed(AGENT_BLOCKED, 6.0) is None  # timer restarted from 6.0
    assert w.feed(AGENT_BLOCKED, 25.0) is None  # only 19s in
    assert w.feed(AGENT_BLOCKED, 26.0) is not None


def test_unknown_holds_an_episode_open() -> None:
    """The failure this guards: herdr blinks to ``unknown`` on one sample mid-block,
    the episode resets, and the operator is never told about a block that is still
    sitting there. ``unknown`` decides nothing."""
    w = StatusWatcher(debounce_s=20.0)
    w.feed(AGENT_BLOCKED, 0.0)
    assert w.feed(AGENT_UNKNOWN, 5.0) is None
    assert w.feed(AGENT_UNKNOWN, 10.0) is None
    assert w.blocked_pending
    assert w.feed(AGENT_BLOCKED, 20.0) is not None  # timed from the ORIGINAL 0.0


def test_unknown_alone_never_starts_an_episode() -> None:
    w = StatusWatcher(debounce_s=0.0)
    for t in range(10):
        assert w.feed(AGENT_UNKNOWN, float(t)) is None
    assert not w.blocked_pending


def test_probe_failure_reads_as_unknown() -> None:
    """The engine returns None when it cannot tell; that must behave as unknown."""
    w = StatusWatcher(debounce_s=10.0)
    w.feed(AGENT_BLOCKED, 0.0)
    assert w.feed(None, 5.0) is None
    assert w.blocked_pending
    assert w.feed(AGENT_BLOCKED, 10.0) is not None


def test_zero_debounce_fires_on_the_first_blocked_sample() -> None:
    w = StatusWatcher(debounce_s=0.0)
    assert w.feed(AGENT_BLOCKED, 42.0) is not None


def test_negative_debounce_is_clamped_not_trusted() -> None:
    w = StatusWatcher(debounce_s=-5.0)
    assert w.debounce_s == 0.0
    assert w.feed(AGENT_BLOCKED, 1.0) is not None


def test_blocked_notify_off_silences_blocked_only() -> None:
    w = StatusWatcher(debounce_s=0.0, notify_blocked=False, notify_done=True)
    assert w.feed(AGENT_BLOCKED, 0.0) is None
    assert w.feed(AGENT_BLOCKED, 100.0) is None
    assert w.feed(AGENT_DONE, 101.0) is not None  # done still works


# ---- done --------------------------------------------------------------------


def test_done_is_off_by_default() -> None:
    w = StatusWatcher()
    assert w.feed(AGENT_DONE, 0.0) is None


def test_done_fires_once_per_transition_into_done() -> None:
    w = StatusWatcher(notify_done=True)
    assert w.feed(AGENT_DONE, 0.0) == Notice(kind=NOTICE_DONE, status=AGENT_DONE)
    assert w.feed(AGENT_DONE, 1.0) is None  # still done — not a new transition
    w.feed(AGENT_WORKING, 2.0)
    assert w.feed(AGENT_DONE, 3.0) is not None  # worked again, finished again


def test_unknown_between_dones_does_not_re_fire() -> None:
    """``unknown`` must not look like "left done", or a redraw becomes a ping."""
    w = StatusWatcher(notify_done=True)
    assert w.feed(AGENT_DONE, 0.0) is not None
    w.feed(AGENT_UNKNOWN, 1.0)
    assert w.feed(AGENT_DONE, 2.0) is None


def test_done_while_blocked_pending_ends_the_block_and_reports_done() -> None:
    w = StatusWatcher(debounce_s=20.0, notify_done=True)
    w.feed(AGENT_BLOCKED, 0.0)
    notice = w.feed(AGENT_DONE, 5.0)
    assert notice is not None and notice.kind == NOTICE_DONE
    assert not w.blocked_pending


# ---- bookkeeping / reuse -----------------------------------------------------


def test_blocked_for_reports_the_observed_duration() -> None:
    w = StatusWatcher(debounce_s=20.0)
    assert w.blocked_for(0.0) == 0.0
    w.feed(AGENT_BLOCKED, 10.0)
    assert w.blocked_for(37.0) == 27.0
    w.feed(AGENT_IDLE, 38.0)
    assert w.blocked_for(50.0) == 0.0


def test_reset_clears_a_pending_episode() -> None:
    """Called at every session end — a block from a dead session must not ping the
    chat about the session that replaced it."""
    w = StatusWatcher(debounce_s=20.0)
    w.feed(AGENT_BLOCKED, 0.0)
    assert w.blocked_pending
    w.reset()
    assert not w.blocked_pending
    assert w.last_status == AGENT_UNKNOWN
    assert w.feed(AGENT_BLOCKED, 10.0) is None  # timer restarted at 10.0
    assert w.feed(AGENT_BLOCKED, 29.0) is None
    assert w.feed(AGENT_BLOCKED, 30.0) is not None


def test_reset_also_clears_the_notified_flag() -> None:
    w = StatusWatcher(debounce_s=0.0)
    assert w.feed(AGENT_BLOCKED, 0.0) is not None
    w.reset()
    assert w.feed(AGENT_BLOCKED, 1.0) is not None  # a new session may ping again


def test_last_status_ignores_unknown() -> None:
    w = StatusWatcher()
    w.feed(AGENT_WORKING, 0.0)
    w.feed(AGENT_UNKNOWN, 1.0)
    assert w.last_status == AGENT_WORKING


def test_clock_going_backwards_never_produces_a_negative_duration() -> None:
    """Defensive: the daemon's clock is monotonic, but the watcher is a value type
    that outlives assumptions about its caller."""
    w = StatusWatcher(debounce_s=0.0)
    notice = w.feed(AGENT_BLOCKED, 100.0)
    assert notice is not None and notice.blocked_for_s >= 0.0
    w.reset()
    w.feed(AGENT_BLOCKED, 100.0)
    assert w.blocked_for(50.0) == 0.0
