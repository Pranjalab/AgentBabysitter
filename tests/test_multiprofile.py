"""Step 1.4 — multi-profile pollers (G5): independence, supervision, stagger,
boot-time state detection, and restart-mid-state, all against FakeTelegram
instances (one per fake bot) and a temp ABS_HOME. No real Telegram, no real
profiles, no leaked processes (every spawned dummy is reaped in a finally).
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from pathlib import Path

from absd.config import DaemonConfig
from absd.__main__ import _build_pollers, _log_boot_state, _run_profile, _resolve_base_url
from absd.profiles import discover
from tests.conftest import write_profile
from tests.harness.fake_telegram import FakeTelegram


# ---- helpers -----------------------------------------------------------------


def point_at(abs_home: Path, name: str, fake: FakeTelegram) -> None:
    """Write the per-profile TEST base-url override so this profile's client
    talks to THIS fake bot (the Step 1.4 per-profile seam)."""
    (abs_home / "profiles" / name / ".telegram-base-url").write_text(
        fake.base_url, encoding="utf-8"
    )


def build_all(abs_home: Path, cfg: DaemonConfig):
    """Discover + build one poller per profile. The GLOBAL base_url is a bogus
    non-localhost value on purpose: every poller must reach its fake via the
    per-profile override, proving the global is never used here."""
    profiles = discover(abs_home, home=abs_home)
    return _build_pollers(profiles, cfg, abs_home / "daemon", "https://api.telegram.org")


async def fast_sleep(d: float) -> None:
    """Compressed sleep: caps every wait at 5ms so backoff/yield loops run fast
    but still yield to the event loop (no tight spins)."""
    await asyncio.sleep(min(d, 0.005))


async def wait_for(cond, timeout: float = 3.0) -> bool:
    """Poll ``cond()`` until true or timeout. Returns whether it became true."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.01)
    return cond()


def spawn_dummy() -> subprocess.Popen:
    """A real, alive child process whose PID stands in for a live session."""
    return subprocess.Popen(["sleep", "30"])


# ---- 1. mixed states: only the idle profile polls ----------------------------


async def test_mixed_states_only_idle_polls(abs_home: Path) -> None:
    fakes = [FakeTelegram() for _ in range(3)]
    for f in fakes:
        await f.start()
    fa, fb, fc = fakes
    dummy = spawn_dummy()
    clients = []
    try:
        # A live (real running pid), B idle, C blocked.
        write_profile(abs_home, "a_live", allow_ids=[42], session_pid=dummy.pid)
        write_profile(abs_home, "b_idle", allow_ids=[42])
        write_profile(abs_home, "c_blocked", allow_ids=[42], blocked=True)
        point_at(abs_home, "a_live", fa)
        point_at(abs_home, "b_idle", fb)
        point_at(abs_home, "c_blocked", fc)
        for f in fakes:
            f.queue_message("hello", from_id=42)

        cfg = DaemonConfig(poll_timeout_s=0)
        pollers = build_all(abs_home, cfg)
        clients = [c for _, c in pollers]
        by_name = {p.profile.name: p for p, _ in pollers}

        assert await by_name["a_live"].poll_once() == -1  # yields to live session
        assert await by_name["b_idle"].poll_once() == 1  # polls + pools
        assert await by_name["c_blocked"].poll_once() == -1  # blocked

        # Only B ever hit Telegram.
        assert fa.getupdates_calls == 0
        assert fc.getupdates_calls == 0
        assert fb.getupdates_calls >= 1
        # Only B pooled + acked.
        assert by_name["b_idle"].pool.count() == 1
        assert by_name["a_live"].pool.count() == 0
        assert by_name["c_blocked"].pool.count() == 0
        assert len(fb.sent_messages) == 1
        assert fa.sent_messages == []
        assert fc.sent_messages == []
    finally:
        dummy.terminate()
        dummy.wait()
        for c in clients:
            await c.close()
        for f in fakes:
            await f.stop()


# ---- 2. independence: one bot's 409 doesn't stall another --------------------


async def test_409_isolation_other_profile_unaffected(abs_home: Path) -> None:
    fb = FakeTelegram()
    fc = FakeTelegram()
    await fb.start()
    await fc.start()
    clients = []
    tasks = []
    stop = asyncio.Event()
    try:
        write_profile(abs_home, "b_stuck", allow_ids=[42])
        write_profile(abs_home, "c_ok", allow_ids=[42])
        point_at(abs_home, "b_stuck", fb)
        point_at(abs_home, "c_ok", fc)
        fb.set_always_409()  # B: a stuck second consumer owns the token
        fc.queue_message("still hear me", from_id=42)

        cfg = DaemonConfig(poll_timeout_s=1, reclaim_backoff_max_s=10.0)
        pollers = build_all(abs_home, cfg)
        clients = [c for _, c in pollers]
        by_name = {p.profile.name: p for p, _ in pollers}

        for p in (by_name["b_stuck"], by_name["c_ok"]):
            tasks.append(
                asyncio.ensure_future(
                    _run_profile(
                        p,
                        0.0,
                        stop,
                        supervise_sleep=fast_sleep,
                        poller_sleep=fast_sleep,
                    )
                )
            )

        # C keeps polling normally and pools its message despite B's 409 storm.
        assert await wait_for(lambda: by_name["c_ok"].pool.count() == 1)
        # B genuinely tried and is backing off (>=1 getUpdates that 409'd)...
        assert await wait_for(lambda: fb.getupdates_calls >= 1)
        # ...and B's task is still alive (a 409 is not a crash).
        assert not tasks[0].done()
        assert by_name["b_stuck"].pool.count() == 0
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        for c in clients:
            await c.close()
        await fb.stop()
        await fc.stop()


# ---- 3. supervision: a dead poller is logged loudly and restarted ------------


async def test_poller_death_is_supervised_and_restarted(
    abs_home: Path, caplog
) -> None:
    fa = FakeTelegram()
    fb = FakeTelegram()
    await fa.start()
    await fb.start()
    clients = []
    tasks = []
    stop = asyncio.Event()
    try:
        write_profile(abs_home, "a_crashy", allow_ids=[42])
        write_profile(abs_home, "b_calm", allow_ids=[42])
        point_at(abs_home, "a_crashy", fa)
        point_at(abs_home, "b_calm", fb)
        fa.queue_message("survive the crash", from_id=42)
        fb.queue_message("i am fine", from_id=42)

        cfg = DaemonConfig(poll_timeout_s=1, reclaim_backoff_max_s=10.0)
        pollers = build_all(abs_home, cfg)
        clients = [c for _, c in pollers]
        by_name = {p.profile.name: p for p, _ in pollers}
        crashy = by_name["a_crashy"]

        # Inject an unexpected exception into A's first cycle only.
        real_poll = crashy.poll_once
        state = {"n": 0}

        async def flaky() -> int:
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("injected poller death")
            return await real_poll()

        crashy.poll_once = flaky  # type: ignore[method-assign]

        with caplog.at_level(logging.ERROR, logger="absd"):
            for p in (crashy, by_name["b_calm"]):
                tasks.append(
                    asyncio.ensure_future(
                        _run_profile(
                            p,
                            0.0,
                            stop,
                            supervise_sleep=fast_sleep,
                            poller_sleep=fast_sleep,
                        )
                    )
                )
            # After the restart, A resumes and pools its message.
            assert await wait_for(lambda: crashy.pool.count() == 1)
            # The other profile was never disturbed.
            assert await wait_for(lambda: by_name["b_calm"].pool.count() == 1)

        assert state["n"] >= 2  # crashed once, then ran again (restart)
        assert not tasks[0].done() and not tasks[1].done()
        loud = [r for r in caplog.records if "DIED unexpectedly" in r.getMessage()]
        assert loud, "expected a loud supervisor restart log line"
        assert loud[0].levelno >= logging.ERROR
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        for c in clients:
            await c.close()
        await fa.stop()
        await fb.stop()


# ---- 4. staggered start: first polls are spread out --------------------------


async def test_staggered_start_spreads_first_polls(abs_home: Path) -> None:
    fakes = [FakeTelegram() for _ in range(3)]
    for f in fakes:
        await f.start()
    clients = []
    tasks = []
    stop = asyncio.Event()
    try:
        # Names sort a<b<c so discovery/launch order is deterministic.
        for name, f in zip(("a", "b", "c"), fakes):
            write_profile(abs_home, name, allow_ids=[42])
            point_at(abs_home, name, f)
            f.queue_message("hi", from_id=42)

        stagger = 0.2
        cfg = DaemonConfig(poll_timeout_s=1, poll_stagger_s=stagger)
        pollers = build_all(abs_home, cfg)
        clients = [c for _, c in pollers]

        # Launch exactly as _run_forever does: stagger by index, real sleeps.
        for i, (p, _) in enumerate(pollers):
            tasks.append(
                asyncio.ensure_future(_run_profile(p, stagger * i, stop))
            )

        assert await wait_for(
            lambda: all(f.first_getupdates_at is not None for f in fakes), timeout=3.0
        )
        ta, tb, tc = (f.first_getupdates_at for f in fakes)
        # Strictly increasing, spaced by roughly the stagger (allow scheduler slop).
        assert ta < tb < tc
        assert (tb - ta) >= stagger * 0.6
        assert (tc - tb) >= stagger * 0.6
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        for c in clients:
            await c.close()
        for f in fakes:
            await f.stop()


# ---- 5. boot-time detection: stale vs live pid -------------------------------


async def test_boot_time_stale_pid_polls_live_pid_yields(
    abs_home: Path, caplog
) -> None:
    fa = FakeTelegram()
    fb = FakeTelegram()
    await fa.start()
    await fb.start()
    clients = []
    dummy = spawn_dummy()
    # A reliably-dead pid: spawn then reap it.
    dead = spawn_dummy()
    dead_pid = dead.pid
    dead.terminate()
    dead.wait()
    try:
        write_profile(abs_home, "live", allow_ids=[42], session_pid=dummy.pid)
        write_profile(abs_home, "stale", allow_ids=[42], session_pid=dead_pid)
        point_at(abs_home, "live", fa)
        point_at(abs_home, "stale", fb)
        fb.queue_message("stale bot still deaf?", from_id=42)

        profiles = {p.name: p for p in discover(abs_home, home=abs_home)}
        # Profile-level classification.
        assert profiles["live"].live_session_pid() == dummy.pid
        assert profiles["live"].has_stale_session_pid() is False
        assert profiles["stale"].live_session_pid() is None
        assert profiles["stale"].has_stale_session_pid() is True

        with caplog.at_level(logging.INFO, logger="absd"):
            _log_boot_state(list(profiles.values()))
        msgs = "\n".join(r.getMessage() for r in caplog.records)
        assert "LIVE session" in msgs
        assert "STALE session.pid" in msgs

        cfg = DaemonConfig(poll_timeout_s=0)
        pollers = {p.profile.name: (p, c) for p, c in build_all(abs_home, cfg)}
        clients = [c for _, c in pollers.values()]

        # Live yields; stale polls (it must NOT be fooled by the dead pid).
        assert await pollers["live"][0].poll_once() == -1
        assert fa.getupdates_calls == 0
        assert await pollers["stale"][0].poll_once() == 1
        assert pollers["stale"][0].pool.count() == 1

        # Neither pid file was touched (deletion is the launcher's job).
        assert (abs_home / "profiles" / "live" / "session.pid").exists()
        stale_pid_file = abs_home / "profiles" / "stale" / "session.pid"
        assert stale_pid_file.exists()
        assert stale_pid_file.read_text().strip() == str(dead_pid)
    finally:
        dummy.terminate()
        dummy.wait()
        for c in clients:
            await c.close()
        await fa.stop()
        await fb.stop()


# ---- 6. restart mid-state: resume offset, no redelivery ----------------------


async def test_restart_resumes_offset_no_duplicate(abs_home: Path) -> None:
    fa = FakeTelegram()
    fb = FakeTelegram()
    fc = FakeTelegram()
    await fa.start()
    await fb.start()
    await fc.start()
    dummy = spawn_dummy()
    clients = []
    try:
        write_profile(abs_home, "a_live", allow_ids=[42], session_pid=dummy.pid)
        write_profile(abs_home, "b_idle", allow_ids=[42])
        write_profile(abs_home, "c_blocked", allow_ids=[42], blocked=True)
        point_at(abs_home, "a_live", fa)
        point_at(abs_home, "b_idle", fb)
        point_at(abs_home, "c_blocked", fc)
        fb.queue_message("commit me", from_id=42)

        cfg = DaemonConfig(poll_timeout_s=0)

        # --- daemon run #1: B pools 1 and commits its offset ---
        pollers1 = {p.profile.name: (p, c) for p, c in build_all(abs_home, cfg)}
        clients += [c for _, c in pollers1.values()]
        assert await pollers1["b_idle"][0].poll_once() == 1
        committed = pollers1["b_idle"][0].offset
        assert committed is not None
        assert pollers1["b_idle"][0].pool.count() == 1
        assert len(fb.sent_messages) == 1
        # (simulate daemon cancel — just drop references / close clients)
        for _, c in pollers1.values():
            await c.close()
        clients = []

        # --- daemon run #2: same ABS_HOME, same fakes (Telegram state persists) ---
        pollers2 = {p.profile.name: (p, c) for p, c in build_all(abs_home, cfg)}
        clients += [c for _, c in pollers2.values()]
        b2 = pollers2["b_idle"][0]
        assert b2.offset == committed  # resumed from the committed offset on disk

        # Re-poll B: the fake confirms the committed offset and redelivers nothing.
        await b2.poll_once()
        assert b2.pool.count() == 1  # NO duplicate (D14 + offset resume)
        assert len(fb.sent_messages) == 1  # NO second ack

        # A still yielding, C still blocked after the "restart".
        assert await pollers2["a_live"][0].poll_once() == -1
        assert await pollers2["c_blocked"][0].poll_once() == -1
        assert fa.getupdates_calls == 0
        assert fc.getupdates_calls == 0
    finally:
        dummy.terminate()
        dummy.wait()
        for c in clients:
            await c.close()
        await fa.stop()
        await fb.stop()
        await fc.stop()


# ---- 7. per-profile base_url safety guard ------------------------------------


def test_resolve_base_url_refuses_non_localhost(abs_home: Path) -> None:
    write_profile(abs_home, "evil", allow_ids=[42])
    prof = discover(abs_home, home=abs_home)[0]
    prof.test_base_url_path.write_text("https://api.telegram.org", encoding="utf-8")
    # A non-localhost override is refused; the global default wins.
    assert _resolve_base_url(prof, "https://api.telegram.org") == "https://api.telegram.org"
    prof.test_base_url_path.write_text("http://evil.example.com:8080", encoding="utf-8")
    assert _resolve_base_url(prof, "GLOBAL") == "GLOBAL"


def test_resolve_base_url_accepts_localhost(abs_home: Path) -> None:
    write_profile(abs_home, "ok", allow_ids=[42])
    prof = discover(abs_home, home=abs_home)[0]
    for url in ("http://127.0.0.1:5599", "http://localhost:1234", "http://[::1]:9"):
        prof.test_base_url_path.write_text(url, encoding="utf-8")
        assert _resolve_base_url(prof, "GLOBAL") == url


def test_resolve_base_url_absent_file_uses_global(abs_home: Path) -> None:
    write_profile(abs_home, "plain", allow_ids=[42])
    prof = discover(abs_home, home=abs_home)[0]
    assert _resolve_base_url(prof, "https://api.telegram.org") == "https://api.telegram.org"
