"""Daemon profile rescan (new-bot provisioning, Stage 2).

The daemon discovers profiles at boot only; a bot provisioned by ``abs start
new-bot`` while it runs must get an idle poller without a restart. These tests
drive :func:`absd.__main__._rescan_once` / ``_rescan_loop`` directly, against
FakeTelegram instances (one per fake bot) and a temp ABS_HOME — no real
Telegram, no real profiles, no leaked processes.

What is proven:
  - a profile appearing on disk gains a live, actually-polling poller + a
    ``profile_added`` event;
  - a profile whose dir vanishes has its poller stopped/dropped + a
    ``profile_removed`` event, with its client closed (no leak);
  - an existing poller (and, by construction, any live session) is untouched;
  - a profile with no token yet is skipped (a later cycle picks it up);
  - cadence 0 disables the loop.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from absd.__main__ import (
    _RunCtx,
    _build_one_poller,
    _default_script_path,
    _drop_poller,
    _make_session_count,
    _rescan_loop,
    _rescan_once,
    _run_profile,
)
from absd.config import DaemonConfig
from absd.events import EventLog, iter_events
from tests.conftest import write_profile
from tests.harness.fake_telegram import FakeTelegram
from tests.test_multiprofile import point_at, wait_for


def make_ctx(abs_home: Path, cfg: DaemonConfig) -> _RunCtx:
    """A rescan context over a temp ABS_HOME. The GLOBAL base_url is a bogus
    non-localhost value on purpose — every poller must reach its fake through the
    per-profile localhost override, never the global."""
    return _RunCtx(
        cfg=cfg,
        abs_home=abs_home,
        home=abs_home,
        daemon_dir=abs_home / "daemon",
        base_url="https://api.telegram.org",
        engine=None,
        script_path=_default_script_path(),
        session_count=_make_session_count(None),
        events=EventLog(abs_home / "daemon" / "events.jsonl"),
        sandbox_mgr=None,
    )


def spawn_into(live: dict, ctx: _RunCtx, name: str, stop: asyncio.Event) -> None:
    """Build + start a poller for an already-on-disk profile and register it in
    ``live`` — the boot-time equivalent, so a test can seed 'existing' pollers."""
    result = _build_one_poller(
        _profile(ctx, name),
        ctx.cfg,
        ctx.daemon_dir,
        ctx.base_url,
        engine=ctx.engine,
        script_path=ctx.script_path,
        session_count=ctx.session_count,
        events=ctx.events,
        sandbox_mgr=ctx.sandbox_mgr,
    )
    assert result is not None
    poller, client = result
    task = asyncio.ensure_future(_run_profile(poller, 0.0, stop))
    live[name] = {"poller": poller, "client": client, "task": task}


def _profile(ctx: _RunCtx, name: str):
    from absd.profiles import Profile

    return Profile.load(name, ctx.abs_home, ctx.home)


async def _teardown(live: dict, fakes: list[FakeTelegram]) -> None:
    for name, entry in list(live.items()):
        await _drop_poller(name, entry)
    for f in fakes:
        await f.stop()


# ---- 1. a new profile gets a live poller + profile_added ---------------------


async def test_rescan_adds_poller_for_new_profile(abs_home: Path) -> None:
    fa, fb = FakeTelegram(), FakeTelegram()
    await fa.start()
    await fb.start()
    stop = asyncio.Event()
    cfg = DaemonConfig(poll_timeout_s=0, poll_stagger_s=0, reclaim_grace_s=0)
    ctx = make_ctx(abs_home, cfg)
    live: dict = {}
    try:
        # Boot: one existing profile, already polling.
        write_profile(abs_home, "default", allow_ids=[42])
        point_at(abs_home, "default", fa)
        spawn_into(live, ctx, "default", stop)
        assert await wait_for(lambda: fa.getupdates_calls >= 1)
        default_task = live["default"]["task"]

        # A new bot is provisioned while the daemon runs.
        write_profile(abs_home, "newbot", allow_ids=[7])
        point_at(abs_home, "newbot", fb)

        added, removed = await _rescan_once(live, ctx, stop)

        assert added == ["newbot"]
        assert removed == []
        assert "newbot" in live
        # The new poller is actually live and reaching its own fake bot.
        assert await wait_for(lambda: fb.getupdates_calls >= 1)
        # The pre-existing poller was never disturbed.
        assert live["default"]["task"] is default_task
        assert not default_task.done()

        events = [e["event"] for e in iter_events(abs_home / "daemon" / "events.jsonl")]
        assert "profile_added" in events
        added_ev = next(
            e for e in iter_events(abs_home / "daemon" / "events.jsonl", event="profile_added")
        )
        assert added_ev["profile"] == "newbot"
    finally:
        await _teardown(live, [fa, fb])


# ---- 2. a vanished profile's poller is dropped + profile_removed -------------


async def test_rescan_drops_poller_for_removed_profile(abs_home: Path) -> None:
    fa, fb = FakeTelegram(), FakeTelegram()
    await fa.start()
    await fb.start()
    stop = asyncio.Event()
    cfg = DaemonConfig(poll_timeout_s=0, poll_stagger_s=0, reclaim_grace_s=0)
    ctx = make_ctx(abs_home, cfg)
    live: dict = {}
    try:
        write_profile(abs_home, "default", allow_ids=[42])
        write_profile(abs_home, "throwaway", allow_ids=[7])
        point_at(abs_home, "default", fa)
        point_at(abs_home, "throwaway", fb)
        spawn_into(live, ctx, "default", stop)
        spawn_into(live, ctx, "throwaway", stop)
        assert await wait_for(lambda: fb.getupdates_calls >= 1)
        gone_task = live["throwaway"]["task"]
        gone_client = live["throwaway"]["client"]

        # The operator deletes the profile.
        shutil.rmtree(abs_home / "profiles" / "throwaway")

        added, removed = await _rescan_once(live, ctx, stop)

        assert added == []
        assert removed == ["throwaway"]
        assert "throwaway" not in live
        # Its poller task is cancelled and its client is closed (no leak): the
        # client polled (opened a session) then close() reset it to None.
        assert gone_task.done()
        assert gone_client._session is None
        # The surviving profile is untouched.
        assert "default" in live
        assert not live["default"]["task"].done()

        removed_ev = next(
            e for e in iter_events(abs_home / "daemon" / "events.jsonl", event="profile_removed")
        )
        assert removed_ev["profile"] == "throwaway"
    finally:
        await _teardown(live, [fa, fb])


# ---- 3. a tokenless profile is skipped (no crash, no phantom poller) ----------


async def test_rescan_skips_profile_without_token(abs_home: Path) -> None:
    stop = asyncio.Event()
    cfg = DaemonConfig(poll_timeout_s=0, poll_stagger_s=0)
    ctx = make_ctx(abs_home, cfg)
    live: dict = {}
    try:
        # A half-written profile: rc.json present (so discover sees it) but the
        # .env has no token line yet.
        prof = abs_home / "profiles" / "half"
        prof.mkdir(parents=True)
        (prof / "rc.json").write_text('{"tg_dir":"%s"}' % (abs_home / "tgx"), encoding="utf-8")
        (abs_home / "tgx").mkdir(parents=True, exist_ok=True)
        (abs_home / "tgx" / ".env").write_text("# no token yet\n", encoding="utf-8")

        added, removed = await _rescan_once(live, ctx, stop)

        assert added == []
        assert "half" not in live
    finally:
        await _teardown(live, [])


# ---- 4. rescan is idempotent when nothing changed ----------------------------


async def test_rescan_noop_when_unchanged(abs_home: Path) -> None:
    fa = FakeTelegram()
    await fa.start()
    stop = asyncio.Event()
    cfg = DaemonConfig(poll_timeout_s=0, poll_stagger_s=0)
    ctx = make_ctx(abs_home, cfg)
    live: dict = {}
    try:
        write_profile(abs_home, "default", allow_ids=[42])
        point_at(abs_home, "default", fa)
        spawn_into(live, ctx, "default", stop)
        first_task = live["default"]["task"]

        added, removed = await _rescan_once(live, ctx, stop)

        assert added == []
        assert removed == []
        assert live["default"]["task"] is first_task
    finally:
        await _teardown(live, [fa])


# ---- 5. cadence 0 disables the loop ------------------------------------------


async def test_rescan_loop_disabled_returns_immediately(abs_home: Path) -> None:
    cfg = DaemonConfig(profile_rescan_s=0)
    ctx = make_ctx(abs_home, cfg)
    stop = asyncio.Event()
    live: dict = {}
    # Should return at once (no sleeping) despite stop never being set.
    await asyncio.wait_for(_rescan_loop(live, ctx, stop), timeout=1.0)


# ---- 6. the loop runs a pass each cadence and honours stop --------------------


async def test_rescan_loop_runs_and_stops(abs_home: Path) -> None:
    fb = FakeTelegram()
    await fb.start()
    cfg = DaemonConfig(profile_rescan_s=0.01, poll_timeout_s=0, poll_stagger_s=0)
    ctx = make_ctx(abs_home, cfg)
    stop = asyncio.Event()
    live: dict = {}
    try:
        loop_task = asyncio.ensure_future(_rescan_loop(live, ctx, stop))
        # Provision a bot after the loop has started.
        write_profile(abs_home, "late", allow_ids=[9])
        point_at(abs_home, "late", fb)
        assert await wait_for(lambda: "late" in live)
        stop.set()
        await asyncio.wait_for(loop_task, timeout=2.0)
    finally:
        await _teardown(live, [fb])
