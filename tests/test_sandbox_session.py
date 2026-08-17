"""Sandbox as a session target (3.2): flow picker, handoff command, pane-only
liveness, container survives session end. Fakes + one gated real-docker test."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from absd.daemon import STATE_IDLE, STATE_RECLAIM, STATE_SESSION_LIVE, Poller
from absd.events import END_SANDBOX_CHANNEL_DOWN
from absd.sandbox import CONTAINER_PREFIX, SandboxInfo
from tests.conftest import write_profile
from tests.harness.fake_telegram import FakeTelegram
from tests.test_flow_e2e import FakeEngine, _register, make_poller


class FakeSandbox:
    """A fake SandboxManager for flow/liveness tests (no docker)."""

    name = "fake-sandbox"

    def __init__(self) -> None:
        self._boxes: dict[str, str] = {}  # name -> state
        self.started: list[str] = []
        self.destroyed: list[str] = []
        self.prepared = []
        #: what login_ok() reports — True / False / None (None = probe failed).
        self.login_state: bool | None = True
        self.login_probes: list[str] = []

        #: kill_session() bookkeeping — the in-container half of the teardown.
        self.killed_sessions: list[tuple[str, str]] = []
        self.kill_result = True

    def login_ok(self, name: str) -> bool | None:
        self.login_probes.append(name)
        return self.login_state

    def kill_session(self, name: str, profile: str) -> bool:
        self.killed_sessions.append((name, profile))
        return self.kill_result

    def docker_available(self) -> bool:
        return True

    def image_present(self) -> bool:
        return True

    def list(self) -> list[SandboxInfo]:
        return [
            SandboxInfo(n, f"/sb/{n}", [], "", "absd-sandbox:v2", state=st)
            for n, st in sorted(self._boxes.items())
        ]

    def host_workdir(self, name: str) -> str:
        return f"/sb/{name}"

    def create(self, name: str) -> SandboxInfo:
        self._boxes[name] = "stopped"
        return SandboxInfo(name, f"/sb/{name}", [], "", "absd-sandbox:v2", state="stopped")

    def ensure_running(self, name: str) -> None:
        self.started.append(name)
        self._boxes[name] = "running"

    def prepare_session(self, name: str, profile: str) -> None:
        if self._boxes.get(name) != "running":
            raise AssertionError("prepare_session ran against a box that is not up")
        self.prepared.append((name, profile))

    def session_exec_argv(self, name: str, launcher_args: list[str]) -> list[str]:
        return ["docker", "exec", "-it", f"{CONTAINER_PREFIX}{name}", "absd-session", *launcher_args]

    def destroy(self, name: str, purge: bool = False) -> None:
        self.destroyed.append(name)

    #: In-box Telegram plugin liveness (the deaf-session guard). ``None`` makes the
    #: probe raise, standing in for a docker error.
    channel_up: bool | None = True

    def process_alive(self, name: str, pattern: str) -> bool:
        if self.channel_up is None:
            raise RuntimeError("docker exec failed")
        return bool(self.channel_up)


async def _flow_pick_sandbox(poller, fake, existing: str) -> None:
    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    # project step: the 🏖 Sandbox… entry (last option). Find its index.
    kb = fake.sent_messages[-1]["reply_markup"]["inline_keyboard"]
    sbx_cb = next(r[0]["callback_data"] for r in kb if "Sandbox" in r[0]["text"])
    fake.queue_callback_query(sbx_cb, from_id=42, chat_id=42)
    await poller.poll_once()
    # sandbox step: pick the existing sandbox (as:sb:<i>)
    assert poller.flow.step == "sandbox"
    fake.queue_callback_query("as:sb:0", from_id=42, chat_id=42)
    await poller.poll_once()
    # mode step
    assert poller.flow.step == "mode"
    fake.queue_callback_query("as:m:n", from_id=42, chat_id=42)
    await poller.poll_once()


async def test_flow_offers_sandbox_and_hands_off_docker_exec(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "p"; proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    sbx = FakeSandbox()
    sbx._boxes["web"] = "stopped"
    poller = make_poller(abs_home, client_factory, engine=engine, sandbox_mgr=sbx)

    await _flow_pick_sandbox(poller, fake, "web")

    assert poller.session_state == STATE_SESSION_LIVE
    cmd = engine.created[0]["command"]
    assert cmd == ["docker", "exec", "-it", "absd-sbx-web", "absd-session", "default"]
    assert "web" in sbx.started  # container started before handoff
    # marker records the sandbox name → pane-only liveness
    import json

    marker = json.loads((abs_home / "profiles" / "default" / "daemon-handoff.json").read_text())
    assert marker["sandbox"] == "web"
    assert poller._session_sandbox == "web"
    # v4: ABS + this profile's pairing are synced into the box before the launch —
    # otherwise the in-box launcher falls back to bare claude (no status bar, no
    # guard, no ABS EXIT), or abs.sh refuses with "profile is not paired". The
    # fake asserts the box was already running when we were called; putting this
    # before ensure_running would `docker exec` a stopped container and no-op.
    assert sbx.prepared == [("web", "default")]


async def test_new_sandbox_subflow_creates_then_hands_off(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    engine = FakeEngine()
    sbx = FakeSandbox()  # no existing sandboxes
    poller = make_poller(abs_home, client_factory, engine=engine, sandbox_mgr=sbx, workspace_root="")

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()  # project step (only the 🏖 Sandbox… entry, no projects)
    kb = fake.sent_messages[-1]["reply_markup"]["inline_keyboard"]
    sbx_cb = next(r[0]["callback_data"] for r in kb if "Sandbox" in r[0]["text"])
    fake.queue_callback_query(sbx_cb, from_id=42, chat_id=42)
    await poller.poll_once()  # sandbox step (only ➕ New)
    fake.queue_callback_query("as:sb:new", from_id=42, chat_id=42)
    await poller.poll_once()  # sandbox_name prompt
    assert poller.flow.step == "sandbox_name"
    fake.queue_message("mybox", from_id=42)
    await poller.poll_once()  # created → mode
    assert poller.flow.step == "mode"
    fake.queue_callback_query("as:m:n", from_id=42, chat_id=42)
    await poller.poll_once()

    assert "mybox" in sbx._boxes  # created via the sub-flow
    assert engine.created[0]["command"][3] == "absd-sbx-mybox"


async def test_sandbox_liveness_is_pane_only(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    # A sandbox session ignores host session.pid entirely (container-namespace pid).
    write_profile(abs_home, allow_ids=[42])
    engine = FakeEngine()
    sbx = FakeSandbox()
    poller = make_poller(abs_home, client_factory, engine=engine, sandbox_mgr=sbx)
    poller._session_sandbox = "web"
    poller._session_pane_id = "web:w1:p1"
    engine._alive["default"] = True

    # a live, DIFFERENT host session.pid would trigger foreign-takeover for a normal
    # session — but for a sandbox it's ignored (no clobber across the boundary).
    poller._launched_pid = os.getpid()
    (abs_home / "profiles" / "default" / "session.pid").write_text("123456789\n")  # mismatch
    assert poller._foreign_takeover() is False
    assert poller._session_dead() is False  # pane alive → alive (pid ignored)

    # pane death IS what kills a sandbox session
    engine.kill("default")
    assert poller._session_dead() is True


async def test_sandbox_session_end_reclaims_container_not_destroyed(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "p"; proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    sbx = FakeSandbox()
    sbx._boxes["web"] = "stopped"
    poller = make_poller(abs_home, client_factory, engine=engine, sandbox_mgr=sbx)

    await _flow_pick_sandbox(poller, fake, "web")
    assert poller.session_state == STATE_SESSION_LIVE

    # session comes alive, then the in-container claude exits → pane dies → reclaim
    assert await poller.watch_once() is True
    engine.kill("default")
    assert await poller.watch_once() is False
    assert poller.session_state == STATE_RECLAIM

    async def rec(_d: float) -> None:
        pass

    await poller.reclaim(sleep=rec)
    assert poller.session_state == STATE_IDLE
    assert poller._session_sandbox is None
    assert sbx.destroyed == []  # container SURVIVES the session end (3.2)


async def test_recovery_sandbox_uses_pane_only(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    # On restart, a recovered sandbox session uses pane liveness (not the marker pid).
    import json

    write_profile(abs_home, allow_ids=[42])
    engine = FakeEngine()
    engine._alive["default"] = True
    sbx = FakeSandbox()
    marker = {
        "timestamp": "2026-07-23T00:00:00Z", "project": "/sb/web", "mode": "normal",
        "chat_id": 42, "pane_id": "web:w1:p1", "launcher_pid": 999999999, "sandbox": "web",
    }
    (abs_home / "profiles" / "default" / "daemon-handoff.json").write_text(json.dumps(marker))
    poller = make_poller(abs_home, client_factory, engine=engine, sandbox_mgr=sbx)

    outcome = await poller.boot_recover_and_notify()
    # marker pid is dead, but the pane is alive → recovered SESSION_LIVE via pane
    assert outcome == "session-live"
    assert poller.session_state == STATE_SESSION_LIVE
    assert poller._session_sandbox == "web"


# ---- the deaf-sandbox guard --------------------------------------------------
#
# Live-test finding: a sandbox session's pane only proves the HOST-side `docker exec`
# client is running. The in-box claude can run while its Telegram channel never
# loaded (host-absolute plugin metadata → marketplace "cache-miss"), so the box is
# DEAF: the daemon has stopped polling and every operator message vanishes. These
# pin the guard that reclaims instead of sitting in that black hole.


def _deaf_poller(abs_home: Path, client_factory, *, channel_up, t: list[float]):
    """A SESSION_LIVE sandbox poller with a controllable clock + channel probe."""
    engine = FakeEngine()
    engine._alive["default"] = True
    sbx = FakeSandbox()
    sbx.channel_up = channel_up
    poller = make_poller(
        abs_home, client_factory, engine=engine, sandbox_mgr=sbx,
        clock=lambda: t[0], session_start_grace_s=30.0,
    )
    # These tests drive the clock to 31s, so pin the SANDBOX grace to match.
    # The real default is far larger (a v4 box syncs ABS in and boots claude
    # before its channel exists) — `test_a_sandbox_gets_the_longer_grace` below
    # covers that, and this keeps the deaf-guard tests about the guard.
    poller.cfg.sandbox_start_grace_s = 30.0
    poller._session_sandbox = "web"
    poller._session_pane_id = "web:w1:p1"
    poller._handoff_at = 0.0
    poller.session_state = STATE_SESSION_LIVE
    return poller, engine, sbx


async def test_deaf_sandbox_session_is_reclaimed_after_grace(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    t = [0.0]
    poller, engine, _ = _deaf_poller(abs_home, client_factory, channel_up=False, t=t)
    poller._handoff_chat_id = 42

    # Inside the grace window the box is simply still booting — never punished.
    t[0] = 10.0
    assert await poller.watch_once() is True
    assert poller.session_state == STATE_SESSION_LIVE

    # Past grace with the channel still down: the pane is ALIVE, yet we reclaim,
    # because a live-but-deaf session silently swallows every message.
    t[0] = 31.0
    assert poller._engine_pane_alive() is True
    assert await poller.watch_once() is False
    assert poller.session_state == STATE_RECLAIM
    assert poller._session_end_reason == END_SANDBOX_CHANNEL_DOWN

    async def rec(_d: float) -> None:
        pass

    await poller.reclaim(sleep=rec)
    assert poller.session_state == STATE_IDLE
    # The operator is told what actually happened — not a misleading login hint.
    sent = "\n".join(m["text"] for m in fake.sent_messages)
    assert "Telegram channel never came up" in sent
    assert "login issue" not in sent


async def test_sandbox_channel_seen_latches_and_keeps_session(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    t = [0.0]
    poller, _, sbx = _deaf_poller(abs_home, client_factory, channel_up=True, t=t)

    t[0] = 31.0
    assert await poller.watch_once() is True
    assert poller._sandbox_channel_seen is True

    # Once observed, the check latches: a later probe blip must NOT kill a session
    # the operator is actively using (the pane signal remains the death signal).
    sbx.channel_up = False
    t[0] = 60.0
    assert await poller.watch_once() is True
    assert poller.session_state == STATE_SESSION_LIVE


async def test_channel_probe_error_fails_open(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    # A probe we cannot run must never be grounds for killing a session.
    write_profile(abs_home, allow_ids=[42])
    t = [0.0]
    poller, _, _ = _deaf_poller(abs_home, client_factory, channel_up=None, t=t)
    t[0] = 31.0
    assert poller._sandbox_channel_up() is True
    assert await poller.watch_once() is True
    assert poller.session_state == STATE_SESSION_LIVE


async def test_normal_session_is_never_channel_checked(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    # The guard is sandbox-only: a host session's channel runs in the pane itself.
    write_profile(abs_home, allow_ids=[42])
    t = [0.0]
    poller, _, sbx = _deaf_poller(abs_home, client_factory, channel_up=False, t=t)
    poller._session_sandbox = None          # a normal host session
    poller._launched_pid = os.getpid()
    t[0] = 999.0
    assert poller._sandbox_channel_failed() is False
    assert await poller.watch_once() is True


# ---- gated real-docker integration ------------------------------------------


def _docker_ok() -> bool:
    import subprocess

    try:
        return subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, timeout=10,
        ).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


@pytest.mark.skipif(not _docker_ok(), reason="docker not available")
def test_integration_sandbox_session_pane_liveness(tmp_path: Path) -> None:
    """Real: a fake long-lived process run via `docker exec` through a real engine
    — engine sees the pane alive; killing the in-container process ends the pane
    (is_alive False); the CONTAINER survives. NEVER runs real claude/telegram."""
    import subprocess

    from absd.engines import TmuxEngine
    from absd.sandbox import SandboxManager

    name = f"absd-test-{uuid.uuid4().hex[:8]}"
    container = f"{CONTAINER_PREFIX}{name}"
    sock = f"abs-test-{uuid.uuid4().hex[:8]}"
    mgr = SandboxManager(abs_home=(tmp_path / "abs"), sandbox_root=(tmp_path / "sb"))
    (tmp_path / "abs" / "daemon").mkdir(parents=True)
    fake_claude = tmp_path / "fake-claude"; fake_claude.mkdir()
    (fake_claude / ".credentials.json").write_text("{}")
    engine = TmuxEngine(socket_name=sock)

    try:
        mgr.build()
        mgr.create(name, creds_src=fake_claude)
        mgr.start(name)
        # engine pane runs `docker exec … sleep 300` — a stand-in for the in-container
        # claude session (NOT real claude, no telegram). This mirrors the SHAPE of a
        # sandbox session (docker-exec client on the host = the pane) that
        # session_exec_argv produces, without launching claude. handle identifies it.
        handle = engine.create_session(
            "sbxprof", tmp_path,
            ["docker", "exec", container, "sleep", "300"],
            {},
        )
        import time

        deadline = time.time() + 8
        while time.time() < deadline and not engine.is_alive("sbxprof", pane_id=handle.pane_id):
            time.sleep(0.1)
        assert engine.is_alive("sbxprof", pane_id=handle.pane_id), "pane should be alive"

        # kill the in-container process → the exec client exits → pane command ends
        subprocess.run(["docker", "exec", container, "pkill", "-f", "sleep 300"],
                       capture_output=True, timeout=15)
        deadline = time.time() + 10
        while time.time() < deadline and engine.is_alive("sbxprof", pane_id=handle.pane_id):
            time.sleep(0.2)
        assert not engine.is_alive("sbxprof", pane_id=handle.pane_id), "pane dies when in-container proc ends"

        # the CONTAINER survives the session end
        assert mgr.is_running(name)
    finally:
        engine.kill("sbxprof")
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=30)
        subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True, timeout=10)
        import contextlib

        with contextlib.suppress(OSError):
            os.unlink(os.path.join(os.environ.get("TMUX_TMPDIR") or "/tmp",
                                   f"tmux-{os.getuid()}", sock))


# ---- the box's own login gate ------------------------------------------------
#
# Found in live testing: the operator started a sandbox session, sent it a
# message, and got nothing. The box was up, the plugin had the message — Claude
# inside just wasn't logged in. The host credentials were fine, so the existing
# precheck passed it straight through. Silence is the worst failure mode we
# have; it must announce itself and name the fix.


async def test_a_not_logged_in_box_is_refused_and_says_how_to_fix_it(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "p"; proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    sbx = FakeSandbox()
    sbx._boxes["web"] = "stopped"
    sbx.login_state = False
    poller = make_poller(abs_home, client_factory, engine=engine, sandbox_mgr=sbx)

    await _flow_pick_sandbox(poller, fake, "web")

    assert sbx.login_probes == ["web"]
    assert engine.created == []  # nothing launched — no zombie deaf session
    assert poller.session_state != STATE_SESSION_LIVE
    sent = " ".join(m.get("text", "") for m in fake.sent_messages)
    assert "not logged in" in sent
    # The message has to carry the actual command, or the operator is left to
    # discover it by attaching to the box, which is how this bug was found.
    assert "abs sandbox login web" in sent


async def test_an_unrunnable_probe_does_not_block_the_launch(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    # None means "couldn't tell". Failing closed here would let a docker hiccup
    # refuse a session that would have worked fine — worse than the bug.
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "p"; proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    sbx = FakeSandbox()
    sbx._boxes["web"] = "stopped"
    sbx.login_state = None
    poller = make_poller(abs_home, client_factory, engine=engine, sandbox_mgr=sbx)

    await _flow_pick_sandbox(poller, fake, "web")

    assert sbx.login_probes == ["web"]
    assert poller.session_state == STATE_SESSION_LIVE
    assert engine.created  # launched anyway


async def test_a_host_session_is_never_probed_for_a_box_login(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "p"; proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    sbx = FakeSandbox()
    sbx.login_state = False  # would block, if it were ever consulted
    poller = make_poller(abs_home, client_factory, engine=engine, sandbox_mgr=sbx)

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:p:0", from_id=42, chat_id=42)  # a HOST project
    await poller.poll_once()
    fake.queue_callback_query("as:m:n", from_id=42, chat_id=42)
    await poller.poll_once()

    assert sbx.login_probes == []
    assert poller.session_state == STATE_SESSION_LIVE


# ---- reaping the in-container half of a sandbox session -----------------------
#
# Live-testing symptom: roughly half the operator's messages stopped coming back.
# Cause: the daemon reclaimed a sandbox launch at 30s, and `engine.kill()` only
# closes the host-side `docker exec` client — the claude inside the container and
# its Telegram plugin survive it (verified against a real container). That orphan
# kept polling the bot, and Telegram gives each update to whichever consumer asks
# first, so the next session only saw a random half of them. Nothing errored.


def _sandbox_poller(abs_home: Path, client_factory, *, box: str | None = "web"):
    engine = FakeEngine()
    engine._alive["default"] = True
    sbx = FakeSandbox()
    poller = make_poller(abs_home, client_factory, engine=engine, sandbox_mgr=sbx)
    poller._session_sandbox = box
    poller._session_pane_id = "web:w1:p1"
    poller.session_state = STATE_SESSION_LIVE
    return poller, sbx


async def test_killing_a_sandbox_session_also_reaps_it_inside_the_box(
    abs_home: Path, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    poller, sbx = _sandbox_poller(abs_home, client_factory)

    poller._kill_engine_session()

    # The host engine kill is not enough on its own — the box must be told too.
    assert sbx.killed_sessions == [("web", "default")]


async def test_a_host_session_kill_never_touches_a_box(
    abs_home: Path, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    poller, sbx = _sandbox_poller(abs_home, client_factory, box=None)

    poller._kill_engine_session()

    assert sbx.killed_sessions == []


async def test_a_surviving_in_box_session_is_not_swallowed(
    abs_home: Path, client_factory, caplog
) -> None:
    # If the reap fails, that is precisely the state that corrupts the next
    # session's message delivery, so it must not pass in silence.
    import logging

    write_profile(abs_home, allow_ids=[42])
    poller, sbx = _sandbox_poller(abs_home, client_factory)
    sbx.kill_result = False

    with caplog.at_level(logging.WARNING):
        poller._kill_engine_session()

    assert "may have survived" in caplog.text


async def test_a_sandbox_gets_the_longer_grace(
    abs_home: Path, client_factory
) -> None:
    # The regression that started all of this: at 31s a v4 box is still syncing
    # ABS in and booting claude. Judging it by the host's 30s window declared
    # healthy launches dead.
    write_profile(abs_home, allow_ids=[42])
    poller, _ = _sandbox_poller(abs_home, client_factory)
    assert poller._start_grace() == poller.cfg.sandbox_start_grace_s
    assert poller._start_grace() > 31.0

    poller._session_sandbox = None  # a host session keeps the tight window
    assert poller._start_grace() == poller.cfg.session_start_grace_s
