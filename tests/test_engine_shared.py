"""Cross-engine lifecycle tests — the D4 proof (PLAN.md Step 1.2 critique gate).

The SAME test bodies run parameterized over BOTH backends: ``TmuxEngine`` on a
throwaway ``-L abs-test-<random>`` socket, and ``HerdrEngine`` on a throwaway
``abs-test-<random>-`` session prefix. Every parameterized test therefore appears
twice in the run (``[tmux]`` and ``[herdr]``) — that pairing is the "every
feature works identically on both engines" evidence.

Isolation + teardown (even on failure):
  - tmux: kill the throwaway server + unlink its socket file.
  - herdr: for every session under this run's prefix, ``pane close`` +
    ``session stop`` + ``session delete``, then ASSERT no session under the prefix
    is still running (surfaces any leak). Never touches a real ``abs-*`` session
    (each run's prefix is unique) or the herdr ``default`` session.

Both backends skip cleanly with a visible reason when their binary is absent
(PLAN.md section 10 / D4). herdr 0.7.5 is installed here, so the herdr params run;
the skip path exists for CI.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from absd.engines import HerdrEngine, TmuxEngine
from absd.engines.base import Engine, EngineError

FAKE_CLAUDE = Path(__file__).parent / "harness" / "fake-claude"
_TMUX = "tmux"
_HERDR = shutil.which("herdr") or os.path.expanduser("~/.local/bin/herdr")
_CLI_TIMEOUT = 20


def _bin_ok(argv: list[str]) -> bool:
    try:
        return subprocess.run(
            argv, capture_output=True, timeout=10
        ).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _tmux_available() -> bool:
    return _bin_ok([_TMUX, "-V"])


def _herdr_available() -> bool:
    return _bin_ok([_HERDR, "--version"])


def _wait_until(
    pred: Callable[[], bool], timeout: float = 8.0, interval: float = 0.05
) -> bool:
    """Poll ``pred`` until true or timeout. No long fixed sleeps in tests."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return pred()


# --------------------------------------------------------------------------- #
# per-backend teardown helpers
# --------------------------------------------------------------------------- #


def _tmux_teardown(sock: str) -> None:
    subprocess.run(
        [_TMUX, "-L", sock, "kill-server"], capture_output=True, timeout=10
    )
    tmpdir = os.environ.get("TMUX_TMPDIR") or "/tmp"
    with contextlib.suppress(OSError):
        os.unlink(os.path.join(tmpdir, f"tmux-{os.getuid()}", sock))


def _herdr_sessions_with_prefix(prefix: str) -> list[str]:
    proc = subprocess.run(
        [_HERDR, "session", "list", "--json"],
        capture_output=True,
        text=True,
        timeout=_CLI_TIMEOUT,
    )
    if proc.returncode != 0:
        return []
    try:
        data = json.loads(proc.stdout or "{}")
    except (json.JSONDecodeError, ValueError):
        return []
    return [
        s["name"]
        for s in data.get("sessions", []) or []
        if isinstance(s, dict)
        and isinstance(s.get("name"), str)
        and s["name"].startswith(prefix)
    ]


def _herdr_running_with_prefix(prefix: str) -> list[str]:
    proc = subprocess.run(
        [_HERDR, "session", "list", "--json"],
        capture_output=True,
        text=True,
        timeout=_CLI_TIMEOUT,
    )
    if proc.returncode != 0:
        return []
    try:
        data = json.loads(proc.stdout or "{}")
    except (json.JSONDecodeError, ValueError):
        return []
    return [
        s["name"]
        for s in data.get("sessions", []) or []
        if isinstance(s, dict)
        and isinstance(s.get("name"), str)
        and s["name"].startswith(prefix)
        and s.get("running")
    ]


def _herdr_teardown(prefix: str) -> None:
    """Fully tear down every session under ``prefix``, then verify none run.

    Done via herdr's CLI directly (not the code under test) so a bug in the
    engine can't leak a server past the test.
    """
    for name in _herdr_sessions_with_prefix(prefix):
        env = {**os.environ, "HERDR_SESSION": name}
        # pane close kills the pane's whole process group (launcher + children);
        # session stop stops the per-session server; delete clears saved state.
        subprocess.run(
            [_HERDR, "pane", "close", "w1:p1"],
            capture_output=True, timeout=_CLI_TIMEOUT, env=env,
        )
        subprocess.run(
            [_HERDR, "session", "stop", name],
            capture_output=True, timeout=_CLI_TIMEOUT, env=env,
        )
        subprocess.run(
            [_HERDR, "session", "delete", name],
            capture_output=True, timeout=_CLI_TIMEOUT, env=env,
        )
    still = _herdr_running_with_prefix(prefix)
    assert not still, f"herdr sessions still running after teardown: {still}"


# --------------------------------------------------------------------------- #
# the parameterized engine fixture
# --------------------------------------------------------------------------- #


@pytest.fixture(params=["tmux", "herdr"])
def engine(request: pytest.FixtureRequest) -> Iterator[Engine]:
    """Yield an isolated engine per backend; tear its whole world down after."""
    backend = request.param
    if backend == "tmux":
        if not _tmux_available():
            pytest.skip("tmux not installed")
        sock = f"abs-test-{uuid.uuid4().hex[:8]}"
        try:
            yield TmuxEngine(socket_name=sock)
        finally:
            _tmux_teardown(sock)
    else:
        if not _herdr_available():
            pytest.skip("herdr not installed")
        prefix = f"abs-test-{uuid.uuid4().hex[:8]}-"
        try:
            yield HerdrEngine(session_prefix=prefix)
        finally:
            _herdr_teardown(prefix)


def _create_fake(
    engine: Engine,
    profile: str,
    cwd: Path,
    *,
    mode: str = "normal",
    env: dict[str, str] | None = None,
) -> None:
    engine.create_session(
        profile=profile,
        cwd=cwd,
        command=[str(FAKE_CLAUDE), "--mode", mode],
        env=env or {},
    )


def _same_path(a: str | None, b: Path) -> bool:
    return a is not None and os.path.realpath(a) == os.path.realpath(str(b))


# --------------------------------------------------------------------------- #
# lifecycle tests — identical body, both engines (D4)
# --------------------------------------------------------------------------- #


def test_available_true(engine: Engine) -> None:
    assert engine.available() is True


def test_lifecycle_create_alive_info_kill_gone(
    engine: Engine, tmp_path: Path
) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    _create_fake(engine, "work", cwd)

    assert _wait_until(lambda: engine.is_alive("work")), "session should come alive"

    infos = engine.list_sessions()
    assert len(infos) == 1
    info = infos[0]
    assert info.profile == "work"
    assert info.name == engine._session_name("work")  # type: ignore[attr-defined]
    assert info.alive is True
    assert _same_path(info.cwd, cwd), (info.cwd, cwd)
    assert isinstance(info.pid, int) and info.pid > 0

    engine.kill("work")
    assert _wait_until(lambda: not engine.is_alive("work")), "session should die on kill"
    assert _wait_until(lambda: engine.list_sessions() == []), "listing empties after kill"


def test_env_reaches_command_process(engine: Engine, tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    pid_file = cwd / "session.pid"
    _create_fake(engine, "work", cwd, env={"FAKE_CLAUDE_PID_FILE": str(pid_file)})
    assert _wait_until(pid_file.exists), "fake-claude must write the pid file from env"
    recorded = int(pid_file.read_text().strip())
    # The pid fake-claude wrote ($$) must equal the launched command's pid the
    # engine reports — proof the env var reached the actual launched command.
    assert _wait_until(lambda: engine.is_alive("work"))
    info = engine.list_sessions()[0]
    assert recorded == info.pid


def test_two_profiles_independent_kill(engine: Engine, tmp_path: Path) -> None:
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    _create_fake(engine, "alpha", a)
    _create_fake(engine, "beta", b)

    assert _wait_until(lambda: engine.is_alive("alpha") and engine.is_alive("beta"))
    assert {s.profile for s in engine.list_sessions()} == {"alpha", "beta"}

    engine.kill("alpha")
    assert _wait_until(lambda: not engine.is_alive("alpha"))
    # beta is untouched.
    assert engine.is_alive("beta") is True
    assert {s.profile for s in engine.list_sessions() if s.alive} == {"beta"}


def test_create_duplicate_raises(engine: Engine, tmp_path: Path) -> None:
    cwd = tmp_path / "proj"; cwd.mkdir()
    _create_fake(engine, "work", cwd)
    assert _wait_until(lambda: engine.is_alive("work"))
    with pytest.raises(EngineError):
        _create_fake(engine, "work", cwd)


def test_is_alive_false_after_command_exits_on_its_own(
    engine: Engine, tmp_path: Path
) -> None:
    cwd = tmp_path / "proj"; cwd.mkdir()
    # crash-after-2 gives a ~2s alive window — comfortably catchable on both
    # engines (herdr's pane shell needs ~0.7s to foreground the command).
    _create_fake(engine, "crash", cwd, mode="crash-after-2")
    assert _wait_until(lambda: engine.is_alive("crash")), "should be alive right after start"
    # The command self-exits; is_alive must flip false. tmux destroys the session;
    # herdr keeps the pane/session with an idle shell — but is_alive is false for
    # both, and nothing reports alive.
    assert _wait_until(lambda: not engine.is_alive("crash"), timeout=12.0), (
        "is_alive must go false once the launched command exits"
    )
    assert all(not s.alive for s in engine.list_sessions())


def test_kill_is_idempotent(engine: Engine) -> None:
    # Killing a non-existent session must not raise (idempotent contract).
    engine.kill("does-not-exist")
