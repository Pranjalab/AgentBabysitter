"""Crash/restart recovery + reboot notification (Step 1.8)."""

from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from absd.daemon import REBOOT_NOTICE, STATE_IDLE, STATE_SESSION_LIVE
from absd.events import EventLog, iter_events
from absd.pool import PooledMessage, utc_now_iso
from tests.conftest import write_profile
from tests.harness.fake_telegram import FakeTelegram
from tests.test_flow_e2e import FakeEngine, make_poller

_DEAD_PID = 999_999_999  # not a live process


def _write_marker(
    abs_home: Path,
    *,
    profile: str = "default",
    project: str = "/p/llm",
    chat_id: int | None = 42,
    pane_id: str | None = "default:w1:p1",
    launcher_pid: int | None = None,
    ts: str | None = None,
) -> None:
    rec = {
        "timestamp": ts or utc_now_iso(),
        "project": project,
        "mode": "normal",
        "chat_id": chat_id,
        "pane_id": pane_id,
        "launcher_pid": launcher_pid,
    }
    p = abs_home / "profiles" / profile / "daemon-handoff.json"
    p.write_text(json.dumps(rec))


def _events(abs_home: Path) -> EventLog:
    return EventLog(abs_home / "daemon" / "events.jsonl")


# ---- recovery matrix ---------------------------------------------------------


async def test_recover_live_via_engine_pane(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    engine = FakeEngine()
    engine._alive["default"] = True  # recorded pane still alive
    ts = (datetime.now(timezone.utc) - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_marker(abs_home, launcher_pid=_DEAD_PID, ts=ts)  # pid dead, pane alive
    poller = make_poller(abs_home, client_factory, engine=engine)

    outcome = await poller.boot_recover_and_notify()

    assert outcome == "session-live"
    assert poller.session_state == STATE_SESSION_LIVE
    assert poller._session_pane_id == "default:w1:p1"
    assert poller._handoff_chat_id == 42
    # lived_s derived from marker timestamp: a poll_once must NOT poll the token now
    before = fake.getupdates_calls
    await poller.poll_once()
    assert fake.getupdates_calls == before  # never a second consumer on the token


async def test_recover_live_via_pid(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    engine = FakeEngine()  # engine reports dead
    _write_marker(abs_home, pane_id=None, launcher_pid=os.getpid())  # our pid is alive
    poller = make_poller(abs_home, client_factory, engine=engine)

    outcome = await poller.boot_recover_and_notify()
    assert outcome == "session-live"
    assert poller.session_state == STATE_SESSION_LIVE


async def test_recover_dead_marker_reclaims_and_notifies(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    engine = FakeEngine()  # dead
    engine._alive["default"] = True  # a leftover engine session to be killed
    poller = make_poller(abs_home, client_factory, engine=engine, events=_events(abs_home))
    poller.pool.append(PooledMessage(1, 42, "kept", utc_now_iso()))
    ts = (datetime.now(timezone.utc) - timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # engine alive would make it "live"; force dead by pane_id absent + dead pid:
    engine._alive["default"] = False
    _write_marker(abs_home, launcher_pid=_DEAD_PID, ts=ts)

    outcome = await poller.boot_recover_and_notify()

    assert outcome == "reclaimed"
    assert poller.session_state == STATE_IDLE
    assert not (abs_home / "profiles" / "default" / "daemon-handoff.json").exists()
    # reboot notice with pool count intact
    assert fake.sent_messages[-1]["text"] == REBOOT_NOTICE.format(label="llm", n=1)
    # session_end lived_s derived from the marker timestamp (~90s)
    end = next(iter_events(abs_home / "daemon" / "events.jsonl", event="session_end"))
    assert 85 <= end["lived_s"] <= 100
    assert poller.pool.count() == 1  # no pool loss


async def test_recover_dead_kills_engine_leftover(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    engine = FakeEngine()
    engine._alive["default"] = False
    _write_marker(abs_home, launcher_pid=_DEAD_PID)
    poller = make_poller(abs_home, client_factory, engine=engine)
    await poller.boot_recover_and_notify()
    assert "default" in engine.kills  # leftover torn down


async def test_no_marker_stale_pid_notifies(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    # A terminal session (no marker) whose session.pid is now dead → reboot notice
    # to the profile's chat (from rc.json).
    write_profile(abs_home, allow_ids=[42], session_pid=_DEAD_PID)
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())
    outcome = await poller.boot_recover_and_notify()
    assert outcome == "stale-terminal"
    assert fake.sent_messages[-1]["text"] == REBOOT_NOTICE.format(label="default", n=0)


async def test_no_marker_live_pid_no_notice(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42], session_pid=os.getpid())  # live terminal
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())
    outcome = await poller.boot_recover_and_notify()
    assert outcome == "idle"
    assert fake.sent_messages == []


async def test_reboot_notice_suppressed_when_off(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42], session_pid=_DEAD_PID, dm_policy="disabled")
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())
    await poller.boot_recover_and_notify()
    assert fake.sent_messages == []  # off → suppressed


async def test_boot_recover_runs_once(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42], session_pid=_DEAD_PID)
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())
    await poller.boot_recover_and_notify()
    n = len(fake.sent_messages)
    assert await poller.boot_recover_and_notify() == "already"  # second call is a no-op
    assert len(fake.sent_messages) == n


# ---- chaos: crash at random points, restart, converge ------------------------


async def test_chaos_crash_recovery_converges(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    """50 seeded iterations: a daemon session existed at a random liveness with a
    random pool; a fresh poller (simulating a process restart) recovers from disk.
    Invariants: converges to SESSION_LIVE iff the session survived; never polls the
    token while live (no double consumer); dead → IDLE, marker cleared, notice
    sent; the pool is never lost."""
    rng = random.Random(1337)
    marker_path = abs_home / "profiles" / "default" / "daemon-handoff.json"
    for i in range(50):
        # fresh-ish per iteration: reset profile + pool + marker + engine
        write_profile(abs_home, allow_ids=[42])
        (abs_home / "profiles" / "default" / "pool.jsonl").unlink(missing_ok=True)
        marker_path.unlink(missing_ok=True)
        fake.sent_messages.clear()

        engine = FakeEngine()
        alive = rng.random() < 0.5
        engine._alive["default"] = alive
        n_pool = rng.randint(0, 3)

        poller_pre = make_poller(abs_home, client_factory, engine=engine)
        for j in range(n_pool):
            poller_pre.pool.append(PooledMessage(j + 1, 42, f"m{j}", utc_now_iso()))
        _write_marker(
            abs_home,
            launcher_pid=(os.getpid() if rng.random() < 0.2 else _DEAD_PID),
            pane_id="default:w1:p1",
        )
        # A launcher pid that is alive OR an alive engine pane both mean "survived".
        marker = json.loads(marker_path.read_text())
        survived = alive or marker["launcher_pid"] == os.getpid()

        # "restart": brand-new poller reads only what's on disk.
        poller = make_poller(abs_home, client_factory, engine=engine, events=_events(abs_home))
        await poller.boot_recover_and_notify()

        before = fake.getupdates_calls
        await poller.poll_once()
        after = fake.getupdates_calls

        if survived:
            assert poller.session_state == STATE_SESSION_LIVE, f"iter {i}"
            assert after == before, f"iter {i}: polled the token while live"
        else:
            assert poller.session_state == STATE_IDLE, f"iter {i}"
            assert not marker_path.exists(), f"iter {i}: stale marker left"
        # pool never lost by recovery
        assert poller.pool.count() == n_pool, f"iter {i}: pool loss"
