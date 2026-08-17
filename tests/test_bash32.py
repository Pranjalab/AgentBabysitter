"""macOS ships bash 3.2. The suite has only ever run bash 5.

Two crashes shipped in one week that were invisible here and fatal on the
operator's Mac — the second one being `build_prompt` dying with `text: command
not found`. The parse bug was the symptom; the missing 3.2 coverage was the
defect. This file closes it.

The hazard, concretely: bash 3.2 looks for the closing `)` of `$( … )` with a
scanner that does not know here-documents exist, so it lexes the here-doc body
as shell. An apostrophe, a stray `)`, a backtick or an odd quote anywhere in the
prose silently moves where the substitution ends and the rest of the paragraph
runs as commands. `x="$(cat <<TAG … TAG)"` is therefore banned outright; put the
here-doc in its own function and substitute the function call.

Two layers, deliberately:

* the grep runs everywhere, including on a laptop with no Docker, and is what
  actually stops the construct coming back;
* the Docker tests are the ones that would have caught this had they existed,
  and they prove the prompt bash 3.2 builds is byte-identical to bash 5's.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ABS_SH = REPO / "abs.sh"

# Every shell file we ship and might one day run under /bin/bash on a Mac.
SHELL_SOURCES = ["abs.sh", "install.sh", "uninstall.sh", "voicelab.sh"]

BASH32_IMAGE = "bash:3.2"
BASH5_IMAGE = "bash:5.2"

# `$(cat <<TAG` / `$( cat <<-TAG` / `` `cat <<TAG `` — the banned shape.
HEREDOC_IN_SUBST = re.compile(r"""[$`]\(?\s*cat\s+<<-?\s*['"]?\w""")


def _sources():
    for name in SHELL_SOURCES:
        p = REPO / name
        if p.exists():
            yield p


def test_no_heredoc_inside_a_command_substitution():
    """The static guard. Runs with no Docker, on any machine, in milliseconds.

    A here-doc whose body is prose must live in its own function. This is the
    rule that keeps the class dead; the Docker tests below only prove today's
    code obeys it.
    """
    offenders = []
    for path in _sources():
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue  # the comment in abs.sh that explains the ban
            if HEREDOC_IN_SUBST.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "here-doc inside a command substitution — bash 3.2 (macOS /bin/bash) "
        "mis-lexes the body and runs the prose as shell. Move the here-doc into "
        "its own function and substitute the call instead:\n  "
        + "\n  ".join(offenders)
    )


# --- the real thing, under a real bash 3.2 ----------------------------------

docker_only = pytest.mark.skipif(
    shutil.which("docker") is None, reason="needs docker for a real bash 3.2"
)


def _docker(image: str, script: str, mounts: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    cmd = ["docker", "run", "--rm", "-v", f"{REPO}:/w:ro"]
    for host, guest in (mounts or {}).items():
        cmd += ["-v", f"{host}:{guest}:ro"]
    cmd += ["-w", "/w", image, "bash", "-c", script]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300)


def _have_image(image: str) -> bool:
    return subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
    ).returncode == 0 or subprocess.run(
        ["docker", "pull", image], capture_output=True, timeout=300
    ).returncode == 0


@pytest.fixture(scope="module")
def bash32():
    if not _have_image(BASH32_IMAGE):
        pytest.skip(f"cannot obtain {BASH32_IMAGE}")
    return BASH32_IMAGE


@pytest.fixture(scope="module")
def bash5():
    if not _have_image(BASH5_IMAGE):
        pytest.skip(f"cannot obtain {BASH5_IMAGE}")
    return BASH5_IMAGE


@docker_only
def test_abs_sh_parses_under_bash_32(bash32):
    """`bash -n` is cheap and catches outright syntax that 3.2 rejects.

    It did NOT catch the crash this file exists for — a mis-lexed substitution
    is syntactically valid garbage — so it is a floor, not the test.
    """
    r = _docker(bash32, "bash -n /w/abs.sh")
    assert r.returncode == 0, r.stderr


# The two branch combinations that matter. `both` + voice-on is the shape that
# crashed on the Mac: it is the only one that evaluates all three prose blocks.
PROMPT_MODES = [
    ("both", True),
    ("both", False),
    ("text", True),
    ("text", False),
    ("voice", True),
]


def _driver(mode: str, voice: bool) -> str:
    """Run build_prompt with the branch flags forced, print nothing else.

    `main "$@"` is dropped so sourcing the script does not launch a session —
    the same trick test_send.py uses.
    """
    return f"""
set -u
grep -v '^main "\\$@"$' /w/abs.sh > /tmp/probe.sh
{{
  echo 'use_profile default'
  echo 'reply_mode() {{ echo {mode}; }}'
  echo 'voice_have() {{ return {0 if voice else 1}; }}'
  echo 'voice_root() {{ echo /voice; }}'
  echo 'build_prompt 424242'
}} >> /tmp/probe.sh
export HOME=/tmp/fakehome; mkdir -p "$HOME"
bash /tmp/probe.sh
"""


@docker_only
@pytest.mark.parametrize("mode,voice", PROMPT_MODES)
def test_build_prompt_survives_bash_32(bash32, mode, voice):
    """The regression. Pre-fix this exits 127 for (both, voice-on)."""
    r = _docker(bash32, _driver(mode, voice))
    assert r.returncode == 0, (
        f"build_prompt died on bash 3.2 with reply_mode={mode} voice={voice}\n"
        f"{r.stderr}"
    )
    assert "command not found" not in r.stderr, r.stderr
    assert "AGENT BABYSITTER IS ACTIVE" in r.stdout
    assert "424242" in r.stdout, "the chat_id did not survive into the prompt"


@docker_only
@pytest.mark.parametrize("mode,voice", PROMPT_MODES)
def test_bash_32_builds_the_same_prompt_as_bash_5(bash32, bash5, mode, voice):
    """Not crashing is half of it — a truncated prompt would also 'work'.

    Same bytes on both shells, or the agent gets a different briefing depending
    on which machine launched it.
    """
    old = _docker(bash32, _driver(mode, voice))
    new = _docker(bash5, _driver(mode, voice))
    assert old.returncode == 0 and new.returncode == 0
    assert old.stdout == new.stdout, (
        f"prompt differs between bash 3.2 and bash 5 for reply_mode={mode} "
        f"voice={voice}: {len(old.stdout)} vs {len(new.stdout)} bytes"
    )
