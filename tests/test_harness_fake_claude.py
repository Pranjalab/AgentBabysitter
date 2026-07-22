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

FAKE_CLAUDE = Path(__file__).parent / "harness" / "fake-claude"


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
