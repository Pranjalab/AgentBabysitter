"""Tests FOR the fake-claude harness (PLAN.md 0.3 requires the fake be tested).

Proves each documented mode behaves as specified: normal stays alive and writes
a session.pid-compatible PID then exits cleanly on SIGTERM; not-logged-in exits 1
quickly with an auth-style stderr message; crash-after-N exits non-zero after N
seconds.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

FAKE_CLAUDE = Path(__file__).parent / "harness" / "fake-claude"


def _child_pids(parent_pid: int) -> list[int]:
    """PIDs whose parent is ``parent_pid`` (Linux /proc). Robust to a comm
    containing spaces/parens by splitting after the final ')'."""
    kids: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        try:
            after_comm = stat.rsplit(")", 1)[1].split()
            ppid = int(after_comm[1])  # fields after comm: state, ppid, ...
        except (IndexError, ValueError):
            continue
        if ppid == parent_pid:
            kids.append(int(entry.name))
    return kids


def _comm(pid: int) -> str:
    try:
        return (Path("/proc") / str(pid) / "comm").read_text().strip()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return ""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_harness_script_is_executable() -> None:
    assert FAKE_CLAUDE.exists()
    assert os.access(FAKE_CLAUDE, os.X_OK), "fake-claude must be executable"


def test_normal_mode_stays_alive_writes_pid_and_exits_on_term(tmp_path: Path) -> None:
    pid_file = tmp_path / "session.pid"
    proc = subprocess.Popen(
        [str(FAKE_CLAUDE), "--mode", "normal", "--pid-file", str(pid_file)]
    )
    try:
        # PID file appears and holds this process's PID.
        deadline = time.time() + 3
        while time.time() < deadline and not pid_file.exists():
            time.sleep(0.02)
        assert pid_file.exists(), "normal mode must write the PID file"
        recorded = int(pid_file.read_text().strip())
        assert recorded == proc.pid

        # It stays alive (does not exit on its own).
        time.sleep(0.3)
        assert proc.poll() is None, "normal mode must stay alive until signalled"
    finally:
        proc.terminate()
        rc = proc.wait(timeout=5)
    # Clean exit on SIGTERM and the PID file is cleaned up.
    assert rc == 0
    assert not pid_file.exists(), "normal mode should remove its PID file on exit"


def test_not_logged_in_exits_one_quickly_with_auth_message(tmp_path: Path) -> None:
    pid_file = tmp_path / "session.pid"
    start = time.time()
    proc = subprocess.run(
        [str(FAKE_CLAUDE), "--mode", "not-logged-in", "--pid-file", str(pid_file)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    elapsed = time.time() - start
    assert proc.returncode == 1
    assert elapsed < 5, "not-logged-in must exit within 5s (PLAN.md 1.6 window)"
    assert "login" in proc.stderr.lower()


def test_crash_after_n_exits_nonzero_after_delay(tmp_path: Path) -> None:
    pid_file = tmp_path / "session.pid"
    start = time.time()
    proc = subprocess.run(
        [str(FAKE_CLAUDE), "--mode", "crash-after-1", "--pid-file", str(pid_file)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    elapsed = time.time() - start
    assert proc.returncode != 0
    assert elapsed >= 1.0, "crash-after-1 must run at least ~1s before crashing"
    assert elapsed < 4.0


def test_crash_after_via_env_var(tmp_path: Path) -> None:
    env = {**os.environ, "FAKE_CLAUDE_MODE": "crash-after", "FAKE_CLAUDE_CRASH_AFTER": "1"}
    start = time.time()
    proc = subprocess.run(
        [str(FAKE_CLAUDE)], capture_output=True, text=True, timeout=10, env=env
    )
    assert proc.returncode != 0
    assert time.time() - start >= 1.0


def test_ignores_unknown_claude_style_args(tmp_path: Path) -> None:
    """The engine invokes fake-claude where it would invoke the launcher, so
    trailing claude-style args must be ignored, not error."""
    pid_file = tmp_path / "session.pid"
    proc = subprocess.Popen(
        [
            str(FAKE_CLAUDE),
            "--profile", "work", "--daemon-start", "--away",
            "--mode", "normal", "--pid-file", str(pid_file),
        ]
    )
    try:
        deadline = time.time() + 3
        while time.time() < deadline and not pid_file.exists():
            time.sleep(0.02)
        assert pid_file.exists()
        assert proc.poll() is None
    finally:
        proc.send_signal(signal.SIGINT)
        rc = proc.wait(timeout=5)
    assert rc == 0


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="requires Linux /proc")
def test_normal_mode_reaps_sleep_child_on_term_no_orphan(tmp_path: Path) -> None:
    """Regression (docs/v3/critique/0.2.md): normal mode backgrounds a `sleep`
    child so its TERM/INT trap can fire immediately. A parent-only SIGTERM —
    what ``subprocess.terminate()`` sends: the PID, NOT the whole process group —
    must still reap that child, not orphan it to init. The Step 0.3 harness
    leaked six such sleeps precisely because cleanup did not kill the child."""
    pid_file = tmp_path / "session.pid"
    proc = subprocess.Popen(
        [str(FAKE_CLAUDE), "--mode", "normal", "--pid-file", str(pid_file)]
    )
    try:
        # Wait for the backgrounded sleep child to appear.
        deadline = time.time() + 3
        sleep_child: int | None = None
        while time.time() < deadline:
            for c in _child_pids(proc.pid):
                if _comm(c) == "sleep":
                    sleep_child = c
                    break
            if sleep_child is not None:
                break
            time.sleep(0.02)
        assert sleep_child is not None, "normal mode should spawn a sleep child"
    finally:
        # SIGTERM to the fake-claude PID only (not the group) — the orphan case.
        proc.terminate()
        rc = proc.wait(timeout=5)
    assert rc == 0, "normal mode must exit cleanly on SIGTERM"
    # The specific sleep child must die, not get reparented to init and linger.
    deadline = time.time() + 3
    while time.time() < deadline and _pid_alive(sleep_child):
        time.sleep(0.02)
    assert not _pid_alive(sleep_child), (
        f"sleep child {sleep_child} survived parent SIGTERM — orphaned to init"
    )
