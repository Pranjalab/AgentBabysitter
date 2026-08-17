"""Step 2.1 + 2.3 in the daemon: blocked pings, and the stale-handoff sweep.

The decision logic is pinned in ``test_agentstatus.py``; this pins the *wiring* —
that the poller samples the right pane, only while the session is really alive,
sends to the chat that started it, and cannot fire on a backend that has no
agent-status capability at all (tmux: the feature is absent, not broken).

Fakes only: an engine with a settable agent status, FakeTelegram, a temp ABS_HOME
and an injected clock (PLAN.md §10). No herdr, no network, no claude.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from absd.daemon import (
    BLOCKED_MSG,
    DONE_MSG,
    STATE_IDLE,
    STATE_SESSION_LIVE,
    HandoffRequest,
    Poller,
)
from absd.events import (
    EVENT_SESSION_BLOCKED,
    EVENT_SESSION_DONE,
    EVENT_STALE_HANDOFF,
    EventLog,
    iter_events,
)
from tests.conftest import write_profile
from tests.harness.fake_telegram import FakeTelegram
from tests.test_flow_e2e import FakeEngine, make_poller


def _events(abs_home: Path) -> EventLog:
    return EventLog(abs_home / "daemon" / "events.jsonl")


def _records(abs_home: Path) -> list[dict]:
    return list(iter_events(abs_home / "daemon" / "events.jsonl"))


class StatusEngine(FakeEngine):
    """A FakeEngine that also answers ``agent_status`` — i.e. a herdr-class backend.

    Records the exact ``(profile, pane_id)`` it was asked about so a test can prove
    the poller reads OUR recorded pane, not "the first pane".
    """

    def __init__(self, status: str | None = "working") -> None:
        super().__init__()
        self.status = status
        self.asked: list[tuple[str, str | None]] = []
        self.raise_on_probe: Exception | None = None

    def agent_status(self, profile: str, pane_id: str | None = None) -> str | None:
        self.asked.append((profile, pane_id))
        if self.raise_on_probe is not None:
            raise self.raise_on_probe
        return self.status


def _clock_at(t: list[float]):
    return lambda: t[0]


async def _go_live(
    abs_home: Path,
    client_factory,
    engine,
    tmp_path: Path,
    now: list[float],
    **cfg_kw,
) -> Poller:
    """Drive a poller to SESSION_LIVE via a real handoff, so every field the
    notification path reads (chat id, pane id, label) is set the way production
    sets it — not hand-poked."""
    proj = tmp_path / "web"
    proj.mkdir(exist_ok=True)
    poller = make_poller(
        abs_home, client_factory, engine=engine, clock=_clock_at(now), **cfg_kw
    )
    poller._handoff_request = HandoffRequest(
        chat_id=42, project_path=str(proj), label="web", away=False
    )
    await poller._do_handoff()
    assert poller.session_state == STATE_SESSION_LIVE
    return poller


# ---- the blocked ping --------------------------------------------------------


async def test_blocked_ping_after_the_debounce(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    engine = StatusEngine(status="blocked")
    now = [1000.0]
    poller = await _go_live(
        abs_home, client_factory, engine, tmp_path, now, blocked_debounce_s=20.0
    )
    before = len(fake.sent_messages)

    assert await poller.watch_once() is True  # episode starts at 1000
    now[0] = 1015.0
    assert await poller.watch_once() is True
    assert len(fake.sent_messages) == before  # 15s — still quiet

    now[0] = 1021.0
    assert await poller.watch_once() is True
    sent = fake.sent_messages[-1]["text"]
    assert sent == BLOCKED_MSG.format(label="web", mins="21s", profile="default")
    assert "abs attach default" in sent


async def test_blocked_ping_is_sent_once_not_every_tick(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    engine = StatusEngine(status="blocked")
    now = [0.0]
    poller = await _go_live(
        abs_home, client_factory, engine, tmp_path, now, blocked_debounce_s=0.0
    )
    before = len(fake.sent_messages)
    for t in (1.0, 4.0, 7.0, 10.0, 100.0):
        now[0] = t
        await poller.watch_once()
    assert len(fake.sent_messages) == before + 1


async def test_blocked_ping_goes_to_the_chat_that_started_the_session(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    engine = StatusEngine(status="blocked")
    now = [0.0]
    poller = await _go_live(
        abs_home, client_factory, engine, tmp_path, now, blocked_debounce_s=0.0
    )
    now[0] = 1.0
    await poller.watch_once()
    assert fake.sent_messages[-1]["chat_id"] == 42


async def test_the_probe_targets_our_recorded_pane(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    """An attach can open a second pane; reading its status would report on a bare
    shell. Liveness learned this the hard way (2.2c) — status must not repeat it."""
    write_profile(abs_home, allow_ids=[42])
    engine = StatusEngine(status="working")
    now = [0.0]
    poller = await _go_live(abs_home, client_factory, engine, tmp_path, now)
    await poller.watch_once()
    assert engine.asked and engine.asked[-1] == ("default", "default:w1:p1")


async def test_blocked_emits_the_event(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    engine = StatusEngine(status="blocked")
    now = [0.0]
    poller = await _go_live(
        abs_home,
        client_factory,
        engine,
        tmp_path,
        now,
        blocked_debounce_s=10.0,
        events=_events(abs_home),
    )
    await poller.watch_once()  # the episode starts at the first sample
    now[0] = 12.0
    await poller.watch_once()
    blocked = [e for e in _records(abs_home) if e["event"] == EVENT_SESSION_BLOCKED]
    assert len(blocked) == 1
    assert blocked[0]["blocked_for_s"] == 12
    assert blocked[0]["profile"] == "default"


# ---- when it must stay silent ------------------------------------------------


async def test_no_ping_on_a_backend_without_agent_status(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    """tmux. The feature is absent (D4), so the loop must run clean and quiet."""
    write_profile(abs_home, allow_ids=[42])
    engine = FakeEngine()  # no agent_status attribute at all
    now = [0.0]
    poller = await _go_live(
        abs_home, client_factory, engine, tmp_path, now, blocked_debounce_s=0.0
    )
    before = len(fake.sent_messages)
    for t in (1.0, 50.0, 500.0):
        now[0] = t
        assert await poller.watch_once() is True
    assert len(fake.sent_messages) == before


async def test_no_ping_when_blocked_notify_is_off(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    engine = StatusEngine(status="blocked")
    now = [0.0]
    poller = await _go_live(
        abs_home,
        client_factory,
        engine,
        tmp_path,
        now,
        blocked_notify=False,
        blocked_debounce_s=0.0,
    )
    before = len(fake.sent_messages)
    now[0] = 100.0
    await poller.watch_once()
    assert len(fake.sent_messages) == before
    assert engine.asked == []  # and it did not even probe


async def test_a_probe_that_raises_never_disturbs_the_session(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    """A status probe is a convenience. If herdr is wedged, the session keeps
    running and the watch loop keeps reporting it alive."""
    write_profile(abs_home, allow_ids=[42])
    engine = StatusEngine(status="blocked")
    engine.raise_on_probe = OSError("herdr socket gone")
    now = [0.0]
    poller = await _go_live(
        abs_home, client_factory, engine, tmp_path, now, blocked_debounce_s=0.0
    )
    before = len(fake.sent_messages)
    now[0] = 100.0
    assert await poller.watch_once() is True
    assert len(fake.sent_messages) == before


async def test_a_probe_returning_none_holds_a_pending_block(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    engine = StatusEngine(status="blocked")
    now = [0.0]
    poller = await _go_live(
        abs_home, client_factory, engine, tmp_path, now, blocked_debounce_s=20.0
    )
    before = len(fake.sent_messages)
    await poller.watch_once()  # blocked observed at t=0 — the episode starts
    engine.status = None  # then herdr cannot tell us
    now[0] = 10.0
    await poller.watch_once()
    assert len(fake.sent_messages) == before
    engine.status = "blocked"
    now[0] = 21.0  # 21s since the block STARTED, not since it came back
    await poller.watch_once()
    assert len(fake.sent_messages) == before + 1


async def test_no_ping_while_the_session_is_still_starting(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    """Before anything is alive there is nothing to be blocked on; a launch that is
    still booting must not produce a "waiting for input" ping."""
    write_profile(abs_home, allow_ids=[42])
    engine = StatusEngine(status="blocked")
    now = [0.0]
    poller = await _go_live(
        abs_home, client_factory, engine, tmp_path, now, blocked_debounce_s=0.0
    )
    engine._alive["default"] = False  # launched, never came alive
    before = len(fake.sent_messages)
    now[0] = 5.0
    assert await poller.watch_once() is True  # still inside the start grace
    assert len(fake.sent_messages) == before
    assert engine.asked == []


async def test_a_block_does_not_survive_into_the_next_session(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    engine = StatusEngine(status="blocked")
    now = [0.0]
    poller = await _go_live(
        abs_home, client_factory, engine, tmp_path, now, blocked_debounce_s=20.0
    )
    now[0] = 5.0
    await poller.watch_once()
    assert poller._status_watcher.blocked_pending

    poller._reset_session_fields()
    assert not poller._status_watcher.blocked_pending
    assert poller._session_label is None


async def test_answering_at_the_desk_within_seconds_pings_nothing(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    """E6, through the wired poller: the operator is AT the keyboard.

    ``test_a_short_block_answered_at_the_desk_never_pings`` pins this in the
    decision layer, which is where the arithmetic lives — but a poller that
    probed the wrong pane, or sent on any non-empty status, would sail past that
    test and still cry wolf on every prompt. This is the case the feature lives
    or dies on: a watcher that pings while someone is sitting there gets muted,
    and a muted watcher never works when it counts.
    """
    write_profile(abs_home, allow_ids=[42])
    engine = StatusEngine(status="working")
    now = [0.0]
    poller = await _go_live(
        abs_home,
        client_factory,
        engine,
        tmp_path,
        now,
        blocked_debounce_s=20.0,
        events=_events(abs_home),
    )
    before = len(fake.sent_messages)

    # Three prompts, each answered inside the debounce. Blocked is only ever
    # observed for a few seconds at a time, exactly as it looks at the desk.
    for answered_at in (30.0, 90.0, 150.0):
        engine.status = "blocked"
        now[0] = answered_at - 5.0
        assert await poller.watch_once() is True
        engine.status = "working"
        now[0] = answered_at
        assert await poller.watch_once() is True

    assert len(fake.sent_messages) == before
    assert engine.asked  # it did probe — silence is the verdict, not a skip
    assert [e for e in _records(abs_home) if e["event"] == EVENT_SESSION_BLOCKED] == []


async def test_a_second_block_in_the_same_session_pings_again(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    """E5, through the wired poller. A new block is a new episode.

    The once-per-episode latch is what makes E3 pass; if it never cleared, you'd
    get exactly one ping per session and every later block would be silent — the
    feature quietly dying after first use, which no other test here would catch.
    """
    write_profile(abs_home, allow_ids=[42])
    engine = StatusEngine(status="blocked")
    now = [0.0]
    poller = await _go_live(
        abs_home,
        client_factory,
        engine,
        tmp_path,
        now,
        blocked_debounce_s=20.0,
        events=_events(abs_home),
    )
    before = len(fake.sent_messages)

    await poller.watch_once()  # episode 1 starts at t=0
    now[0] = 25.0
    await poller.watch_once()
    assert len(fake.sent_messages) == before + 1

    engine.status = "working"  # answered in the chat
    now[0] = 30.0
    await poller.watch_once()

    engine.status = "blocked"  # episode 2
    now[0] = 100.0
    await poller.watch_once()
    now[0] = 125.0
    await poller.watch_once()
    assert len(fake.sent_messages) == before + 2

    blocked = [e for e in _records(abs_home) if e["event"] == EVENT_SESSION_BLOCKED]
    assert [e["blocked_for_s"] for e in blocked] == [25, 25]


async def test_the_blocked_event_carries_no_message_text(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    """E7. The event log is a diagnostic, not a transcript — it records that a
    session blocked and for how long, never what was being said."""
    write_profile(abs_home, allow_ids=[42])
    engine = StatusEngine(status="blocked")
    now = [0.0]
    poller = await _go_live(
        abs_home,
        client_factory,
        engine,
        tmp_path,
        now,
        blocked_debounce_s=0.0,
        events=_events(abs_home),
    )
    await poller.watch_once()
    sent = fake.sent_messages[-1]["text"]
    assert "waiting" in sent  # the ping did go out, so there was something to leak

    (blocked,) = [e for e in _records(abs_home) if e["event"] == EVENT_SESSION_BLOCKED]
    assert set(blocked) == {"ts", "event", "level", "profile", "blocked_for_s"}
    assert "web" not in json.dumps(blocked)  # not even the project label


# ---- the done ping (opt-in) --------------------------------------------------


async def test_done_ping_is_off_by_default(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    engine = StatusEngine(status="done")
    now = [0.0]
    poller = await _go_live(abs_home, client_factory, engine, tmp_path, now)
    before = len(fake.sent_messages)
    now[0] = 10.0
    await poller.watch_once()
    assert len(fake.sent_messages) == before


async def test_done_ping_when_enabled(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    engine = StatusEngine(status="done")
    now = [0.0]
    poller = await _go_live(
        abs_home,
        client_factory,
        engine,
        tmp_path,
        now,
        done_notify=True,
        events=_events(abs_home),
    )
    now[0] = 10.0
    await poller.watch_once()
    assert fake.sent_messages[-1]["text"] == DONE_MSG.format(label="web")
    assert EVENT_SESSION_DONE in [e["event"] for e in _records(abs_home)]


# ---- Step 2.3: the stale-handoff sweep --------------------------------------


async def test_sweep_arms_the_timer_before_it_ever_sweeps(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    """Boot recovery has just run; a second opinion one cycle later adds nothing."""
    write_profile(abs_home, allow_ids=[42])
    now = [10_000.0]
    poller = make_poller(
        abs_home, client_factory, engine=FakeEngine(), clock=_clock_at(now)
    )
    poller._write_handoff_marker("/p", "normal", 42)
    assert await poller.sweep_stale_handoff() is False


async def test_sweep_reclaims_an_old_marker_with_nothing_alive(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    now = [10_000.0]
    engine = FakeEngine()
    poller = make_poller(
        abs_home,
        client_factory,
        engine=engine,
        clock=_clock_at(now),
        events=_events(abs_home),
        stale_handoff_after_s=600.0,
        stale_handoff_check_s=60.0,
    )
    poller._write_handoff_marker("/p", "normal", 42)
    marker = abs_home / "profiles" / "default" / "daemon-handoff.json"
    assert marker.exists()

    await poller.sweep_stale_handoff()  # arms
    now[0] += 61.0
    # The marker's age comes from its wall-clock timestamp, so make it old.
    poller._marker_age_s = lambda m: 700.0  # type: ignore[method-assign]

    assert await poller.sweep_stale_handoff() is True
    assert not marker.exists()
    assert "default" in engine.kills  # the engine leftover went too
    assert EVENT_STALE_HANDOFF in [e["event"] for e in _records(abs_home)]


async def test_sweep_leaves_a_young_marker_alone(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    """A session that is still booting must never be swept — hence the validated
    floor of session_start_grace_s under stale_handoff_after_s."""
    write_profile(abs_home, allow_ids=[42])
    now = [10_000.0]
    poller = make_poller(
        abs_home,
        client_factory,
        engine=FakeEngine(),
        clock=_clock_at(now),
        stale_handoff_check_s=1.0,
    )
    poller._write_handoff_marker("/p", "normal", 42)
    await poller.sweep_stale_handoff()
    now[0] += 10.0
    poller._marker_age_s = lambda m: 30.0  # type: ignore[method-assign]
    assert await poller.sweep_stale_handoff() is False
    assert (abs_home / "profiles" / "default" / "daemon-handoff.json").exists()


async def test_sweep_leaves_a_marker_whose_session_pid_is_alive(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42], session_pid=os.getpid())
    now = [10_000.0]
    poller = make_poller(
        abs_home, client_factory, engine=FakeEngine(), clock=_clock_at(now),
        stale_handoff_check_s=1.0,
    )
    poller._write_handoff_marker("/p", "normal", 42)
    await poller.sweep_stale_handoff()
    now[0] += 10.0
    poller._marker_age_s = lambda m: 9_999.0  # type: ignore[method-assign]
    assert await poller.sweep_stale_handoff() is False


async def test_sweep_leaves_a_marker_whose_pane_is_alive(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    now = [10_000.0]
    engine = FakeEngine()
    engine._alive["default"] = True
    poller = make_poller(
        abs_home, client_factory, engine=engine, clock=_clock_at(now),
        stale_handoff_check_s=1.0,
    )
    poller._write_handoff_marker("/p", "normal", 42, pane_id="default:w1:p1")
    await poller.sweep_stale_handoff()
    now[0] += 10.0
    poller._marker_age_s = lambda m: 9_999.0  # type: ignore[method-assign]
    assert await poller.sweep_stale_handoff() is False
    assert engine.kills == []


async def test_sweep_never_runs_while_the_session_is_live(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    """watch_once owns SESSION_LIVE and knows more than the sweep does."""
    write_profile(abs_home, allow_ids=[42])
    now = [10_000.0]
    poller = await _go_live(
        abs_home, client_factory, FakeEngine(), tmp_path, now, stale_handoff_check_s=1.0
    )
    now[0] += 100.0
    poller._marker_age_s = lambda m: 9_999.0  # type: ignore[method-assign]
    assert await poller.sweep_stale_handoff() is False


async def test_sweep_is_rate_limited(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    now = [10_000.0]
    poller = make_poller(
        abs_home, client_factory, engine=FakeEngine(), clock=_clock_at(now),
        stale_handoff_check_s=60.0,
    )
    poller._write_handoff_marker("/p", "normal", 42)
    poller._marker_age_s = lambda m: 9_999.0  # type: ignore[method-assign]
    assert await poller.sweep_stale_handoff() is False  # arms
    now[0] += 30.0
    assert await poller.sweep_stale_handoff() is False  # too soon
    assert (abs_home / "profiles" / "default" / "daemon-handoff.json").exists()
    now[0] += 31.0
    assert await poller.sweep_stale_handoff() is True


async def test_sweep_declines_when_the_engine_cannot_answer(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    """Cannot tell → assume alive. Guessing here would kill the operator's work."""
    write_profile(abs_home, allow_ids=[42])

    class BlindEngine(FakeEngine):
        def is_alive(self, profile, pane_id=None):
            from absd.engines.base import EngineError

            raise EngineError("cannot reach the engine")

    now = [10_000.0]
    engine = BlindEngine()
    poller = make_poller(
        abs_home, client_factory, engine=engine, clock=_clock_at(now),
        stale_handoff_check_s=1.0,
    )
    poller._write_handoff_marker("/p", "normal", 42, pane_id="default:w1:p1")
    await poller.sweep_stale_handoff()
    now[0] += 10.0
    poller._marker_age_s = lambda m: 9_999.0  # type: ignore[method-assign]
    assert await poller.sweep_stale_handoff() is False
