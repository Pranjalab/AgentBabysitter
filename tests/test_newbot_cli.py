"""`abs start new-bot` orchestration guards, against the REAL abs.sh.

Fully fixtured — temp ABS_HOME/HOME, scrubbed ABS_/TELEGRAM_ env (never touch the
live session), stub claude/curl/bun on PATH (real jq/bash), NO real Telegram and NO
pairing. We only prove the *guards* fire the way they must; the happy path (which
would type a token and pair) is covered by the manual-test doc, not here.

Covered:
  - assert_no_live_session fires when the resolved profile already has a live
    poller (a bot.pid we keep alive) — provisioning refuses before any token entry;
  - the interactive guard: a non-TTY invocation refuses (new-bot needs a terminal);
  - token-not-verifiable aborts cleanly: over a pty we type a well-formed token,
    the stub curl makes getMe return ok:false, and abs.sh dies "Telegram rejected".
"""

from __future__ import annotations

import errno
import os
import select
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from tests.conftest import write_profile

ABS_SH = Path(__file__).resolve().parents[1] / "abs.sh"

# Stub `claude`: satisfies need_deps + `plugin list` (so ensure_plugin sees the
# plugin installed), never networks.
_STUB_CLAUDE = """#!/usr/bin/env bash
case "${1:-}" in
  plugin) echo "telegram@claude-plugins-official"; exit 0 ;;
esac
exit 0
"""
_STUB_NOOP = "#!/usr/bin/env bash\nexit 0\n"
# Stub `curl`: drains the -K config on stdin, then answers every Bot API call with
# ok:false — so getMe verification fails without a single real network call.
_STUB_CURL_REJECT = """#!/usr/bin/env bash
cat >/dev/null 2>&1
printf '{"ok":false,"description":"Unauthorized"}'
"""


def _write_stub(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


@pytest.fixture
def stub_bin(tmp_path: Path) -> Path:
    bind = tmp_path / "bin"
    bind.mkdir()
    _write_stub(bind / "claude", _STUB_CLAUDE)
    _write_stub(bind / "curl", _STUB_NOOP)
    _write_stub(bind / "bun", _STUB_NOOP)
    return bind


def _env(abs_home: Path, home: Path, stub_bin: Path, **extra: str) -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("ABS_") or key.startswith("TELEGRAM_") or key == "CLAUDERC_HOME":
            env.pop(key, None)
    env["HOME"] = str(home)
    env["ABS_HOME"] = str(abs_home)
    env["PATH"] = f"{stub_bin}:{env.get('PATH', '')}"
    env["ABS_REPO"] = "http://127.0.0.1:1/never"
    env.update(extra)
    return env


pytestmark = [
    pytest.mark.skipif(shutil.which("jq") is None, reason="jq required"),
    pytest.mark.skipif(shutil.which("bash") is None, reason="bash required"),
]


# ---- 1. assert_no_live_session fires -----------------------------------------


def test_new_bot_refuses_while_session_live(tmp_path: Path, stub_bin: Path) -> None:
    home = tmp_path / "home"; home.mkdir()
    abs_home = tmp_path / "abs"
    write_profile(abs_home, "default", allow_ids=[42])
    # A LIVE poller on the resolved (default) profile: bot.pid naming this very
    # (alive) test process. profile_live_pid -> assert_no_live_session must die.
    tg_dir = abs_home / "tg" / "default"
    (tg_dir / "bot.pid").write_text(str(os.getpid()))

    proc = subprocess.run(
        ["bash", str(ABS_SH), "start", "new-bot"],
        env=_env(abs_home, home, stub_bin),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(home),
    )

    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, combined
    assert "already has a live poller" in combined
    # Never created a new profile.
    assert list((abs_home / "profiles").glob("*")) == [abs_home / "profiles" / "default"]


# ---- 2. interactive-only guard (non-TTY refuses) -----------------------------


def test_new_bot_refuses_without_tty(tmp_path: Path, stub_bin: Path) -> None:
    home = tmp_path / "home"; home.mkdir()
    abs_home = tmp_path / "abs"
    # No profile, no live session: the guard that fires is the TTY check.
    (abs_home / "profiles").mkdir(parents=True)

    proc = subprocess.run(
        ["bash", str(ABS_SH), "start", "new-bot"],
        env=_env(abs_home, home, stub_bin),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(home),
    )

    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, combined
    assert "interactive" in combined


# ---- 3. token-not-verifiable aborts cleanly (over a pty) ---------------------


def test_new_bot_bad_token_aborts(tmp_path: Path) -> None:
    import pty

    home = tmp_path / "home"; home.mkdir()
    abs_home = tmp_path / "abs"
    (abs_home / "profiles").mkdir(parents=True)  # empty → no relay target
    stub_bin = tmp_path / "bin"; stub_bin.mkdir()
    _write_stub(stub_bin / "claude", _STUB_CLAUDE)
    _write_stub(stub_bin / "bun", _STUB_NOOP)
    _write_stub(stub_bin / "curl", _STUB_CURL_REJECT)  # getMe -> ok:false
    env = _env(abs_home, home, stub_bin)

    pid, fd = pty.fork()
    if pid == 0:  # child: abs.sh with the pty as its controlling terminal
        try:
            os.chdir(str(home))
            os.execvpe("bash", ["bash", str(ABS_SH), "start", "new-bot"], env)
        except Exception:
            os._exit(127)

    # parent: type a WELL-FORMED token once prompted, then drain to EOF.
    token = b"123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789\n"
    out = b""
    sent = False
    deadline = time.time() + 25
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
            if not sent and b"Bot token" in out:
                os.write(fd, token)
                sent = True
        elif not sent:
            # prompt uses -s (no echo) so it may not surface; send once anyway.
            os.write(fd, token)
            sent = True
    os.waitpid(pid, 0)

    text = out.decode(errors="replace")
    assert "Telegram rejected that token" in text, text
    # Never created a profile from the bad token.
    assert list((abs_home / "profiles").glob("*")) == []
