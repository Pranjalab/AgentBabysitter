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


# ---- v4: the session runs THROUGH abs.sh when it is synced into the box ------
#
# Everything above exercises the v3 fallback (no /opt/abs/abs.sh on a test host).
# These drive the v4 path via the ABSD_ABS_SH seam with a stub abs.sh, because that
# is what gives an in-box session the ABS status bar, the Bash guard, the ABS remote
# controls and a session.pid `abs exit` can signal — a bare claude has none of them.


_STUB_ABS = """#!/usr/bin/env bash
{ for a in "$@"; do printf '%s\\0' "$a"; done; } > "$ABSD_TEST_ARGV"
printf '%s' "${ABS_EXTRA_SYSTEM_PROMPT:-}" > "$ABSD_TEST_ARGV.sys"
exit 0
"""


def _run_via_abs(tmp_path: Path, args: list[str], prompt_text: str = "RESTRICTED-BODY"):
    """Run the launcher with a stub abs.sh in place, returning (argv, extra_prompt)."""
    bind = tmp_path / "bin"
    bind.mkdir(exist_ok=True)
    stub_claude = bind / "claude"
    stub_claude.write_text(_STUB_CLAUDE)
    stub_claude.chmod(0o755)
    abs_sh = tmp_path / "abs.sh"
    abs_sh.write_text(_STUB_ABS)
    abs_sh.chmod(0o755)
    prompt_file = tmp_path / "restricted-prompt.txt"
    prompt_file.write_text(prompt_text)
    argv_file = tmp_path / "argv"

    env = dict(os.environ)
    env["PATH"] = f"{bind}:{env.get('PATH', '')}"
    env["ABSD_TEST_ARGV"] = str(argv_file)
    env["ABSD_RESTRICTED_PROMPT_FILE"] = str(prompt_file)
    env["ABSD_ABS_SH"] = str(abs_sh)
    env.pop("ABS_EXTRA_SYSTEM_PROMPT", None)

    proc = subprocess.run(
        ["bash", str(ABSD_SESSION), *args],
        env=env, capture_output=True, text=True, timeout=30, cwd=str(tmp_path),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    raw = argv_file.read_bytes() if argv_file.exists() else b""
    recorded = [p.decode() for p in raw.split(b"\0")]
    if recorded and recorded[-1] == "":
        recorded.pop()
    sysfile = Path(str(argv_file) + ".sys")
    return recorded, (sysfile.read_text() if sysfile.exists() else "")


def test_v4_launches_through_abs_not_claude(tmp_path: Path) -> None:
    argv, _ = _run_via_abs(tmp_path, ["myprofile"])
    # abs.sh owns the launch: it adds --channels/--settings/session.pid itself, so
    # the launcher must NOT also pass the channel (that would be abs.sh's job twice).
    assert argv == ["--profile", "myprofile", "--daemon-start"]
    assert "--channels" not in argv


def test_v4_initial_prompt_becomes_a_flag_not_a_positional(tmp_path: Path) -> None:
    # abs.sh's dispatch reads a bare positional as an unknown COMMAND and dies, so a
    # pooled message MUST be re-emitted as `--prompt <text>`. Getting this wrong kills
    # every pool-forwarding sandbox launch.
    argv, _ = _run_via_abs(tmp_path, ["p", "Messages while offline:\nhello 🎉"])
    assert _flag_value(argv, "--prompt") == "Messages while offline:\nhello 🎉"
    assert argv[-2] == "--prompt"          # last flag, value intact as ONE arg


def test_v4_away_maps_to_the_abs_flag(tmp_path: Path) -> None:
    # acceptEdits IS abs.sh's --away (same effect plus the warning); anything else
    # is passed through for claude.
    argv, _ = _run_via_abs(tmp_path, ["p", "--permission-mode", "acceptEdits"])
    assert "--away" in argv
    assert "--permission-mode" not in argv

    argv2, _ = _run_via_abs(tmp_path, ["p", "--permission-mode", "plan"])
    assert "--away" not in argv2
    assert _flag_value(argv2, "--permission-mode") == "plan"


def test_v4_extra_system_prompt_goes_through_the_env_seam(tmp_path: Path) -> None:
    # abs.sh builds its OWN --append-system-prompt (the ABS operating instructions).
    # A second --append-system-prompt would compete with it, so the restricted persona
    # travels in ABS_EXTRA_SYSTEM_PROMPT and abs.sh merges the two.
    argv, extra = _run_via_abs(
        tmp_path, ["p", "--restricted", "--append-system-prompt", "EXTRA"],
        prompt_text="BASE-RESTRICTED",
    )
    assert "--append-system-prompt" not in argv
    assert "BASE-RESTRICTED" in extra and "EXTRA" in extra


def test_v4_resume_and_model_survive(tmp_path: Path) -> None:
    argv, _ = _run_via_abs(tmp_path, ["p", "--model", "haiku", "--continue"])
    assert "--continue" in argv
    assert _flag_value(argv, "--model") == "haiku"


def test_pre_v4_box_still_gets_the_bare_claude_launch(tmp_path: Path) -> None:
    # A box created from an older image has no /opt/abs — it must keep working
    # exactly as it did, not fail.
    argv = _run(tmp_path, ["p", "--continue"])   # ABSD_ABS_SH unset → path missing
    assert argv[0] == "--channels"
    assert "--profile" not in argv
