"""Headless checks for install.sh / uninstall.sh (Step 1.8).

Full installs need a human (tty prompts, real deps); here we cover what runs
without one: bash syntax, the command symlink on a checkout install (daemon/voice
skipped for want of a tty), and the uninstall daemon-unit teardown with a stub
systemctl. Never touches the real ~/.abs or the real systemd unit.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / "install.sh"
UNINSTALL = REPO / "uninstall.sh"


def _stub_bin(tmp_path: Path, names: list[str]) -> Path:
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    for n in names:
        p = b / n
        p.write_text("#!/usr/bin/env bash\nexit 0\n")
        p.chmod(0o755)
    return b


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_scripts_are_valid_bash() -> None:
    for script in (INSTALL, UNINSTALL):
        proc = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert proc.returncode == 0, f"{script.name}: {proc.stderr}"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_install_symlinks_command_from_checkout(tmp_path: Path) -> None:
    # deps stubbed so the installer doesn't try to fetch/install anything; no tty
    # so every ask_yes (daemon, herdr, voice) is skipped.
    stub = _stub_bin(tmp_path, ["curl", "claude", "jq", "bun"])
    home = tmp_path / "home"; home.mkdir()
    prefix = tmp_path / "bin"
    env = {
        **os.environ,
        "HOME": str(home),
        "PREFIX": str(prefix),
        "PATH": f"{stub}:{os.environ.get('PATH', '')}",
    }
    proc = subprocess.run(
        ["bash", str(INSTALL)],
        env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    target = prefix / "abs"
    assert target.is_symlink() or target.exists()
    # a checkout install symlinks to the repo's abs.sh
    if target.is_symlink():
        assert os.path.realpath(target) == str((REPO / "abs.sh").resolve())


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_uninstall_tears_down_daemon_unit(tmp_path: Path) -> None:
    home = tmp_path / "home"
    unit_dir = home / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    unit = unit_dir / "absd.service"
    unit.write_text("[Unit]\n")
    # a stub systemctl that records its calls
    stub = _stub_bin(tmp_path, [])
    calls = tmp_path / "systemctl.calls"
    sc = stub / "systemctl"
    sc.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "{calls}"\nexit 0\n')
    sc.chmod(0o755)
    prefix = tmp_path / "bin2"; prefix.mkdir()

    env = {
        **os.environ,
        "HOME": str(home),
        "PREFIX": str(prefix),
        "PATH": f"{stub}:{os.environ.get('PATH', '')}",
    }
    proc = subprocess.run(
        ["bash", str(UNINSTALL), "--keep-state"],
        env=env, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not unit.exists()  # unit removed
    logged = calls.read_text() if calls.exists() else ""
    assert "stop absd.service" in logged
    assert "disable absd.service" in logged


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq required")
def test_doctor_runs_readonly(tmp_path: Path) -> None:
    # `abs doctor` is read-only and exits 0 even against a bare temp ABS_HOME.
    home = tmp_path / "home"; home.mkdir()
    abs_home = tmp_path / "abs"
    (abs_home / "daemon").mkdir(parents=True)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ABS_", "TELEGRAM_"))}
    env.update({"HOME": str(home), "ABS_HOME": str(abs_home)})
    proc = subprocess.run(
        ["bash", str(REPO / "abs.sh"), "doctor"],
        env=env, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout + proc.stderr
    assert "Core dependencies" in out and "Daemon (v3)" in out
