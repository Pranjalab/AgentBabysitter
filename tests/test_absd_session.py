"""The in-container launcher `docker/sandbox/absd-session` (Stage 3 argv parsing).

Runs the REAL launcher script directly (no docker) with a stub `claude` on PATH and
a temp restricted-prompt file, and asserts it forwards --model, injects the restricted
system prompt for --restricted, combines both prompts, and passes the rest of the
claude flags through untouched. No real claude, no network, no container.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ABSD_SESSION = Path(__file__).resolve().parents[1] / "docker" / "sandbox" / "absd-session"

# Stub `claude`: record argv NUL-delimited (so a multi-line prompt value survives as
# ONE arg) and exit. Never networks.
_STUB_CLAUDE = """#!/usr/bin/env bash
{ for a in "$@"; do printf '%s\\0' "$a"; done; } > "$ABSD_TEST_ARGV"
exit 0
"""

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")


def _run(tmp_path: Path, args: list[str], prompt_text: str = "RESTRICTED-PROMPT-BODY"):
    bind = tmp_path / "bin"
    bind.mkdir(exist_ok=True)
    stub = bind / "claude"
    stub.write_text(_STUB_CLAUDE)
    stub.chmod(0o755)
    prompt_file = tmp_path / "restricted-prompt.txt"
    prompt_file.write_text(prompt_text)
    argv_file = tmp_path / "argv"

    env = dict(os.environ)
    env["PATH"] = f"{bind}:{env.get('PATH', '')}"
    env["ABSD_TEST_ARGV"] = str(argv_file)
    env["ABSD_RESTRICTED_PROMPT_FILE"] = str(prompt_file)

    proc = subprocess.run(
        ["bash", str(ABSD_SESSION), *args],
        env=env, capture_output=True, text=True, timeout=30, cwd=str(tmp_path),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    raw = argv_file.read_bytes() if argv_file.exists() else b""
    recorded = [p.decode() for p in raw.split(b"\0")]
    # drop the trailing empty after the final NUL
    if recorded and recorded[-1] == "":
        recorded.pop()
    return recorded


def _flag_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


# ---- always wires the channel ------------------------------------------------


def test_forwards_channel(tmp_path: Path) -> None:
    argv = _run(tmp_path, ["myprofile"])
    assert argv[0] == "--channels"
    assert argv[1] == "plugin:telegram@claude-plugins-official"


# ---- --model is forwarded ----------------------------------------------------


def test_model_forwarded(tmp_path: Path) -> None:
    argv = _run(tmp_path, ["p", "--model", "haiku"])
    assert _flag_value(argv, "--model") == "haiku"


# ---- --restricted injects the bundled prompt ---------------------------------


def test_restricted_injects_prompt(tmp_path: Path) -> None:
    argv = _run(tmp_path, ["p", "--restricted"], prompt_text="NO-CODE-RULE-HERE")
    assert "--append-system-prompt" in argv
    assert _flag_value(argv, "--append-system-prompt") == "NO-CODE-RULE-HERE"
    # the marker itself is consumed, never passed to claude
    assert "--restricted" not in argv


def test_restricted_and_model_together(tmp_path: Path) -> None:
    argv = _run(tmp_path, ["p", "--restricted", "--model", "haiku"])
    assert _flag_value(argv, "--model") == "haiku"
    assert "--append-system-prompt" in argv


# ---- --restricted + explicit --append-system-prompt COMBINE ------------------


def test_restricted_combines_with_explicit_append(tmp_path: Path) -> None:
    argv = _run(
        tmp_path,
        ["p", "--restricted", "--append-system-prompt", "EXTRA-CONTEXT"],
        prompt_text="BASE-RESTRICTED",
    )
    combined = _flag_value(argv, "--append-system-prompt")
    assert "BASE-RESTRICTED" in combined
    assert "EXTRA-CONTEXT" in combined


# ---- passthrough claude flags survive, in order ------------------------------


def test_passthrough_flags_and_prompt(tmp_path: Path) -> None:
    argv = _run(
        tmp_path,
        ["p", "--restricted", "--model", "haiku",
         "--permission-mode", "acceptEdits", "--continue",
         "Messages received while you were offline:\nhello 🎉"],
    )
    # claude passthrough flags preserved
    assert "--permission-mode" in argv
    assert _flag_value(argv, "--permission-mode") == "acceptEdits"
    assert "--continue" in argv
    # the initial-prompt positional survives with its newline + emoji intact
    assert "Messages received while you were offline:\nhello 🎉" in argv


# ---- no --restricted, no prompt injected -------------------------------------


def test_no_restricted_no_prompt(tmp_path: Path) -> None:
    argv = _run(tmp_path, ["p", "--continue"])
    assert "--append-system-prompt" not in argv
    assert "--continue" in argv
