"""The ONE test that runs the REAL abs.sh --daemon-start (PLAN.md Step 1.5 item 6).

Proves the flag: parses, dies loudly on an unpaired profile, and (paired) skips
the interactive prompts and reaches the launch — WITHOUT touching a real claude or
the network. Everything is fixtured: a temp ABS_HOME + HOME, a stub `claude`/
`curl`/`bun` on PATH (real jq/bash), and a fake profile written byte-for-byte like
abs.sh writes it (conftest.write_profile).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from tests.conftest import write_profile

ABS_SH = Path(__file__).resolve().parents[1] / "abs.sh"

_STUB_CLAUDE = """#!/usr/bin/env bash
# Stub `claude`: satisfies `plugin list` and records the launch, never networks.
case "${1:-}" in
  plugin) echo "telegram@claude-plugins-official"; exit 0 ;;
esac
for a in "$@"; do
  if [ "$a" = "--channels" ]; then
    [ -n "${FAKE_CLAUDE_LAUNCH_MARKER:-}" ] && printf 'launched\\n' > "$FAKE_CLAUDE_LAUNCH_MARKER"
    exit 0
  fi
done
exit 0
"""

_STUB_NOOP = "#!/usr/bin/env bash\nexit 0\n"


def _write_stub(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


@pytest.fixture
def stub_bin(tmp_path: Path) -> Path:
    """A PATH-front dir with stub claude/curl/bun (real jq/bash stay reachable)."""
    bind = tmp_path / "bin"
    bind.mkdir()
    _write_stub(bind / "claude", _STUB_CLAUDE)
    _write_stub(bind / "curl", _STUB_NOOP)
    _write_stub(bind / "bun", _STUB_NOOP)
    return bind


def _env(abs_home: Path, home: Path, stub_bin: Path, **extra: str) -> dict:
    env = dict(os.environ)
    # Scrub any ABS/Telegram vars inherited from the surrounding (possibly LIVE)
    # session — TELEGRAM_STATE_DIR especially would point abs.sh at the real bot
    # dir and make it see the real poller. Full isolation from the real system.
    for key in list(env):
        if key.startswith("ABS_") or key.startswith("TELEGRAM_") or key == "CLAUDERC_HOME":
            env.pop(key, None)
    env["HOME"] = str(home)
    env["ABS_HOME"] = str(abs_home)
    env["PATH"] = f"{stub_bin}:{env.get('PATH', '')}"
    # Never let a real update check / network sneak in even if a path is missed.
    env["ABS_REPO"] = "http://127.0.0.1:1/never"
    env.update(extra)
    return env


def _no_leftover_launcher() -> None:
    # Best-effort: the backgrounded usage-cache warmup uses the stub claude/curl
    # (instant exit), so nothing should linger. This is a guard, not an assertion.
    time.sleep(0.1)


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq required")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_daemon_start_unpaired_dies_loudly(tmp_path: Path, stub_bin: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    abs_home = tmp_path / "abs"
    (abs_home / "profiles").mkdir(parents=True)  # exists but no paired profile

    proc = subprocess.run(
        ["bash", str(ABS_SH), "--profile", "default", "--daemon-start"],
        env=_env(abs_home, home, stub_bin),
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    _no_leftover_launcher()

    assert proc.returncode != 0, proc.stdout + proc.stderr
    combined = proc.stdout + proc.stderr
    assert "not paired" in combined
    assert "abs --profile default setup" in combined
    # never reached the launch
    assert not (abs_home / "profiles" / "default" / "session.pid").exists()


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq required")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_daemon_start_paired_skips_prompts_and_launches(
    tmp_path: Path, stub_bin: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    abs_home = tmp_path / "abs"
    write_profile(abs_home, "default", allow_ids=[42])
    marker = tmp_path / "launch-marker"

    proc = subprocess.run(
        ["bash", str(ABS_SH), "--profile", "default", "--daemon-start"],
        env=_env(abs_home, home, stub_bin, FAKE_CLAUDE_LAUNCH_MARKER=str(marker)),
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    _no_leftover_launcher()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    # session.pid was written before exec (the daemon's liveness signal).
    assert (abs_home / "profiles" / "default" / "session.pid").exists()
    # reached the launch (exec'd the stub claude with --channels).
    assert marker.exists(), proc.stdout + proc.stderr
    # No interactive update/flood prompt text (they were skipped).
    combined = proc.stdout + proc.stderr
    assert "Update now and relaunch" not in combined
    assert "queued from before this session" not in combined


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq required")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_daemon_start_away_sets_accept_edits(tmp_path: Path, stub_bin: Path) -> None:
    # --away must reach the perm_args (acceptEdits). The stub claude records its
    # argv so we can assert the launcher forwarded --permission-mode acceptEdits.
    home = tmp_path / "home"
    home.mkdir()
    abs_home = tmp_path / "abs"
    write_profile(abs_home, "default", allow_ids=[42])
    argv_dump = tmp_path / "argv"
    stub = stub_bin / "claude"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'case "${1:-}" in plugin) echo "telegram@claude-plugins-official"; exit 0 ;; esac\n'
        'for a in "$@"; do if [ "$a" = "--channels" ]; then printf \'%s\\n\' "$@" > "'
        + str(argv_dump)
        + '"; exit 0; fi; done\nexit 0\n'
    )
    stub.chmod(0o755)

    proc = subprocess.run(
        ["bash", str(ABS_SH), "--profile", "default", "--daemon-start", "--away"],
        env=_env(abs_home, home, stub_bin),
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    _no_leftover_launcher()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert argv_dump.exists(), proc.stdout + proc.stderr
    forwarded = argv_dump.read_text()
    assert "acceptEdits" in forwarded


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq required")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_terminal_launch_records_recent(tmp_path: Path, stub_bin: Path) -> None:
    # A plain terminal launch (no --daemon-start) records the project in recents
    # (resume-first, Step 2.2). Stub claude/curl keep it offline; the cwd is the
    # recorded path.
    import json as _json

    home = tmp_path / "home"
    home.mkdir()
    abs_home = tmp_path / "abs"
    write_profile(abs_home, "default", allow_ids=[42])
    proj = tmp_path / "myproj"
    proj.mkdir()
    marker = tmp_path / "launch-marker"

    proc = subprocess.run(
        ["bash", str(ABS_SH), "--profile", "default"],
        env=_env(abs_home, home, stub_bin, FAKE_CLAUDE_LAUNCH_MARKER=str(marker)),
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(proj),  # the dir claude runs in == the recorded path
    )
    _no_leftover_launcher()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert marker.exists(), proc.stdout + proc.stderr
    recents_file = abs_home / "daemon" / "recents.json"
    assert recents_file.exists(), proc.stdout + proc.stderr
    data = _json.loads(recents_file.read_text())
    assert data["default"][0]["path"] == str(proj.resolve())
    assert data["default"][0]["mode"] == "normal"


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq required")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_terminal_launch_refuses_when_session_live(tmp_path: Path, stub_bin: Path) -> None:
    # FIX A: cmd_run must NOT overwrite a live session's session.pid — that clobber
    # made the daemon reclaim (kill) a live claude. A live session.pid → die with a
    # clear message pointing at attach/exit.
    home = tmp_path / "home"; home.mkdir()
    abs_home = tmp_path / "abs"
    write_profile(abs_home, "default", allow_ids=[42])
    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        (abs_home / "profiles" / "default" / "session.pid").write_text(f"{sleeper.pid}\n")
        proc = subprocess.run(
            ["bash", str(ABS_SH), "--profile", "default"],
            env=_env(abs_home, home, stub_bin),
            capture_output=True, text=True, timeout=30, cwd=str(tmp_path),
        )
        combined = proc.stdout + proc.stderr
        assert proc.returncode != 0, combined
        assert "already has a live session" in combined
        assert "abs attach default" in combined
        # did NOT overwrite the live pid
        assert (abs_home / "profiles" / "default" / "session.pid").read_text().strip() == str(sleeper.pid)
    finally:
        sleeper.terminate()
        sleeper.wait()


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq required")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_terminal_launch_proceeds_past_stale_pid(tmp_path: Path, stub_bin: Path) -> None:
    # A STALE (dead) session.pid must not block the launch — it is replaced.
    home = tmp_path / "home"; home.mkdir()
    abs_home = tmp_path / "abs"
    write_profile(abs_home, "default", allow_ids=[42])
    marker = tmp_path / "launch-marker"
    # a reaped child pid is guaranteed dead
    dead = subprocess.Popen(["true"]); dead.wait()
    (abs_home / "profiles" / "default" / "session.pid").write_text(f"{dead.pid}\n")

    proc = subprocess.run(
        ["bash", str(ABS_SH), "--profile", "default"],
        env=_env(abs_home, home, stub_bin, FAKE_CLAUDE_LAUNCH_MARKER=str(marker)),
        capture_output=True, text=True, timeout=30, cwd=str(tmp_path),
    )
    _no_leftover_launcher()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert marker.exists()  # reached the launch (stale pid did not block)


# ---- terminal resume-first start menu (Step 2.2 terminal) --------------------

_STUB_CLAUDE_DUMP = """#!/usr/bin/env bash
case "${1:-}" in
  plugin) echo "telegram@claude-plugins-official"; exit 0 ;;
esac
for a in "$@"; do
  if [ "$a" = "--channels" ]; then
    [ -n "${FAKE_CLAUDE_CWD_FILE:-}" ] && pwd > "$FAKE_CLAUDE_CWD_FILE"
    [ -n "${FAKE_CLAUDE_ARGV_FILE:-}" ] && printf '%s\\n' "$@" > "$FAKE_CLAUDE_ARGV_FILE"
    exit 0
  fi
done
exit 0
"""


def _dump_stub(stub_bin: Path) -> None:
    (stub_bin / "claude").write_text(_STUB_CLAUDE_DUMP)
    (stub_bin / "claude").chmod(0o755)


def _seed_recent(abs_home: Path, profile: str, path: Path, mode: str = "normal") -> None:
    from absd.recents import Recents

    Recents(abs_home / "daemon" / "recents.json").record(profile, str(path), path.name, mode)


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq required")
def test_non_tty_with_recents_shows_no_menu(tmp_path: Path, stub_bin: Path) -> None:
    # stdin is NOT a tty (subprocess pipe) → the menu never shows, even with recents.
    home = tmp_path / "home"; home.mkdir()
    abs_home = tmp_path / "abs"
    write_profile(abs_home, "default", allow_ids=[42])
    (abs_home / "daemon").mkdir(parents=True, exist_ok=True)
    recent = tmp_path / "recent"; recent.mkdir()
    _seed_recent(abs_home, "default", recent)
    cwd = tmp_path / "here"; cwd.mkdir()
    _dump_stub(stub_bin)
    cwd_file = tmp_path / "cwd"

    proc = subprocess.run(
        ["bash", str(ABS_SH), "--profile", "default"],
        env=_env(abs_home, home, stub_bin, FAKE_CLAUDE_CWD_FILE=str(cwd_file)),
        capture_output=True, text=True, timeout=30, cwd=str(cwd),
    )
    _no_leftover_launcher()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # launched in the CURRENT folder (no menu, no cd to the recent)
    assert cwd_file.read_text().strip() == str(cwd.resolve())


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq required")
def test_new_bypass_launches_fresh_in_cwd(tmp_path: Path, stub_bin: Path) -> None:
    home = tmp_path / "home"; home.mkdir()
    abs_home = tmp_path / "abs"
    write_profile(abs_home, "default", allow_ids=[42])
    (abs_home / "daemon").mkdir(parents=True, exist_ok=True)
    recent = tmp_path / "recent"; recent.mkdir()
    _seed_recent(abs_home, "default", recent)
    cwd = tmp_path / "here"; cwd.mkdir()
    _dump_stub(stub_bin)
    cwd_file = tmp_path / "cwd"
    argv_file = tmp_path / "argv"

    proc = subprocess.run(
        ["bash", str(ABS_SH), "--profile", "default", "--new"],
        env=_env(abs_home, home, stub_bin,
                 FAKE_CLAUDE_CWD_FILE=str(cwd_file), FAKE_CLAUDE_ARGV_FILE=str(argv_file)),
        capture_output=True, text=True, timeout=30, cwd=str(cwd),
    )
    _no_leftover_launcher()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert cwd_file.read_text().strip() == str(cwd.resolve())  # fresh here
    assert "--continue" not in argv_file.read_text()  # not a resume


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq required")
def test_resume_bypass_resumes_top_recent(tmp_path: Path, stub_bin: Path) -> None:
    home = tmp_path / "home"; home.mkdir()
    abs_home = tmp_path / "abs"
    write_profile(abs_home, "default", allow_ids=[42])
    (abs_home / "daemon").mkdir(parents=True, exist_ok=True)
    recent = tmp_path / "research"; recent.mkdir()
    _seed_recent(abs_home, "default", recent, mode="away")
    cwd = tmp_path / "here"; cwd.mkdir()
    _dump_stub(stub_bin)
    cwd_file = tmp_path / "cwd"
    argv_file = tmp_path / "argv"

    proc = subprocess.run(
        ["bash", str(ABS_SH), "--profile", "default", "--resume"],
        env=_env(abs_home, home, stub_bin,
                 FAKE_CLAUDE_CWD_FILE=str(cwd_file), FAKE_CLAUDE_ARGV_FILE=str(argv_file)),
        capture_output=True, text=True, timeout=30, cwd=str(cwd),
    )
    _no_leftover_launcher()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # cd'd to the recorded path and resumed with --continue
    assert cwd_file.read_text().strip() == str(recent.resolve())
    argv = argv_file.read_text()
    assert "--continue" in argv
    assert "acceptEdits" in argv  # recorded mode was away


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq required")
def test_no_recents_no_menu_straight_launch(tmp_path: Path, stub_bin: Path) -> None:
    # No recents → today's behavior exactly (no menu), even were stdin a tty.
    home = tmp_path / "home"; home.mkdir()
    abs_home = tmp_path / "abs"
    write_profile(abs_home, "default", allow_ids=[42])
    cwd = tmp_path / "here"; cwd.mkdir()
    _dump_stub(stub_bin)
    cwd_file = tmp_path / "cwd"
    proc = subprocess.run(
        ["bash", str(ABS_SH), "--profile", "default"],
        env=_env(abs_home, home, stub_bin, FAKE_CLAUDE_CWD_FILE=str(cwd_file)),
        capture_output=True, text=True, timeout=30, cwd=str(cwd),
    )
    _no_leftover_launcher()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert cwd_file.read_text().strip() == str(cwd.resolve())


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq required")
def test_interactive_menu_resume_via_pty(tmp_path: Path, stub_bin: Path) -> None:
    # The real menu: a pty makes `[ -t 0 ]` true so the picker shows; we send "1\n"
    # (resume top recent) and assert the launch cd'd there with --continue.
    import errno
    import os
    import pty
    import select

    home = tmp_path / "home"; home.mkdir()
    abs_home = tmp_path / "abs"
    write_profile(abs_home, "default", allow_ids=[42])
    (abs_home / "daemon").mkdir(parents=True, exist_ok=True)
    recent = tmp_path / "research"; recent.mkdir()
    _seed_recent(abs_home, "default", recent)
    cwd = tmp_path / "here"; cwd.mkdir()
    _dump_stub(stub_bin)
    cwd_file = tmp_path / "cwd"
    argv_file = tmp_path / "argv"
    env = _env(abs_home, home, stub_bin,
               FAKE_CLAUDE_CWD_FILE=str(cwd_file), FAKE_CLAUDE_ARGV_FILE=str(argv_file))

    pid, fd = pty.fork()
    if pid == 0:  # child: exec bash with the pty as its controlling terminal
        try:
            os.chdir(str(cwd))
            os.execvpe("bash", ["bash", str(ABS_SH), "--profile", "default"], env)
        except Exception:
            os._exit(127)
    # parent: answer the prompt, drain output until EOF, reap.
    os.write(fd, b"1\n")
    deadline = time.time() + 25
    out = b""
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 1.0)
        if fd in r:
            try:
                chunk = os.read(fd, 4096)
            except OSError as e:
                if e.errno == errno.EIO:  # pty closed on child exit
                    break
                raise
            if not chunk:
                break
            out += chunk
        if not os.path.exists(str(cwd_file)):
            continue
    _pid, status = os.waitpid(pid, 0)
    _no_leftover_launcher()

    assert cwd_file.exists(), out.decode(errors="replace")
    assert cwd_file.read_text().strip() == str(recent.resolve())
    assert "--continue" in argv_file.read_text()
