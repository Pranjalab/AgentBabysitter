"""HANDOFF through a REAL engine (tmux/herdr) with a stub launcher.

Proves the daemon's launch path (create/liveness/kill) works on both engines
(D4), driving the full ABS START flow against FakeTelegram but letting a real
TmuxEngine / HerdrEngine actually create the session. The launched command is the
stub launcher (stands in for abs.sh --daemon-start): it writes session.pid and
stays alive — no claude, no network. Skips cleanly where the engine is absent.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from absd.config import DaemonConfig
from absd.daemon import STATE_SESSION_LIVE, Poller
from absd.engines import HerdrEngine, TmuxEngine
from absd.profiles import discover
from absd.registry import Registry
from tests.conftest import write_profile
from tests.harness.fake_telegram import FakeTelegram

STUB = Path(__file__).parent / "harness" / "stub-launcher"
_TMUX = "tmux"
_HERDR = shutil.which("herdr") or os.path.expanduser("~/.local/bin/herdr")


def _bin_ok(argv: list[str]) -> bool:
    try:
        return subprocess.run(argv, capture_output=True, timeout=10).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _wait_until(pred: Callable[[], bool], timeout: float = 8.0, interval: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return pred()


def _tmux_teardown(sock: str) -> None:
    subprocess.run([_TMUX, "-L", sock, "kill-server"], capture_output=True, timeout=10)
    tmpdir = os.environ.get("TMUX_TMPDIR") or "/tmp"
    with contextlib.suppress(OSError):
        os.unlink(os.path.join(tmpdir, f"tmux-{os.getuid()}", sock))


def _herdr_teardown(prefix: str) -> None:
    import json as _json

    proc = subprocess.run(
        [_HERDR, "session", "list", "--json"], capture_output=True, text=True, timeout=20
    )
    try:
        data = _json.loads(proc.stdout or "{}")
    except (ValueError, _json.JSONDecodeError):
        return
    for s in data.get("sessions", []) or []:
        name = s.get("name") if isinstance(s, dict) else None
        if not isinstance(name, str) or not name.startswith(prefix):
            continue
        env = {**os.environ, "HERDR_SESSION": name}
        subprocess.run([_HERDR, "pane", "close", "w1:p1"], capture_output=True, timeout=20, env=env)
        subprocess.run([_HERDR, "session", "stop", name], capture_output=True, timeout=20, env=env)
        subprocess.run([_HERDR, "session", "delete", name], capture_output=True, timeout=20, env=env)


@pytest.fixture(params=["tmux", "herdr"])
def engine(request: pytest.FixtureRequest) -> Iterator[object]:
    backend = request.param
    if backend == "tmux":
        if not _bin_ok([_TMUX, "-V"]):
            pytest.skip("tmux not installed")
        sock = f"abs-test-{uuid.uuid4().hex[:8]}"
        try:
            yield TmuxEngine(socket_name=sock)
        finally:
            _tmux_teardown(sock)
    else:
        if not _bin_ok([_HERDR, "--version"]):
            pytest.skip("herdr not installed")
        prefix = f"abs-test-{uuid.uuid4().hex[:8]}-"
        try:
            yield HerdrEngine(session_prefix=prefix)
        finally:
            _herdr_teardown(prefix)


async def test_handoff_launches_real_session_then_reclaim_detects_death(
    engine, abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "proj"
    proj.mkdir()
    Registry(abs_home / "daemon" / "registry.json").add(proj)

    prof = discover(abs_home, home=abs_home)[0]
    client = client_factory(prof.load_token())
    cfg = DaemonConfig(poll_timeout_s=0, workspace_root="")
    poller = Poller(
        prof, client, cfg, state_dir=abs_home / "daemon",
        engine=engine, script_path=str(STUB),
    )
    # keep engine session names unique per run so we can find the created one.
    profile_name = prof.name

    # Drive the flow to HANDOFF.
    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:p:0", from_id=42, chat_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:m:n", from_id=42, chat_id=42)
    await poller.poll_once()

    assert poller.session_state == STATE_SESSION_LIVE

    try:
        # The REAL engine created a live session running the stub launcher.
        assert _wait_until(lambda: engine.is_alive(profile_name)), "session should be alive"
        # The stub wrote session.pid under ABS_HOME → both liveness signals agree.
        assert _wait_until(lambda: prof.live_session_pid() is not None)
        assert poller._session_dead() is False

        # Kill it → both signals go dead (reconciled), so _session_dead() is True.
        engine.kill(profile_name)
        assert _wait_until(lambda: poller._session_dead()), "death must reconcile both signals"
    finally:
        engine.kill(profile_name)


async def test_reclaim_tears_down_leftover_engine_session(
    engine, abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    # BUG 1: when the launched command exits, a herdr session's pane shell survives
    # and the session stays "running" — so the NEXT handoff fails "already running".
    # RECLAIM must engine.kill() it. Proven on BOTH engines with the stub launcher.
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "proj"
    proj.mkdir()
    Registry(abs_home / "daemon" / "registry.json").add(proj)

    prof = discover(abs_home, home=abs_home)[0]
    client = client_factory(prof.load_token())
    cfg = DaemonConfig(poll_timeout_s=0, workspace_root="")
    poller = Poller(
        prof, client, cfg, state_dir=abs_home / "daemon",
        engine=engine, script_path=str(STUB),
    )
    name = prof.name

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:p:0", from_id=42, chat_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:m:n", from_id=42, chat_id=42)
    await poller.poll_once()
    assert poller.session_state == STATE_SESSION_LIVE

    try:
        assert _wait_until(lambda: engine.is_alive(name))
        assert await poller.watch_once() is True  # observed alive
        # Simulate the launched command (claude) exiting on its own.
        pid = prof.live_session_pid()
        assert pid is not None
        os.kill(pid, signal.SIGTERM)
        assert _wait_until(lambda: poller._session_dead())
        assert await poller.watch_once() is False  # → RECLAIM

        async def _rec(_d: float) -> None:
            pass

        await poller.reclaim(sleep=_rec)

        # RECLAIM killed the engine session, so the profile no longer lingers and
        # a fresh create for the same profile succeeds (the next-handoff case).
        assert _wait_until(
            lambda: name not in {s.profile for s in engine.list_sessions()}
        ), "reclaim must remove the leftover engine session"
        engine.create_session(
            name, proj, [str(STUB), "--profile", name, "--daemon-start"],
            {"ABS_HOME": str(abs_home)},
        )
        assert _wait_until(lambda: engine.is_alive(name)), "next start must succeed"
    finally:
        engine.kill(name)


def test_herdr_liveness_targets_recorded_pane_not_first(tmp_path: Path) -> None:
    # FIX B: an attach that resurrects a session and opens a SECOND workspace/pane
    # (bare $HOME shell) must NOT be mistaken for the session. is_alive targeted at
    # the RECORDED pane (w1:p1) stays True while claude runs there, and goes False
    # only when THAT pane's command dies — regardless of the extra pane.
    if not _bin_ok([_HERDR, "--version"]):
        pytest.skip("herdr not installed")
    prefix = f"abs-test-{uuid.uuid4().hex[:8]}-"
    engine = HerdrEngine(session_prefix=prefix)
    abs_home = tmp_path / "abs"
    (abs_home / "profiles" / "work").mkdir(parents=True)
    proj = tmp_path / "proj"; proj.mkdir()
    name = prefix + "work"
    try:
        handle = engine.create_session(
            "work", proj,
            [str(STUB), "--profile", "work", "--daemon-start"],
            {"ABS_HOME": str(abs_home)},
        )
        assert handle.pane_id == "w1:p1"
        assert _wait_until(lambda: engine.is_alive("work", pane_id="w1:p1")), "claude pane alive"

        # Simulate the attach resurrection: a second workspace with a bare shell.
        subprocess.run(
            [_HERDR, "workspace", "create", "--cwd", str(tmp_path), "--no-focus"],
            capture_output=True, text=True, timeout=20,
            env={**os.environ, "HERDR_SESSION": name},
        )
        # Targeted liveness is UNCONFUSED by the extra pane.
        assert engine.is_alive("work", pane_id="w1:p1") is True

        # Kill ONLY the recorded pane's command (the stub wrote its pid).
        pid = int((abs_home / "profiles" / "work" / "session.pid").read_text().strip())
        os.kill(pid, signal.SIGTERM)
        # Recorded pane is command-dead even though the w2 shell still exists.
        assert _wait_until(
            lambda: engine.is_alive("work", pane_id="w1:p1") is False
        ), "recorded pane must read dead once its command exits"
    finally:
        _herdr_teardown(prefix)
