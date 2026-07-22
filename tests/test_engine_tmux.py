"""Tests for the tmux engine backend (PLAN.md Step 1.1).

Two tiers:
  - Pure-function / no-tmux tests: output parsing, the attach-command string, the
    factory, table formatting. These run everywhere.
  - Integration tests against REAL tmux on a throwaway ``-L abs-test-<random>``
    socket. Every test kills its whole server in teardown (even on failure) and
    never touches the user's default socket or any real ``abs-*`` session. They
    skip cleanly with a visible reason when tmux is absent (PLAN.md section 10).
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest

from absd.engines import TmuxEngine, get_engine
from absd.engines.base import Engine, SessionInfo
from absd.engines.cli import format_sessions_table
from absd.engines.tmux import (
    EngineError,
    PaneRecord,
    parse_pane_records,
    sessions_from_records,
)

FAKE_CLAUDE = Path(__file__).parent / "harness" / "fake-claude"
_TMUX = "tmux"


def _tmux_available() -> bool:
    try:
        return (
            subprocess.run(
                [_TMUX, "-V"], capture_output=True, timeout=10
            ).returncode
            == 0
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


requires_tmux = pytest.mark.skipif(
    not _tmux_available(), reason="tmux not installed"
)


def _wait_until(pred: Callable[[], bool], timeout: float = 6.0, interval: float = 0.05) -> bool:
    """Poll ``pred`` until true or timeout. No long fixed sleeps in tests."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return pred()


# --------------------------------------------------------------------------- #
# Pure-function tests (no tmux needed)
# --------------------------------------------------------------------------- #


def test_parse_pane_records_basic() -> None:
    out = (
        "abs-work\t/home/x/proj\t4321\t0\n"
        "abs-other\t/tmp/o\t4322\t1\n"
    )
    recs = parse_pane_records(out)
    assert recs == [
        PaneRecord(session="abs-work", cwd="/home/x/proj", pid=4321, dead=False),
        PaneRecord(session="abs-other", cwd="/tmp/o", pid=4322, dead=True),
    ]


def test_parse_pane_records_skips_blank_and_malformed() -> None:
    out = "\n" "abs-a\t/p\t1\t0\n" "garbage-line-no-tabs\n" "\t\t\n"
    recs = parse_pane_records(out)
    # Only the well-formed abs-a row survives; the 3-empty-field row has 3 parts
    # (< 4) after split? "\t\t" -> ['', '', ''] = 3 parts -> skipped.
    assert len(recs) == 1
    assert recs[0].session == "abs-a"


def test_parse_pane_records_nonint_pid_becomes_none() -> None:
    recs = parse_pane_records("abs-a\t/p\tNOTPID\t0\n")
    assert recs[0].pid is None


def test_sessions_from_records_filters_non_abs_and_folds() -> None:
    recs = [
        PaneRecord(session="abs-work", cwd="/w", pid=10, dead=False),
        PaneRecord(session="scratch", cwd="/s", pid=11, dead=False),  # ignored
        PaneRecord(session="abs-aaa", cwd="/a", pid=12, dead=True),
    ]
    infos = sessions_from_records(recs)
    # sorted by profile: aaa before work
    assert [i.profile for i in infos] == ["aaa", "work"]
    aaa, work = infos
    assert aaa == SessionInfo(profile="aaa", name="abs-aaa", alive=False, cwd="/a", pid=12)
    assert work == SessionInfo(profile="work", name="abs-work", alive=True, cwd="/w", pid=10)


def test_sessions_from_records_alive_if_any_pane_live() -> None:
    recs = [
        PaneRecord(session="abs-x", cwd="/x", pid=1, dead=True),
        PaneRecord(session="abs-x", cwd="/x", pid=2, dead=False),
    ]
    infos = sessions_from_records(recs)
    assert len(infos) == 1
    assert infos[0].alive is True


def test_attach_command_exact_string() -> None:
    eng = TmuxEngine(socket_name="abs")
    assert eng.attach_command("work") == "tmux -L abs attach -t abs-work"


def test_attach_command_custom_socket() -> None:
    eng = TmuxEngine(socket_name="abs-test-xyz")
    assert eng.attach_command("p1") == "tmux -L abs-test-xyz attach -t abs-p1"


def test_get_engine_factory() -> None:
    assert isinstance(get_engine("tmux"), TmuxEngine)
    assert isinstance(get_engine("auto"), TmuxEngine)  # auto == tmux until 1.2
    with pytest.raises(ValueError):
        get_engine("herdr")  # not until Step 1.2
    with pytest.raises(ValueError):
        get_engine("nope")


def test_tmux_engine_satisfies_protocol() -> None:
    # runtime_checkable structural check — no tmux-specific leak in the protocol.
    assert isinstance(TmuxEngine(), Engine)


def test_format_sessions_table_empty() -> None:
    assert format_sessions_table([]) == "No ABS sessions."


def test_format_sessions_table_rows() -> None:
    table = format_sessions_table(
        [SessionInfo(profile="work", name="abs-work", alive=True, cwd="/w", pid=1)]
    )
    assert "PROFILE" in table and "ALIVE" in table and "CWD" in table
    assert "work" in table and "yes" in table and "/w" in table


# --------------------------------------------------------------------------- #
# Integration tests — real tmux on an isolated throwaway socket
# --------------------------------------------------------------------------- #


@pytest.fixture
def engine() -> "Engine":  # yields a TmuxEngine on a unique socket, tears down server
    sock = f"abs-test-{uuid.uuid4().hex[:8]}"
    eng = TmuxEngine(socket_name=sock)
    try:
        yield eng
    finally:
        # Kill the entire server for this socket — reaps every session/pane even
        # if the test failed mid-way. Never touches the default 'abs' socket.
        subprocess.run(
            [_TMUX, "-L", sock, "kill-server"],
            capture_output=True,
            timeout=10,
        )
        # kill-server stops the process but leaves the socket-file inode behind;
        # unlink it so the test leaves zero trace in /tmp. Best-effort.
        tmpdir = os.environ.get("TMUX_TMPDIR") or "/tmp"
        with contextlib.suppress(OSError):
            os.unlink(os.path.join(tmpdir, f"tmux-{os.getuid()}", sock))


def _create_fake(eng: "TmuxEngine", profile: str, cwd: Path, *, mode: str = "normal",
                 env: dict[str, str] | None = None) -> None:
    eng.create_session(
        profile=profile,
        cwd=cwd,
        command=[str(FAKE_CLAUDE), "--mode", mode],
        env=env or {},
    )


@requires_tmux
def test_available_true_when_tmux_present() -> None:
    assert TmuxEngine().available() is True


@requires_tmux
def test_lifecycle_create_alive_info_kill_gone(engine: "TmuxEngine", tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    _create_fake(engine, "work", cwd)

    assert _wait_until(lambda: engine.is_alive("work")), "session should come alive"

    infos = engine.list_sessions()
    assert len(infos) == 1
    info = infos[0]
    assert info.profile == "work"
    assert info.name == "abs-work"
    assert info.alive is True
    assert info.cwd == str(cwd)
    assert isinstance(info.pid, int) and info.pid > 0

    engine.kill("work")
    assert _wait_until(lambda: not engine.is_alive("work")), "session should die on kill"
    assert engine.list_sessions() == []


@requires_tmux
def test_env_reaches_command_process(engine: "TmuxEngine", tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    pid_file = cwd / "session.pid"
    _create_fake(
        engine, "work", cwd, env={"FAKE_CLAUDE_PID_FILE": str(pid_file)}
    )
    assert _wait_until(pid_file.exists), "fake-claude must write the pid file from env"
    recorded = int(pid_file.read_text().strip())
    # The pid fake-claude wrote ($$) must equal the pane's process pid — proof the
    # env var reached the actual launched command, not just the tmux session env.
    info = engine.list_sessions()[0]
    assert recorded == info.pid


@requires_tmux
def test_two_profiles_independent_kill(engine: "TmuxEngine", tmp_path: Path) -> None:
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
    assert {s.profile for s in engine.list_sessions()} == {"beta"}


@requires_tmux
def test_create_duplicate_raises(engine: "TmuxEngine", tmp_path: Path) -> None:
    cwd = tmp_path / "proj"; cwd.mkdir()
    _create_fake(engine, "work", cwd)
    assert _wait_until(lambda: engine.is_alive("work"))
    with pytest.raises(EngineError):
        _create_fake(engine, "work", cwd)


@requires_tmux
def test_is_alive_false_after_command_exits_on_its_own(engine: "TmuxEngine", tmp_path: Path) -> None:
    cwd = tmp_path / "proj"; cwd.mkdir()
    _create_fake(engine, "crash", cwd, mode="crash-after-1")
    assert _wait_until(lambda: engine.is_alive("crash")), "should be alive right after start"
    # fake-claude exits ~1s later; with no remain-on-exit the session vanishes and
    # is_alive flips false. Poll (no fixed sleep) with a generous budget.
    assert _wait_until(lambda: not engine.is_alive("crash"), timeout=8.0), (
        "is_alive must go false once the launched command exits"
    )
    assert engine.list_sessions() == []


@requires_tmux
def test_kill_is_idempotent(engine: "TmuxEngine") -> None:
    # Killing a non-existent session must not raise (idempotent contract).
    engine.kill("does-not-exist")
