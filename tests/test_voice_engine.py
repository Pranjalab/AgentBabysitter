"""Kokoro is the engine everyone gets. Chatterbox is the exception.

Chatterbox was the original TTS engine and it was the wrong default for almost
everyone: it is torch on a GPU, and without one it falls back to the CPU, where a
long report takes minutes. That is not merely slow — it is long enough for a
burst of replies to overlap, which is exactly how the operator's MacBook Air
ended up with three wedged synthesis processes and no voice notes at all.

Kokoro is 82M parameters, built for the CPU, and produces a note in seconds on
the same hardware. From 3.2.3 `abs voice setup` builds it and nothing else.

Chatterbox is kept, not deleted, because it does one thing kokoro cannot: clone a
voice from a sample. It is `--chatterbox` now — asked for on purpose.

The subtle part, and the reason this file exists: `voice_have` used to require
`.venv-tts`. Left alone, it would have called every new kokoro-only install
broken, and `build_prompt` would have told the model voice was not available on a
machine that speaks perfectly well.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ABS_SH = REPO / "abs.sh"

PROFILE = "enginetest"


@pytest.fixture
def box(tmp_path):
    home = tmp_path / "abshome"
    (home / "profiles" / PROFILE).mkdir(parents=True)
    (home / "profiles" / PROFILE / "rc.json").write_text(
        json.dumps({"bot": "testbot", "chat_id": 42})
    )

    class Box:
        abs_home = home
        root = tmp_path / "voiceroot"

        def install(self, *, stt=True, kokoro=False, chatterbox=False):
            """Fabricate a voice root with exactly the pieces named."""
            self.root.mkdir(exist_ok=True)
            def venv(name):
                (self.root / name / "bin").mkdir(parents=True, exist_ok=True)
                py = self.root / name / "bin" / "python"
                py.write_text("#!/bin/sh\nexit 0\n")
                py.chmod(0o755)
            if stt:
                venv(".venv")
                (self.root / "transcribe.py").write_text("")
            if kokoro:
                venv(".venv-kokoro")
                (self.root / "speak_kokoro.py").write_text("")
            if chatterbox:
                venv(".venv-tts")
                (self.root / "speak.py").write_text("")

        def call(self, snippet):
            body = "".join(l for l in open(ABS_SH) if l.strip() != 'main "$@"')
            script = tmp_path / "call.sh"
            script.write_text(f"{body}\nuse_profile {PROFILE}\n{snippet}\n")
            env = dict(os.environ, ABS_HOME=str(home), ABS_VOICE_ROOT=str(self.root))
            env.pop("TELEGRAM_STATE_DIR", None)
            return subprocess.run(["bash", str(script)], capture_output=True,
                                  text=True, env=env)

        def run(self, *args):
            env = dict(os.environ, ABS_HOME=str(home), ABS_VOICE_ROOT=str(self.root))
            env.pop("TELEGRAM_STATE_DIR", None)
            return subprocess.run(["bash", str(ABS_SH), "--profile", PROFILE, *args],
                                  capture_output=True, text=True, env=env)

    return Box()


# ---- what counts as "voice is installed" -------------------------------------


def test_a_kokoro_only_install_is_a_working_install(box):
    """The regression this file is really guarding. `voice_have` demanded
    `.venv-tts`, so without this change every install built by 3.2.3 would report
    itself broken — and `build_prompt` would tell the model voice was unavailable
    on a machine that speaks fine."""
    box.install(kokoro=True)
    assert box.call("voice_have && echo YES || echo NO").stdout.strip() == "YES"
    assert box.call("voice_can_speak && echo YES || echo NO").stdout.strip() == "YES"


def test_a_chatterbox_only_install_still_counts(box):
    """Everyone who set voice up before 3.2.3 has exactly this. An upgrade must
    not turn their working install into a broken one."""
    box.install(chatterbox=True)
    assert box.call("voice_have && echo YES || echo NO").stdout.strip() == "YES"


def test_no_speech_engine_at_all_is_not_a_working_install(box):
    box.install(stt=True)
    assert box.call("voice_have && echo YES || echo NO").stdout.strip() == "NO"


def test_speech_without_transcription_is_not_the_whole_pipeline(box):
    """`voice_have` is the both-directions claim — build_prompt tells the model it
    can transcribe an inbound note on the strength of it."""
    box.install(stt=False, kokoro=True)
    assert box.call("voice_have && echo YES || echo NO").stdout.strip() == "NO"


# ---- which engine actually runs ----------------------------------------------


def test_kokoro_is_chosen_when_both_are_present(box):
    box.install(kokoro=True, chatterbox=True)
    out = box.run("voice", "status")
    assert "Engine: kokoro" in out.stderr, out.stderr


def test_a_chatterbox_only_machine_is_warned_it_is_the_slow_one(box):
    """It says "ready" either way, and that word hides a 10x difference. The
    operator had no way to see which engine he had."""
    box.install(chatterbox=True)
    out = box.run("voice", "status")
    assert "chatterbox only" in out.stderr, out.stderr
    assert "abs voice setup --force" in out.stderr


# ---- the status page ----------------------------------------------------------


def test_chatterbox_is_shown_as_optional_not_as_missing(box):
    """A bare ✗ beside "TTS" on a perfectly good kokoro install reads as a broken
    install. The row has to say what it is for."""
    box.install(kokoro=True)
    out = box.run("voice", "status")
    assert "voice cloning" in out.stderr, out.stderr
    assert "optional" in out.stderr


def test_setup_help_leads_with_kokoro_and_offers_chatterbox(box):
    out = box.run("voice", "setup", "--help")
    assert out.returncode == 0
    assert "Kokoro" in out.stderr
    assert "--chatterbox" in out.stderr
    assert "clone" in out.stderr


def test_an_unknown_setup_flag_is_refused(box):
    out = box.run("voice", "setup", "--gpu")
    assert out.returncode != 0
    assert "Usage: abs voice setup" in out.stderr


# ---- choosing a voice by ear -------------------------------------------------
#
# The operator picked the voice he liked by accident, then asked to be offered a
# choice. A list of identifiers is not a choice anybody can make — `af_bella` and
# `bm_george` mean nothing until you have heard them — and this is a tool whose
# premise is that he would rather listen than read. So the samples are sent as
# real voice notes, and the offer is made once and then never again.


def test_samples_refuses_on_a_machine_that_cannot_speak(box):
    box.install(stt=True)
    out = box.run("voice", "samples")
    assert out.returncode != 0
    assert "abs voice setup" in out.stderr


def test_samples_needs_kokoro_specifically(box):
    """Chatterbox has no voice list to choose from — the sample set is kokoro's."""
    box.install(chatterbox=True)
    out = box.run("voice", "samples")
    assert out.returncode != 0


def test_samples_is_in_the_help(box):
    out = box.run("voice", "--help")
    assert "samples" in out.stderr


# ---- the one-time offer ------------------------------------------------------


def _prompt(box, **overrides):
    body = "".join(l for l in open(ABS_SH) if l.strip() != 'main "$@"')
    script = box.abs_home.parent / "prompt.sh"
    lines = [body, f"use_profile {PROFILE}"]
    for fn, ret in overrides.items():
        lines.append(f"{fn}() {{ return {ret}; }}")
    lines.append("build_prompt 42")
    script.write_text("\n".join(lines) + "\n")
    env = dict(os.environ, ABS_HOME=str(box.abs_home), ABS_VOICE_ROOT=str(box.root))
    env.pop("TELEGRAM_STATE_DIR", None)
    return subprocess.run(["bash", str(script)], capture_output=True, text=True,
                          env=env).stdout


def test_the_offer_appears_for_someone_who_has_never_chosen(box):
    box.install(kokoro=True)
    assert "PICK A VOICE" in _prompt(box, voice_have=0, voice_can_speak=0)


def test_the_offer_is_absent_once_a_voice_has_been_chosen(box):
    """Whatever they pick, they have been asked. Asking again is nagging."""
    box.install(kokoro=True)
    assert box.run("config", "kokoro-voice", "bf_emma").returncode == 0
    assert "PICK A VOICE" not in _prompt(box, voice_have=0, voice_can_speak=0)


def test_the_offer_is_absent_on_a_machine_that_cannot_speak(box):
    """Offering a choice of voices to a box with no engine is pure noise."""
    box.install(stt=True)
    assert "PICK A VOICE" not in _prompt(box, voice_have=1, voice_can_speak=1)


def test_the_offer_can_be_dismissed_without_choosing(box):
    """"It sounds fine, leave it" has to end this as firmly as picking one."""
    box.install(kokoro=True)
    assert box.run("config", "voice-offer", "done").returncode == 0
    assert "PICK A VOICE" not in _prompt(box, voice_have=0, voice_can_speak=0)


def test_the_offer_can_be_brought_back(box):
    box.install(kokoro=True)
    box.run("config", "voice-offer", "done")
    assert box.run("config", "voice-offer", "reset").returncode == 0
    assert "PICK A VOICE" in _prompt(box, voice_have=0, voice_can_speak=0)


def test_the_offer_state_can_be_read(box):
    box.install(kokoro=True)
    assert "pending" in box.run("config", "voice-offer").stderr
    box.run("config", "voice-offer", "done")
    assert "done" in box.run("config", "voice-offer").stderr


def test_the_offer_carries_real_runnable_paths(box):
    """A prompt that tells the model to run `SCRIPT_PATH_HERE` is worse than no
    prompt: it looks like an instruction and cannot be followed."""
    box.install(kokoro=True)
    text = _prompt(box, voice_have=0, voice_can_speak=0)
    assert "SCRIPT_PATH_HERE" not in text
    assert "PROFILE_HERE" not in text
    assert f"--profile {PROFILE} voice samples" in text
