"""The two reply channels as independent switches, and the auto-silent override.

Two behaviours are being pinned.

**`reply-text` / `reply-voice` are switches, `reply_mode` is the storage.** People
reason about this as "text on, voice on"; the hooks are written against a three-way
mode that is already tested to death in ``test_reply_voice.py``. These map one onto
the other so no new branch appears in the message-delivery path — that path is
where a dropped reply costs a message, and it is the wrong place for a fourth,
freshly-written code path.

**Both switches off is refused.** It is not a delivery mode, it is silence, and it
is the one state where a message the operator was waiting for never arrives with
nothing saying why. `abs quiet on` already means that, says so, and is reversible
from either side.

**Auto-silent yields to an explicit choice.** After three terminal prompts the
heuristic used to hold proactive pings until you touched your phone. Reasonable as
a default; wrong once you have said "send me every result as text and voice", which
is exactly the contradiction that made the old prompt-held instruction unreliable.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from tests.test_reply_voice import (  # the harness is already built there
    ABS_SH,
    PROFILE,
    abs_home,  # noqa: F401 - fixture
    abs_run,
    needs_tts,
    sh,
)


def _rc(abs_home) -> dict:
    return json.loads((abs_home / "profiles" / PROFILE / "rc.json").read_text())


def _mode(abs_home) -> str:
    return sh(abs_home, "reply_mode").stdout.strip()


# ---- the switches map onto the modes -----------------------------------------


def test_text_on_voice_off_is_the_default_mode(abs_home):  # noqa: F811
    assert _mode(abs_home) == "text"
    out = abs_run(abs_home, "config", "reply-text", "on")
    assert out.returncode == 0
    assert _mode(abs_home) == "text"


def test_voice_on_with_text_on_gives_both(abs_home):  # noqa: F811
    out = abs_run(abs_home, "config", "reply-voice", "on")
    assert out.returncode == 0, out.stderr
    assert _mode(abs_home) == "both"
    assert "text on" in out.stderr and "voice on" in out.stderr


@needs_tts
def test_text_off_with_voice_on_gives_voice_only(abs_home):  # noqa: F811
    abs_run(abs_home, "config", "reply-voice", "on")
    out = abs_run(abs_home, "config", "reply-text", "off")
    assert out.returncode == 0, out.stderr
    assert _mode(abs_home) == "voice"


def test_turning_voice_back_off_returns_to_text(abs_home):  # noqa: F811
    abs_run(abs_home, "config", "reply-voice", "on")
    assert _mode(abs_home) == "both"
    abs_run(abs_home, "config", "reply-voice", "off")
    assert _mode(abs_home) == "text"


def test_switches_are_idempotent(abs_home):  # noqa: F811
    for _ in range(3):
        assert abs_run(abs_home, "config", "reply-voice", "on").returncode == 0
    assert _mode(abs_home) == "both"


@pytest.mark.parametrize("word", ["true", "yes"])
def test_synonyms_for_on(abs_home, word):  # noqa: F811
    assert abs_run(abs_home, "config", "reply-voice", word).returncode == 0
    assert _mode(abs_home) == "both"


@pytest.mark.parametrize("word", ["false", "no"])
def test_synonyms_for_off(abs_home, word):  # noqa: F811
    abs_run(abs_home, "config", "reply-voice", "on")
    assert abs_run(abs_home, "config", "reply-voice", word).returncode == 0
    assert _mode(abs_home) == "text"


def test_garbage_is_refused_and_changes_nothing(abs_home):  # noqa: F811
    abs_run(abs_home, "config", "reply-voice", "on")
    out = abs_run(abs_home, "config", "reply-voice", "maybe")
    assert out.returncode != 0
    assert "on|off" in out.stderr
    assert _mode(abs_home) == "both"  # untouched


def test_no_value_reports_both_channels(abs_home):  # noqa: F811
    abs_run(abs_home, "config", "reply-voice", "on")
    out = abs_run(abs_home, "config", "reply-text")
    assert out.returncode == 0
    assert "text on" in out.stderr and "voice on" in out.stderr


def test_text_is_an_alias_for_reply_text(abs_home):  # noqa: F811
    assert abs_run(abs_home, "config", "text", "on").returncode == 0


# ---- both off is refused, not silently accepted ------------------------------


def test_turning_both_off_is_refused(abs_home):  # noqa: F811
    out = abs_run(abs_home, "config", "reply-text", "off")
    assert out.returncode != 0
    assert "quiet on" in out.stderr  # pointed at the thing that actually means this
    assert _mode(abs_home) == "text"  # nothing changed


@needs_tts
def test_turning_voice_off_while_text_is_off_is_refused(abs_home):  # noqa: F811
    abs_run(abs_home, "config", "reply-voice", "on")
    abs_run(abs_home, "config", "reply-text", "off")
    assert _mode(abs_home) == "voice"

    out = abs_run(abs_home, "config", "reply-voice", "off")
    assert out.returncode != 0
    assert "quiet on" in out.stderr
    assert _mode(abs_home) == "voice"  # still deliverable


def test_text_off_is_refused_on_a_machine_that_cannot_speak(abs_home, tmp_path):  # noqa: F811
    """Suppressing text where nothing can speak delivers nothing at all. The
    failure mode of this feature must be "text as usual", never silence.

    "Cannot speak" is produced by running a COPY of abs.sh from a directory with no
    ``speak.py``/``.venv-*`` beside it: ``voice_root`` looks beside the script, so a
    copy elsewhere resolves to ``$ABS_HOME/voice``, which does not exist. (Same
    mechanism that once made every voice test skip on a box that had TTS — see
    ``_machine_can_speak``.) Turning voice on first is what makes this test about
    the TTS check rather than about the both-off rule.
    """
    lone = tmp_path / "lonely" / "abs.sh"
    lone.parent.mkdir()
    lone.write_bytes(open(ABS_SH, "rb").read())

    def run(*args):
        env = dict(os.environ, ABS_HOME=str(abs_home))
        env.pop("TELEGRAM_STATE_DIR", None)
        return subprocess.run(
            ["bash", str(lone), "--profile", PROFILE, *args],
            capture_output=True, text=True, env=env,
        )

    assert run("config", "reply-voice", "on").returncode == 0  # warns, but allowed
    out = run("config", "reply-text", "off")
    assert out.returncode != 0
    assert "abs voice setup" in out.stderr  # refused for the RIGHT reason
    assert "quiet on" not in out.stderr     # not the both-off rule
    assert _mode(abs_home) == "both"        # left deliverable


# ---- auto-silent -------------------------------------------------------------


def test_auto_silent_is_on_by_default(abs_home):  # noqa: F811
    out = abs_run(abs_home, "config", "auto-silent")
    assert "Auto-silent: on" in out.stderr


def test_auto_silent_can_be_turned_off_directly(abs_home):  # noqa: F811
    out = abs_run(abs_home, "config", "auto-silent", "off")
    assert out.returncode == 0
    assert _rc(abs_home).get("no_auto_silent") is True
    assert "Auto-silent: off" in abs_run(abs_home, "config", "auto-silent").stderr


def test_auto_silent_off_makes_is_quiet_ignore_a_tripped_flag(abs_home):  # noqa: F811
    """The flag may already be set from before the switch was flipped; the switch
    must win, or turning it off would appear to do nothing until the next reset."""
    abs_run(abs_home, "config", "auto-silent", "off")
    rc = abs_home / "profiles" / PROFILE / "rc.json"
    state = json.loads(rc.read_text())
    state["auto_silent"] = True  # as if the heuristic had tripped
    rc.write_text(json.dumps(state))

    assert abs_run(abs_home, "is-quiet").stdout.strip() == "active"


def test_auto_silent_on_still_mutes_when_tripped(abs_home):  # noqa: F811
    rc = abs_home / "profiles" / PROFILE / "rc.json"
    state = json.loads(rc.read_text())
    state["auto_silent"] = True
    rc.write_text(json.dumps(state))
    assert abs_run(abs_home, "is-quiet").stdout.strip() == "quiet"


def test_manual_quiet_still_wins_regardless(abs_home):  # noqa: F811
    """`abs quiet on` is the explicit "mute it" and must not be weakened by any of
    this — it is the answer we point people at instead of both switches off."""
    abs_run(abs_home, "config", "auto-silent", "off")
    abs_run(abs_home, "quiet", "on")
    assert abs_run(abs_home, "is-quiet").stdout.strip() == "quiet"


def test_terminal_streak_never_trips_the_flag_when_disabled(abs_home):  # noqa: F811
    abs_run(abs_home, "config", "auto-silent", "off")
    for _ in range(5):  # well past SILENT_STREAK
        sh(abs_home, '_silent_terminal "$(date +%s)"')
    state = _rc(abs_home)
    assert state.get("auto_silent") is False
    assert state.get("terminal_streak", 0) >= 3  # still counted, just not acted on
    assert abs_run(abs_home, "is-quiet").stdout.strip() == "active"


def test_terminal_streak_does_trip_the_flag_when_enabled(abs_home):  # noqa: F811
    for _ in range(3):
        sh(abs_home, '_silent_terminal "$(date +%s)"')
    assert _rc(abs_home).get("auto_silent") is True
    assert abs_run(abs_home, "is-quiet").stdout.strip() == "quiet"


# ---- the coupling: asking for voice stops the heuristic overruling you -------


def test_turning_voice_on_turns_auto_silent_off(abs_home):  # noqa: F811
    """The whole complaint: asking for a voice note on every result, then having a
    heuristic decide you did not want to be told."""
    abs_run(abs_home, "config", "reply-voice", "on")
    assert _rc(abs_home).get("no_auto_silent") is True


def test_config_reply_both_also_turns_auto_silent_off(abs_home):  # noqa: F811
    out = abs_run(abs_home, "config", "reply", "both")
    assert out.returncode == 0
    assert _rc(abs_home).get("no_auto_silent") is True
    assert "Auto-silent turned off" in out.stderr


def test_config_reply_text_leaves_auto_silent_alone(abs_home):  # noqa: F811
    """Going back to text-only is not a statement about reporting cadence, so it
    must not silently re-enable — or disable — the heuristic."""
    abs_run(abs_home, "config", "auto-silent", "off")
    abs_run(abs_home, "config", "reply", "text")
    assert _rc(abs_home).get("no_auto_silent") is True


def test_auto_silent_can_be_turned_back_on_after_the_coupling(abs_home):  # noqa: F811
    """The coupling is a one-time action with visible feedback, not a permanent
    lock — someone who wants both voice replies and desk-quiet can have it."""
    abs_run(abs_home, "config", "reply-voice", "on")
    abs_run(abs_home, "config", "auto-silent", "on")
    assert _rc(abs_home).get("no_auto_silent") is None
    assert _mode(abs_home) == "both"  # the reply mode is untouched


def test_both_switches_show_up_in_the_config_listing(abs_home):  # noqa: F811
    abs_run(abs_home, "config", "reply-voice", "on")
    out = abs_run(abs_home, "config").stderr
    assert "reply text     on" in out
    assert "reply voice    on" in out
    assert "auto-silent    off" in out


# ---- when the change actually lands ------------------------------------------
#
# The two halves differ, and the command used to say "Takes effect next session"
# for both. That is wrong in opposite directions: it sends someone restarting for
# a voice change that was already live, and — because a line you have learned to
# ignore stops being read — it is exactly how a live session gets mis-scored as
# broken. Voice is decided at send time by the PostToolUse mirror. Suppressing
# text needs the PreToolUse gate, which is written into the settings file when
# the session launches.


@needs_tts
def test_turning_voice_on_says_it_is_live_now(abs_home):  # noqa: F811
    out = abs_run(abs_home, "config", "reply-voice", "on")
    assert out.returncode == 0, out.stderr
    assert "Takes effect now" in out.stderr
    assert "NEW session" not in out.stderr


def test_turning_voice_off_says_it_is_live_now(abs_home):  # noqa: F811
    abs_run(abs_home, "config", "reply-voice", "on")
    out = abs_run(abs_home, "config", "reply-voice", "off")
    assert out.returncode == 0, out.stderr
    assert "Takes effect now" in out.stderr


@needs_tts
def test_turning_text_off_says_a_new_session_is_needed(abs_home):  # noqa: F811
    """The one case that genuinely waits for a relaunch, and the only one the
    operator has to be told about."""
    abs_run(abs_home, "config", "reply-voice", "on")
    out = abs_run(abs_home, "config", "reply-text", "off")
    assert out.returncode == 0, out.stderr
    assert "NEW session" in out.stderr


@needs_tts
def test_turning_text_back_on_is_live_now(abs_home):  # noqa: F811
    abs_run(abs_home, "config", "reply-voice", "on")
    abs_run(abs_home, "config", "reply-text", "off")
    out = abs_run(abs_home, "config", "reply-text", "on")
    assert out.returncode == 0, out.stderr
    assert "Takes effect now" in out.stderr
    assert "NEW session" not in out.stderr
