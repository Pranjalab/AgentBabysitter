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
