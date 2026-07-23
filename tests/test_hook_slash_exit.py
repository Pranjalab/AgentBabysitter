"""abs.sh session-side /abs_exit slash alias (Step 2.2c).

Drives the real ``__silent-hook`` path with a crafted UserPromptSubmit payload and
asserts ``/abs_exit`` triggers the same directive as ``ABS EXIT`` — and that other
kill-ladder phrases stay text-only (no slash alias). Fully fixtured: temp
ABS_HOME/HOME, scrubbed ABS_/TELEGRAM_ env (never touch the live session), real jq.
No claude, no network (the profile sets no_ack so the hook's reaction call is
skipped).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import write_profile

ABS_SH = Path(__file__).resolve().parents[1] / "abs.sh"


def _env(abs_home: Path, home: Path) -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("ABS_") or key.startswith("TELEGRAM_") or key == "CLAUDERC_HOME":
            env.pop(key, None)
    env["HOME"] = str(home)
    env["ABS_HOME"] = str(abs_home)
    return env


def _no_ack_profile(abs_home: Path) -> None:
    write_profile(abs_home, "default", allow_ids=[42])
    rc_path = abs_home / "profiles" / "default" / "rc.json"
    rc = json.loads(rc_path.read_text())
    rc["no_ack"] = True  # skip the hook's Telegram reaction (no network)
    rc["no_log"] = True
    rc_path.write_text(json.dumps(rc))


def _run_hook(abs_home: Path, home: Path, prompt: str) -> subprocess.CompletedProcess:
    payload = json.dumps(
        {"hook_event_name": "UserPromptSubmit", "prompt": prompt, "session_id": "s1"}
    )
    return subprocess.run(
        ["bash", str(ABS_SH), "--profile", "default", "__silent-hook"],
        input=payload,
        env=_env(abs_home, home),
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(home),
    )


def _channel(inner: str) -> str:
    return f'<channel source="plugin:telegram" chat_id="42" message_id="5">{inner}</channel>'


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq required")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_slash_abs_exit_triggers_exit_directive(tmp_path: Path) -> None:
    home = tmp_path / "home"; home.mkdir()
    abs_home = tmp_path / "abs"
    _no_ack_profile(abs_home)

    proc = _run_hook(abs_home, home, _channel("/abs_exit"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # the ABS EXIT directive is printed to stdout (Claude Code injects it)
    assert "abs --profile default exit" in proc.stdout


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq required")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_slash_abs_exit_with_botname(tmp_path: Path) -> None:
    home = tmp_path / "home"; home.mkdir()
    abs_home = tmp_path / "abs"
    _no_ack_profile(abs_home)

    proc = _run_hook(abs_home, home, _channel("/abs_exit@mybot"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "abs --profile default exit" in proc.stdout


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq required")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_other_kill_phrases_have_no_slash_alias(tmp_path: Path) -> None:
    # /abs_stop is NOT an alias (only /abs_exit is session-side) → no directive.
    home = tmp_path / "home"; home.mkdir()
    abs_home = tmp_path / "abs"
    _no_ack_profile(abs_home)

    proc = _run_hook(abs_home, home, _channel("/abs_stop"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "abs --profile default exit" not in proc.stdout
    assert "STOP requested" not in proc.stdout  # /abs_stop is not ABS STOP either


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq required")
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_plain_abs_exit_still_works(tmp_path: Path) -> None:
    # regression: the text phrase ABS EXIT still triggers the directive.
    home = tmp_path / "home"; home.mkdir()
    abs_home = tmp_path / "abs"
    _no_ack_profile(abs_home)

    proc = _run_hook(abs_home, home, _channel("ABS EXIT"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "abs --profile default exit" in proc.stdout
