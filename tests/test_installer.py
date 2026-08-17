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


# ---- the curl path can now deliver the whole thing ---------------------------
#
# `curl … | bash` used to install one file, and one file cannot run v3: the daemon
# is Python living in this repo with its own venv, and sandboxes need the
# Dockerfile. Both are gated on being a checkout, so the headline install could not
# reach the headline feature — it installed a script that then had to explain what
# it was unable to do. The installer offers to clone instead.
#
# Driven with no tty, which is the honest headless case: `ask_yes` cannot open
# /dev/tty, so it returns non-zero and the clone is declined. That is what these
# tests pin — the DECLINE path must keep working exactly as before, because it is
# the fallback for every machine without git and every non-interactive install.

_FAKE_GIT = """#!/usr/bin/env bash
# Records the clone it was asked for, and produces a plausible checkout.
case "${1:-}" in
  clone)
    dst="${@: -1}"
    mkdir -p "$dst"
    printf 'readonly ABS_VERSION="9.9.9"\\n' > "$dst/abs.sh"
    mkdir -p "$dst/absd"
    printf '%s\\n' "$dst" > "$FAKE_GIT_LOG"
    exit 0 ;;
  -C) shift 2; exec true ;;
esac
exit 0
"""


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_a_piped_install_without_a_tty_falls_back_to_the_single_script(tmp_path: Path) -> None:
    """No terminal means no way to ask, and the answer must be the safe one: install
    what the old installer installed rather than cloning into someone's home
    unasked."""
    home = tmp_path / "home"
    home.mkdir()
    bind = _stub_bin(tmp_path, ["claude", "bun", "systemctl"])
    # A `curl` that serves this repo's own abs.sh, so the download path is real.
    curl = bind / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        'out=""\n'
        'while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2 ;; *) shift ;; esac; done\n'
        f'[ -n "$out" ] && cp {REPO / "abs.sh"} "$out"\n'
        "exit 0\n"
    )
    curl.chmod(0o755)
    git_log = tmp_path / "git.log"
    fake_git = bind / "git"
    fake_git.write_text(_FAKE_GIT)
    fake_git.chmod(0o755)

    env = dict(os.environ)
    for k in list(env):
        if k.startswith("ABS_"):
            env.pop(k, None)
    env.update(
        HOME=str(home), PREFIX=str(tmp_path / "bin-out"),
        PATH=f"{bind}:{env.get('PATH', '')}",
        FAKE_GIT_LOG=str(git_log),
        ABS_CLONE_DIR=str(tmp_path / "clone"),
    )
    # Piped in: no BASH_SOURCE file, no tty.
    proc = subprocess.run(
        ["bash", "-c", f"cat {INSTALL} | bash"],
        capture_output=True, text=True, env=env, timeout=120,
        stdin=subprocess.DEVNULL, cwd=str(tmp_path),
    )
    combined = proc.stdout + proc.stderr
    assert (tmp_path / "bin-out" / "abs").exists(), combined
    assert not git_log.exists(), "cloned without being able to ask"
    assert not (tmp_path / "clone").exists(), combined


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_the_clone_prompt_defaults_to_yes(tmp_path: Path) -> None:
    """`ask_yes` used to treat Enter as no, and the clone prompt is written "[Y/n]".
    A capital Y that means no is a lie that hands people the cut-down install."""
    script = tmp_path / "probe.sh"
    body = INSTALL.read_text()
    start = body.index("ask_yes() {")
    end = body.index("\n}", start) + 2
    script.write_text(
        body[:body.index("set -euo pipefail")] + "\n" + body[start:end] + "\n"
        'if ask_yes "clone? [Y/n]" y; then echo YES; else echo NO; fi\n'
        'if ask_yes "other? [y/N]"; then echo YES2; else echo NO2; fi\n'
    )
    # A pty, because ask_yes reads /dev/tty and Enter is the whole point.
    import pty
    pid, fd = pty.fork()
    if pid == 0:
        os.execve("/bin/bash", ["bash", str(script)], dict(os.environ))
    import time
    time.sleep(0.5)
    os.write(fd, b"\r")          # Enter on the [Y/n] question
    time.sleep(0.5)
    os.write(fd, b"\r")          # Enter on the [y/N] question
    time.sleep(0.8)
    out = b""
    try:
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            out += chunk
    except OSError:
        pass
    os.waitpid(pid, 0)
    text = out.decode("utf-8", "replace")
    assert "YES" in text and "NO2" in text, text


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_accepting_the_clone_lands_abs_inside_the_checkout(tmp_path: Path) -> None:
    """The new DEFAULT path for every new user, driven through a pty.

    The decline path is covered above without a terminal. Accepting had never been
    run at all, which is the wrong thing to take on trust about the headline
    install — a broken clone branch would greet everybody who follows the README.

    `git` is stubbed into producing a checkout-shaped directory, so the installer's
    own checkout branch executes for real without reaching GitHub. What is asserted
    is the property that matters: `abs` ends up a symlink INTO the clone, which is
    what makes `git pull` an upgrade path and what the daemon needs to exist at all.
    """
    import pty
    import select
    import time

    base = tmp_path
    (base / "bin").mkdir()
    (base / "home").mkdir()
    (base / "out").mkdir()
    log = base / "git.log"

    git = base / "bin" / "git"
    git.write_text(
        "#!/usr/bin/env bash\n"
        'case "${1:-}" in\n'
        "  clone)\n"
        '    dst="${@: -1}"\n'
        '    mkdir -p "$dst/absd"\n'
        f'    cp {REPO / "abs.sh"} "$dst/abs.sh"\n'
        f'    printf "cloned\\n" >> "{log}"\n'
        "    exit 0 ;;\n"
        "esac\n"
        "exit 0\n"
    )
    git.chmod(0o755)
    for name in ("claude", "bun", "systemctl"):
        p = base / "bin" / name
        p.write_text("#!/usr/bin/env bash\nexit 0\n")
        p.chmod(0o755)

    env = dict(os.environ)
    for k in list(env):
        if k.startswith("ABS_"):
            env.pop(k, None)
    env.update(
        HOME=str(base / "home"), PREFIX=str(base / "out"),
        PATH=f"{base / 'bin'}:{env.get('PATH', '')}",
        ABS_CLONE_DIR=str(base / "clone"),
        ABS_REPO="http://127.0.0.1:1/never",
    )

    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - execs immediately
        os.execve("/bin/bash", ["bash", "-c", f"cat {INSTALL} | bash"], env)

    out = bytearray()
    deadline = time.time() + 60
    answered = 0
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.3)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        out.extend(chunk)
        text = out.decode("utf-8", "replace")
        if answered == 0 and "Clone the repository" in text:
            os.write(fd, b"\r")        # Enter — the documented default
            answered = 1
        elif answered >= 1 and text.count("? [") > answered:
            os.write(fd, b"n\r")       # decline the optional extras
            answered += 1
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass

    transcript = out.decode("utf-8", "replace")
    assert log.exists(), transcript                       # Enter really meant yes
    target = base / "out" / "abs"
    assert target.is_symlink(), transcript
    assert str(base / "clone") in os.readlink(target), transcript
    assert (base / "clone" / "absd").is_dir(), transcript
