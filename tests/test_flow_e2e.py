"""ABS START flow → HANDOFF → SESSION_LIVE → RECLAIM, end-to-end against fakes.

Uses a FakeEngine (records create_session, controllable liveness) plus per-test
FakeTelegram + temp ABS_HOME — no real engine, network, or claude (PLAN.md §10).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from absd.config import DaemonConfig
from absd.daemon import (
    ALREADY_LIVE_MSG,
    AT_CAP_MSG,
    FLOW_EXPIRED_MSG,
    HANDOFF_STALE_RECOVER_MSG,
    SESSION_ENDED_MSG,
    STATE_IDLE,
    STATE_RECLAIM,
    STATE_SESSION_LIVE,
    Extracted,
    HandoffRequest,
    Poller,
)
from absd.engines.base import EngineError, SessionInfo
from absd.profiles import discover
from absd.registry import Registry
from tests.conftest import write_profile
from tests.harness.fake_telegram import FakeTelegram


class FakeEngine:
    """Records create_session args; controllable per-profile liveness."""

    name = "fake"

    def __init__(self, fail: bool = False) -> None:
        self.created: list[dict] = []
        self._alive: dict[str, bool] = {}
        self.kills: list[str] = []
        self.fail = fail

    def available(self) -> bool:
        return True

    def create_session(self, profile, cwd, command, env) -> None:
        if self.fail:
            raise EngineError("simulated engine failure")
        if self._alive.get(profile):
            raise EngineError(f"session {profile} already exists")
        self.created.append(
            {
                "profile": profile,
                "cwd": str(cwd),
                "command": list(command),
                "env": dict(env),
            }
        )
        self._alive[profile] = True

    def is_alive(self, profile) -> bool:
        return self._alive.get(profile, False)

    def kill(self, profile) -> None:
        self.kills.append(profile)
        self._alive[profile] = False

    def attach_command(self, profile) -> str:
        return f"attach {profile}"

    def list_sessions(self) -> list[SessionInfo]:
        return [
            SessionInfo(profile=p, name=f"abs-{p}", alive=a)
            for p, a in self._alive.items()
        ]


def make_poller(
    abs_home: Path,
    client_factory,
    *,
    engine=None,
    workspace_root: str = "",
    clock=None,
    script_path: str = "/opt/abs.sh",
    **cfg_kw,
) -> Poller:
    prof = discover(abs_home, home=abs_home)[0]
    client = client_factory(prof.load_token())
    cfg = DaemonConfig(
        poll_timeout_s=0,
        reclaim_backoff_max_s=10.0,
        reclaim_grace_s=5.0,
        workspace_root=workspace_root,
        **cfg_kw,
    )
    kwargs = {}
    if clock is not None:
        kwargs["clock"] = clock
    return Poller(
        prof,
        client,
        cfg,
        state_dir=abs_home / "daemon",
        engine=engine,
        script_path=script_path,
        **kwargs,
    )


def _register(abs_home: Path, proj: Path) -> None:
    Registry(abs_home / "daemon" / "registry.json").add(proj)


# ---- full callback flow → handoff -------------------------------------------


async def test_full_callback_flow_handoff(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "web"
    proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine)

    # 1) ABS START → project keyboard
    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    assert poller.flow is not None and poller.flow.step == "project"
    kb = fake.sent_messages[-1]["reply_markup"]["inline_keyboard"]
    assert kb[0][0]["callback_data"] == "as:p:0"

    # 2) pick the registered project by callback → mode keyboard
    fake.queue_callback_query("as:p:0", from_id=42, chat_id=42)
    await poller.poll_once()
    assert poller.flow.step == "mode"

    # 3) pick Normal → HANDOFF
    fake.queue_callback_query("as:m:n", from_id=42, chat_id=42)
    await poller.poll_once()

    assert poller.session_state == STATE_SESSION_LIVE
    assert poller.flow is None
    assert len(engine.created) == 1
    created = engine.created[0]
    assert created["profile"] == "default"
    assert created["cwd"] == str(proj.resolve())
    assert created["command"] == [
        "bash", "/opt/abs.sh", "--profile", "default", "--daemon-start",
    ]
    assert created["env"]["ABS_HOME"] == str(abs_home)

    # confirmation + attach hint
    conf = fake.sent_messages[-1]["text"]
    assert "Started" in conf and "abs attach default" in conf

    # handoff marker written (timestamp, project, mode, chat)
    marker = json.loads(
        (abs_home / "profiles" / "default" / "daemon-handoff.json").read_text()
    )
    assert marker["project"] == str(proj.resolve())
    assert marker["mode"] == "normal"
    assert marker["chat_id"] == 42

    # single offset commit at handoff (FakeTelegram bookkeeping): the mode callback
    # was update_id 3, so the committed offset is 4, and the server confirmed it.
    assert poller.handoff_committed_offset == 4
    assert fake.confirmed_offset == 4

    # stopped polling: a further poll_once issues no getUpdates (SESSION_LIVE).
    before = fake.getupdates_calls
    await poller.poll_once()
    assert fake.getupdates_calls == before


async def test_text_fallback_flow_handoff(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "api"
    proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine)

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    fake.queue_message("1", from_id=42)  # numbered project pick
    await poller.poll_once()
    assert poller.flow.step == "mode"
    fake.queue_message("1", from_id=42)  # numbered mode pick (Normal)
    await poller.poll_once()

    assert poller.session_state == STATE_SESSION_LIVE
    assert engine.created[0]["cwd"] == str(proj.resolve())


async def test_away_flag_reaches_launcher(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "p"
    proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine)

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:p:0", from_id=42, chat_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:m:a", from_id=42, chat_id=42)  # Away
    await poller.poll_once()

    assert "--away" in engine.created[0]["command"]
    marker = json.loads(
        (abs_home / "profiles" / "default" / "daemon-handoff.json").read_text()
    )
    assert marker["mode"] == "away"


# ---- new-folder flow (D6) ----------------------------------------------------


async def test_new_folder_flow_creates_under_root(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    ws = tmp_path / "ws"
    ws.mkdir()
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine, workspace_root=str(ws))

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    # only option is "New folder"
    fake.queue_callback_query("as:nf", from_id=42, chat_id=42)
    await poller.poll_once()
    assert poller.flow.step == "folder"
    fake.queue_message("myapp", from_id=42)
    await poller.poll_once()
    assert poller.flow.step == "mode"
    fake.queue_callback_query("as:m:n", from_id=42, chat_id=42)
    await poller.poll_once()

    created_dir = ws / "myapp"
    assert created_dir.is_dir()
    assert engine.created[0]["cwd"] == str(created_dir.resolve())


@pytest.mark.parametrize("bad", ["../evil", "/etc", "a b", "café", "..", ""])
async def test_new_folder_rejects_bad_names(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path, bad: str
) -> None:
    write_profile(abs_home, allow_ids=[42])
    ws = tmp_path / "ws"
    ws.mkdir()
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine, workspace_root=str(ws))

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:nf", from_id=42, chat_id=42)
    await poller.poll_once()
    assert poller.flow.step == "folder"

    fake.queue_message(bad, from_id=42)
    await poller.poll_once()

    # still in the folder step (re-prompted); nothing created; no handoff.
    assert poller.flow is not None and poller.flow.step == "folder"
    assert list(ws.iterdir()) == []
    assert engine.created == []

    # a valid name then proceeds — proves the rejection was recoverable.
    fake.queue_message("good_name", from_id=42)
    await poller.poll_once()
    assert poller.flow.step == "mode"
    assert (ws / "good_name").is_dir()


async def test_new_folder_stays_jailed_under_root(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    # Even a valid single-segment name only ever lands directly under the root.
    write_profile(abs_home, allow_ids=[42])
    ws = tmp_path / "ws"
    ws.mkdir()
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine, workspace_root=str(ws))

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:nf", from_id=42, chat_id=42)
    await poller.poll_once()
    fake.queue_message("proj", from_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:m:n", from_id=42, chat_id=42)
    await poller.poll_once()

    assert (ws / "proj").is_dir()
    assert list(sibling.iterdir()) == []  # nothing escaped


# ---- flow timeout ------------------------------------------------------------


async def test_flow_times_out(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "p"
    proj.mkdir()
    _register(abs_home, proj)
    now = [1000.0]
    poller = make_poller(
        abs_home, client_factory, engine=FakeEngine(),
        clock=lambda: now[0], flow_timeout_s=300.0,
    )

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    assert poller.flow is not None

    now[0] += 301  # past the timeout
    fake.queue_message("hello?", from_id=42)
    await poller.poll_once()

    assert poller.flow is None
    assert any(m["text"] == FLOW_EXPIRED_MSG for m in fake.sent_messages)


# ---- ABS START while live / at cap -------------------------------------------


async def test_abs_start_while_live_gives_attach_hint(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    # A session is live (this pid) — the _begin_flow guard replies with the attach
    # hint and starts no flow. (In normal polling the live-session yield prevents
    # even reaching here; this proves the belt-and-suspenders race guard.)
    write_profile(abs_home, allow_ids=[42], session_pid=os.getpid())
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())
    ex = Extracted(update_id=1, from_id=42, chat_id=42, text="ABS START")
    await poller._begin_flow(ex)
    assert poller.flow is None
    assert fake.sent_messages[-1]["text"] == ALREADY_LIVE_MSG.format(profile="default")


async def test_abs_start_at_session_cap(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "p"
    proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    engine._alive["other"] = True  # one session already live elsewhere
    poller = make_poller(abs_home, client_factory, engine=engine, max_sessions=1)

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()

    assert poller.flow is None
    assert fake.sent_messages[-1]["text"] == AT_CAP_MSG.format(cap=1)


# ---- handoff launch failure --------------------------------------------------


async def test_handoff_launch_failure_reports_and_stays_idle(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "p"
    proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine(fail=True)
    poller = make_poller(abs_home, client_factory, engine=engine)

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:p:0", from_id=42, chat_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:m:n", from_id=42, chat_id=42)
    await poller.poll_once()

    assert poller.session_state == STATE_IDLE
    assert engine.created == []
    texts = [m["text"] for m in fake.sent_messages]
    # self-heal was attempted (stale-recover note), then an actionable failure.
    assert HANDOFF_STALE_RECOVER_MSG in texts
    assert "Couldn't start the session" in texts[-1]
    assert "abs daemon logs" in texts[-1]
    # marker cleaned up on failure
    assert not (abs_home / "profiles" / "default" / "daemon-handoff.json").exists()


# ---- HANDOFF self-heal over a stale engine session (live-demo bug 1) ---------


async def test_handoff_self_heals_stale_session(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    # A leftover engine session exists (create would collide) but NO genuinely live
    # session (no session.pid). Handoff must kill the stale one and retry once.
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "p"
    proj.mkdir()
    engine = FakeEngine()
    engine._alive["default"] = True  # stale leftover, but no live session.pid
    poller = make_poller(abs_home, client_factory, engine=engine)
    poller._handoff_request = HandoffRequest(
        chat_id=42, project_path=str(proj), label="p", away=False
    )
    poller.offset = 5

    await poller._do_handoff()

    assert poller.session_state == STATE_SESSION_LIVE
    assert len(engine.created) == 1  # created cleanly after self-heal
    texts = [m["text"] for m in fake.sent_messages]
    assert HANDOFF_STALE_RECOVER_MSG in texts  # told the user it recovered
    assert any("Started" in t for t in texts)  # then confirmed the start


async def test_self_heal_refuses_when_genuinely_live(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    # A create collision WITH a genuinely live session.pid must NOT self-heal
    # (never clobber a real session): refuse with the attach hint, no retry.
    write_profile(abs_home, allow_ids=[42], session_pid=os.getpid())
    proj = tmp_path / "p"
    proj.mkdir()
    engine = FakeEngine()
    engine._alive["default"] = True
    poller = make_poller(abs_home, client_factory, engine=engine)
    poller._handoff_request = HandoffRequest(
        chat_id=42, project_path=str(proj), label="p", away=False
    )
    poller.offset = 5

    await poller._do_handoff()

    assert poller.session_state == STATE_IDLE
    assert engine.created == []  # never created a competing session
    texts = [m["text"] for m in fake.sent_messages]
    assert HANDOFF_STALE_RECOVER_MSG not in texts  # no self-heal attempted
    assert texts[-1] == ALREADY_LIVE_MSG.format(profile="default")


# ---- RECLAIM -----------------------------------------------------------------


async def test_reclaim_kills_engine_session(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    # RECLAIM must engine.kill(profile) so a stale session can't linger (bug 1).
    write_profile(abs_home, allow_ids=[42])
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine)
    poller.session_state = STATE_RECLAIM
    poller._handoff_chat_id = 42

    async def rec(_d: float) -> None:
        pass

    await poller.reclaim(sleep=rec)

    assert "default" in engine.kills  # the leftover was torn down
    assert poller.session_state == STATE_IDLE


# ---- RECLAIM (session death → reclaim sequence) ------------------------------


async def test_reclaim_on_session_death(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "p"
    proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine)

    # drive to SESSION_LIVE
    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:p:0", from_id=42, chat_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:m:n", from_id=42, chat_id=42)
    await poller.poll_once()
    assert poller.session_state == STATE_SESSION_LIVE

    # session comes alive, then dies
    assert await poller.watch_once() is True  # alive → seen
    engine.kill("default")
    assert await poller.watch_once() is False  # dead → RECLAIM
    assert poller.session_state == STATE_RECLAIM

    sleeps: list[float] = []

    async def rec(d: float) -> None:
        sleeps.append(d)

    await poller.reclaim(sleep=rec)

    assert sleeps[0] == 5.0  # reclaim grace delay
    assert poller.session_state == STATE_IDLE
    assert any(m["text"] == SESSION_ENDED_MSG for m in fake.sent_messages)
    # marker cleared
    assert not (abs_home / "profiles" / "default" / "daemon-handoff.json").exists()

    # polling resumes: a pooled message lands after reclaim
    fake.queue_message("i'm back", from_id=42)
    await poller.poll_once()
    assert poller.pool.count() == 1


async def test_reclaim_409_backoff(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())
    # enter RECLAIM directly with a known chat
    poller.session_state = STATE_RECLAIM
    poller._handoff_chat_id = 42
    poller._write_handoff_marker("/p", "normal", 42)

    fake.inject_409(2)  # the probe hits 409 twice, then succeeds

    sleeps: list[float] = []

    async def rec(d: float) -> None:
        sleeps.append(d)

    await poller.reclaim(sleep=rec)

    # grace (5), then 2, 4 backoff, then the probe succeeds.
    assert sleeps == [5.0, 2.0, 4.0]
    assert poller.session_state == STATE_IDLE
    assert any(m["text"] == SESSION_ENDED_MSG for m in fake.sent_messages)


async def test_watch_never_alive_reclaims_after_grace(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    # A launch that never comes alive is reclaimed after session_start_grace_s.
    write_profile(abs_home, allow_ids=[42])
    now = [100.0]
    poller = make_poller(
        abs_home, client_factory, engine=FakeEngine(),
        clock=lambda: now[0], session_start_grace_s=30.0,
    )
    poller.session_state = STATE_SESSION_LIVE
    poller._handoff_at = now[0]
    poller._session_seen_alive = False

    assert await poller.watch_once() is True  # still starting
    now[0] += 31  # past the startup grace, still never alive
    assert await poller.watch_once() is False
    assert poller.session_state == STATE_RECLAIM


# ---- stale handoff marker ----------------------------------------------------


async def test_stale_marker_cleared_when_no_live_session(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())
    poller._write_handoff_marker("/p", "normal", 42)
    marker = abs_home / "profiles" / "default" / "daemon-handoff.json"
    assert marker.exists()

    await poller.check_stale_handoff()
    assert not marker.exists()


async def test_stale_marker_kept_when_session_live(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42], session_pid=os.getpid())
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())
    poller._write_handoff_marker("/p", "normal", 42)
    marker = abs_home / "profiles" / "default" / "daemon-handoff.json"

    await poller.check_stale_handoff()
    assert marker.exists()  # a live session owns it
