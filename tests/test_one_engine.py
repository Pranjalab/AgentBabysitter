"""One voice engine, ever — and a machine without one says so instead of faking it.

Reported 17 Sep: on a fresh machine with no Kokoro, the model improvised a
text-to-speech of its own (slow, wrong voice); on a machine that later had
Kokoro, the same reply arrived as two notes from two engines. Two rules close
it. The PreToolUse guard blocks any command that makes speech when the hook is
already speaking replies (mode `both` / `voice`) or when no engine is installed.
And a launch on a machine that cannot speak asks once whether to install voice.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ABS_SH = os.path.join(REPO, "abs.sh")
PROFILE = "oneeng"

# Assembled at runtime so no literal TTS command sits in this file's text — the
# guard reads command lines, and a `bash … tests/` invocation that quoted one
# would be blocked by the very rule under test.
OURS = "abs " + "say hello"
SCRIPT = "python speak_" + "kokoro.py"
MAC = "sa" + "y -v Alex hello"
ESPEAK = "esp" + "eak hello"
COQUI = "tt" + "s --text hello"


@pytest.fixture
def box(tmp_path):
    home = tmp_path / "abshome"
    prof = home / "profiles" / PROFILE
    prof.mkdir(parents=True)
    (home / "profiles" / "default").mkdir()
    tg = tmp_path / "tg"
    tg.mkdir()
    (tg / ".env").write_text("TELEGRAM_BOT_TOKEN=123:fake\n")
    (prof / "rc.json").write_text(json.dumps({
        "bot": "b", "chat_id": 42, "tg_dir": str(tg), "reply_mode": "both",
    }))
    # A voice root the test controls: empty = cannot speak; populated = can.
    vroot = tmp_path / "voice"
    vroot.mkdir()

    class Box:
        rc = prof / "rc.json"
        voice_root = vroot

        def can_speak(self):
            (vroot / ".venv-kokoro" / "bin").mkdir(parents=True, exist_ok=True)
            py = vroot / ".venv-kokoro" / "bin" / "python"
            py.write_text("#!/bin/sh\nexit 0\n")
            py.chmod(0o755)
            (vroot / "speak_kokoro.py").write_text("")
            (tmp_path / "bin").mkdir(exist_ok=True)
            ff = tmp_path / "bin" / "ffmpeg"
            ff.write_text("#!/bin/sh\nexit 0\n")
            ff.chmod(0o755)

        def mode(self, m):
            d = json.loads(self.rc.read_text())
            d["reply_mode"] = m
            self.rc.write_text(json.dumps(d))

        def guard(self, command, origin="terminal"):
            d = json.loads(self.rc.read_text())
            d["last_origin"] = origin
            self.rc.write_text(json.dumps(d))
            payload = json.dumps({"tool_name": "Bash", "session_id": "s-1",
                                  "tool_input": {"command": command}})
            env = dict(os.environ, ABS_HOME=str(home), ABS_VOICE_ROOT=str(vroot),
                       PATH=f"{tmp_path / 'bin'}:{os.environ.get('PATH', '')}")
            env.pop("TELEGRAM_STATE_DIR", None)
            return subprocess.run(["bash", ABS_SH, "--profile", PROFILE, "__guard-hook"],
                                  input=payload, capture_output=True, text=True, env=env)

    return Box()


# ---- no engine: nothing improvised --------------------------------------------

@pytest.mark.parametrize("cmd", [OURS, SCRIPT, MAC, ESPEAK, COQUI])
def test_without_an_engine_every_way_of_making_audio_is_blocked(box, cmd):
    r = box.guard(cmd)
    assert r.returncode == 2, (cmd, r.stderr)
    assert "voice is not installed" in r.stderr
    assert "voice setup" in r.stderr


def test_the_block_applies_at_the_desk_too_not_only_from_telegram(box):
    r = box.guard(MAC, origin="terminal")
    assert r.returncode == 2


# ---- an engine, and the hook already speaking: one note ---------------------

@pytest.mark.parametrize("cmd", [OURS, SCRIPT, MAC])
def test_in_mode_both_the_models_own_note_is_blocked_as_a_second_copy(box, cmd):
    box.can_speak()
    box.mode("both")
    r = box.guard(cmd)
    assert r.returncode == 2, (cmd, r.stderr)
    assert "second copy" in r.stderr


def test_in_mode_voice_too(box):
    box.can_speak()
    box.mode("voice")
    assert box.guard(OURS).returncode == 2


def test_in_mode_text_with_an_engine_abs_say_is_the_models_to_run(box):
    """"Speak this one" is a real request when replies are otherwise text."""
    box.can_speak()
    box.mode("text")
    r = box.guard(OURS)
    assert r.returncode == 0, r.stderr


def test_ordinary_commands_are_untouched(box):
    box.can_speak()
    for cmd in ("git push", "ls -la", "grep -r say .", "echo tts", "abs voice status"):
        assert box.guard(cmd).returncode == 0, cmd


def test_the_rule_survives_the_guards_own_off_switch(box):
    """`abs config guard off` disarms the destructive-command guard; it must not
    disarm the one-engine rule, which is about duplicates, not trust."""
    box.can_speak()
    box.mode("both")
    d = json.loads(box.rc.read_text())
    d["no_guard"] = True
    box.rc.write_text(json.dumps(d))
    assert box.guard(OURS).returncode == 2


# ---- the launch offer ------------------------------------------------------------

def test_the_voice_off_prompt_forbids_improvising(box, tmp_path):
    env = dict(os.environ, ABS_HOME=str(box.rc.parent.parent.parent), ABS_VOICE_ROOT=str(box.voice_root))
    env.pop("TELEGRAM_STATE_DIR", None)
    r = subprocess.run(["bash", ABS_SH, "--profile", PROFILE, "prompt", "show", "built"],
                       capture_output=True, text=True, env=env)
    assert "Do NOT improvise one" in r.stdout
    assert "voice setup" in r.stdout
