"""Sandbox as a session target (3.2): flow picker, handoff command, pane-only
liveness, container survives session end. Fakes + one gated real-docker test."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from absd.daemon import STATE_IDLE, STATE_RECLAIM, STATE_SESSION_LIVE, Poller
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

    def session_exec_argv(self, name: str, launcher_args: list[str]) -> list[str]:
        return ["docker", "exec", "-it", f"{CONTAINER_PREFIX}{name}", "absd-session", *launcher_args]

    def destroy(self, name: str, purge: bool = False) -> None:
        self.destroyed.append(name)


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
