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
    MENU_IDLE,
    MENU_SESSION,
    RECENT_GONE_MSG,
    SESSION_ENDED_MSG,
    STATE_IDLE,
    STATE_RECLAIM,
    STATE_SESSION_LIVE,
    Extracted,
    HandoffRequest,
    Poller,
)
from absd.engines.base import EngineError, SessionHandle, SessionInfo
from absd.profiles import discover
from absd.recents import Recents
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

    def create_session(self, profile, cwd, command, env) -> SessionHandle:
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
        return SessionHandle(pane_id=f"{profile}:w1:p1", pid=None)

    def is_alive(self, profile, pane_id=None) -> bool:
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
    events=None,
    creds_path=None,
    sandbox_mgr=None,
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
    # Hermetic login precheck: default to a PRESENT credentials file under the temp
    # ABS_HOME so handoff tests never depend on the real ~/.claude (login-detection
    # tests pass their own missing/empty creds_path).
    if creds_path is None:
        creds_path = abs_home / ".creds.json"
        creds_path.write_text("{}")
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
        events=events,
        creds_path=creds_path,
        sandbox_mgr=sandbox_mgr,
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

    # confirmation + SAFE attach hint only (FIX D: no raw engine attach command —
    # a raw `herdr session attach` in the confirmation resurrected a session and
    # killed a live one). The confirmation ends with the `abs attach` wrapper.
    conf = fake.sent_messages[-1]["text"]
    assert "Started" in conf
    assert conf.rstrip().endswith("abs attach default")
    assert "session attach" not in conf  # no raw engine command

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


# ---- resume-first ABS START (Step 2.2) ---------------------------------------


def _seed_recent(abs_home: Path, path: Path, label: str, mode: str = "normal") -> None:
    Recents(abs_home / "daemon" / "recents.json").record("default", str(path), label, mode)


async def test_abs_start_offers_recents_screen(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "llm"
    proj.mkdir()
    _seed_recent(abs_home, proj, "llm")
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()

    assert poller.flow is not None and poller.flow.step == "recents"
    kb = fake.sent_messages[-1]["reply_markup"]["inline_keyboard"]
    assert kb[0][0]["callback_data"] == "as:r:0"
    assert "Resume llm" in kb[0][0]["text"]
    assert kb[-1][0]["callback_data"] == "as:new"


async def test_resume_one_tap_handoff_with_continue(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "llm"
    proj.mkdir()
    _seed_recent(abs_home, proj, "llm", mode="away")
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine)

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    # ONE tap: resume the recent → straight to handoff (skips project + mode).
    fake.queue_callback_query("as:r:0", from_id=42, chat_id=42)
    await poller.poll_once()

    assert poller.session_state == STATE_SESSION_LIVE
    cmd = engine.created[0]["command"]
    assert "--continue" in cmd  # resume threads --continue to claude
    assert "--away" in cmd  # recorded mode honored
    assert engine.created[0]["cwd"] == str(proj.resolve())


async def test_resume_text_fallback(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "llm"
    proj.mkdir()
    _seed_recent(abs_home, proj, "llm")
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine)

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    fake.queue_message("1", from_id=42)  # numbered resume
    await poller.poll_once()

    assert poller.session_state == STATE_SESSION_LIVE
    assert "--continue" in engine.created[0]["command"]


async def test_new_session_from_recents_opens_picker_no_continue(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    recent = tmp_path / "llm"
    recent.mkdir()
    _seed_recent(abs_home, recent, "llm")
    proj = tmp_path / "web"
    proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine)

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:new", from_id=42, chat_id=42)  # New session
    await poller.poll_once()
    assert poller.flow.step == "project"  # back to the picker
    # pick the registered project + normal mode → fresh launch, NO --continue
    fake.queue_callback_query("as:p:0", from_id=42, chat_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:m:n", from_id=42, chat_id=42)
    await poller.poll_once()
    assert poller.session_state == STATE_SESSION_LIVE
    assert "--continue" not in engine.created[0]["command"]


async def test_resume_dead_path_drops_and_reshows(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    gone = tmp_path / "gone"
    gone.mkdir()
    _seed_recent(abs_home, gone, "gone")
    proj = tmp_path / "web"
    proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine)

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    # delete the recorded folder, then tap its Resume button
    import shutil

    shutil.rmtree(gone)
    fake.queue_callback_query("as:r:0", from_id=42, chat_id=42)
    await poller.poll_once()

    texts = [m["text"] for m in fake.sent_messages]
    assert RECENT_GONE_MSG in texts
    # dropped from recents, and (no recents left) falls through to the picker
    assert Recents(abs_home / "daemon" / "recents.json").list("default") == []
    assert poller.flow.step == "project"
    assert engine.created == []  # nothing launched


async def test_resume_at_cap_refuses(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "llm"
    proj.mkdir()
    _seed_recent(abs_home, proj, "llm")
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine, max_sessions=1)

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()  # recents shown (not yet at cap)
    # another profile takes the only slot before the resume tap
    engine._alive["other"] = True
    fake.queue_callback_query("as:r:0", from_id=42, chat_id=42)
    await poller.poll_once()

    assert poller.flow is None
    assert fake.sent_messages[-1]["text"] == AT_CAP_MSG.format(cap=1)
    assert engine.created == []


async def test_corrupt_recents_falls_to_picker(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    (abs_home / "daemon" / "recents.json").write_text("{ not json ]")
    proj = tmp_path / "web"
    proj.mkdir()
    _register(abs_home, proj)
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    assert poller.flow.step == "project"  # corrupt recents → treated as empty


async def test_handoff_records_recent(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "web"
    proj.mkdir()
    _register(abs_home, proj)
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:p:0", from_id=42, chat_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:m:n", from_id=42, chat_id=42)
    await poller.poll_once()

    recents = Recents(abs_home / "daemon" / "recents.json").list("default")
    assert len(recents) == 1
    assert recents[0].path == str(proj.resolve())
    assert recents[0].mode == "normal"


# ---- Telegram "/" menu (Step 2.2) --------------------------------------------


async def test_run_registers_idle_menu(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42], blocked=True)  # yields, doesn't poll
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())

    async def rec(_d: float) -> None:
        pass

    await poller.run(max_cycles=1, sleep=rec)
    assert fake.commands  # set_my_commands was called
    assert fake.commands[-1] == MENU_IDLE


async def test_menu_debounced_across_restart(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42], blocked=True)

    async def rec(_d: float) -> None:
        pass

    p1 = make_poller(abs_home, client_factory, engine=FakeEngine())
    await p1.run(max_cycles=1, sleep=rec)
    # a "restart": a fresh poller on the same ABS_HOME reads the persisted menu kind
    p2 = make_poller(abs_home, client_factory, engine=FakeEngine())
    await p2.run(max_cycles=1, sleep=rec)
    assert len(fake.commands) == 1  # registered once, not twice (debounce)


async def test_handoff_sets_session_menu_reclaim_restores_idle(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "web"
    proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine)

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:p:0", from_id=42, chat_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:m:n", from_id=42, chat_id=42)
    await poller.poll_once()
    assert MENU_SESSION in fake.commands  # in-session menu on handoff

    # session dies → reclaim restores the idle menu
    assert await poller.watch_once() is True
    engine.kill("default")
    assert await poller.watch_once() is False

    async def rec(_d: float) -> None:
        pass

    await poller.reclaim(sleep=rec)
    assert fake.commands[-1] == MENU_IDLE


# ---- session.pid clobber / foreign-takeover (Step 2.2c FIX C) -----------------


async def test_foreign_takeover_yields_not_reclaims(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    # A terminal launch overwrites session.pid with a different, LIVE pid while our
    # daemon session runs. The daemon must YIELD (not reclaim/kill) — trusting the
    # clobbered pid is what made it kill a live claude. Only when BOTH the foreign
    # pid and our recorded pane die does it reclaim.
    import subprocess

    write_profile(abs_home, allow_ids=[42])
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine)

    ours = subprocess.Popen(["sleep", "60"])
    foreign = subprocess.Popen(["sleep", "60"])
    pid_file = abs_home / "profiles" / "default" / "session.pid"
    try:
        # simulate a live daemon session
        poller.session_state = STATE_SESSION_LIVE
        poller._session_pane_id = "default:w1:p1"
        poller._launched_pid = ours.pid
        poller._session_seen_alive = True
        engine._alive["default"] = True

        # terminal launch clobbers session.pid with a foreign LIVE pid
        pid_file.write_text(f"{foreign.pid}\n")

        assert poller._foreign_takeover() is True
        assert poller._session_dead() is False  # never "dead" during takeover
        assert await poller.watch_once() is True  # yields, stays SESSION_LIVE
        assert poller.session_state == STATE_SESSION_LIVE
        assert engine.kills == []  # did NOT kill the engine session

        # now the foreign session AND our recorded session both end
        foreign.terminate(); foreign.wait()
        ours.terminate(); ours.wait()
        engine._alive["default"] = False

        assert poller._foreign_takeover() is False
        assert poller._session_dead() is True
        assert await poller.watch_once() is False
        assert poller.session_state == STATE_RECLAIM
    finally:
        for p in (ours, foreign):
            if p.poll() is None:
                p.terminate()
                p.wait()


async def test_session_dead_uses_recorded_pane_not_disk_pid(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    # The pid signal is the RECORDED launcher pid, not the shared session.pid file.
    # A dead session.pid on disk must NOT make _session_dead True while our recorded
    # pid + pane are alive.
    import subprocess

    write_profile(abs_home, allow_ids=[42])
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine)
    ours = subprocess.Popen(["sleep", "60"])
    try:
        poller._session_pane_id = "default:w1:p1"
        poller._launched_pid = ours.pid
        engine._alive["default"] = True
        # a stale/foreign-but-DEAD session.pid on disk (e.g. clobbered then exited)
        (abs_home / "profiles" / "default" / "session.pid").write_text("999999999\n")
        assert poller._session_dead() is False  # recorded pane + pid still alive
    finally:
        ours.terminate()
        ours.wait()
