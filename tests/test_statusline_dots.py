"""The status-bar dots: what each one is allowed to mean.

`cmd_statusline` had no tests at all — not for Text, not for Voice, not for the
Daemon dot added in 3.0.0. It runs on every single Claude Code render, so a
mistake here is in front of the operator constantly, and two of them were.

**Both dots answer the same question about their own channel:** if a reply
happened right now, would it go out this way? Anything else and they can't be
read side by side.

The two bugs this pins:

1. **Voice reported activity, not configuration.** It went green only if a note
   had been sent within `ABS_VOICE_ACTIVE_SECS` (120s). That was right when voice
   was on-demand through `abs say`. Once reply switches made voice fire on every
   reply, the dot went dim two minutes after a note that had arrived exactly as
   configured — reading as broken when nothing was.
2. **Text ignored its own switch.** `reply text off` left the Text dot green,
   because the dot predates the switches and only ever looked at quiet/off.

The colours are load-bearing, so the tests read the actual SGR bytes rather than
stripping them: 71 is the green, 244 the dim grey.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ABS_SH = os.path.join(REPO, "abs.sh")

PROFILE = "bartest"
GREEN = "38;5;71"
DIM = "38;5;244"

_DOT = re.compile(r"\x1b\[(38;5;\d+)m●\x1b\[0m (Text|Voice|Daemon)")


@pytest.fixture
def bar(tmp_path):
    """A statusline renderer over a throwaway ~/.abs.

    abs.sh is COPIED somewhere with no `.venv-kokoro`/`.venv-tts` beside it, so
    `voice_root` falls through to `$ABS_HOME/voice` and this machine's own TTS
    install (or lack of one) can't decide the result. `speak()` fabricates the
    pipeline there; without it, the box genuinely cannot talk.
    """
    home = tmp_path / "abshome"
    (home / "profiles" / PROFILE).mkdir(parents=True)
    lone = tmp_path / "elsewhere"
    lone.mkdir()
    shutil.copy(ABS_SH, lone / "abs")

    class Bar:
        home = None

        def rc(self, **fields):
            p = home / "profiles" / PROFILE / "rc.json"
            state = json.loads(p.read_text()) if p.exists() else {"bot": "b", "chat_id": 42}
            state.update(fields)
            p.write_text(json.dumps(state))

        def speak(self, can=True):
            """Fabricate (or remove) a kokoro install under $ABS_HOME/voice."""
            root = home / "voice"
            if not can:
                shutil.rmtree(root, ignore_errors=True)
                return
            (root / ".venv-kokoro" / "bin").mkdir(parents=True, exist_ok=True)
            py = root / ".venv-kokoro" / "bin" / "python"
            py.write_text("#!/bin/sh\nexit 0\n")
            py.chmod(0o755)
            (root / "speak_kokoro.py").write_text("")

        def access(self, policy):
            """`abs off` state. TG_DIR defaults to ~/.claude/channels/… — well
            outside ABS_HOME — so point rc.json's `tg_dir` at the sandbox first,
            or the test would read (and a bug could write) the real one."""
            p = home / "tg"
            p.mkdir(parents=True, exist_ok=True)
            self.rc(tg_dir=str(p))
            (p / "access.json").write_text(json.dumps({"dmPolicy": policy}))

        def daemon(self, age_s=None):
            """Write this profile's daemon status file, `age_s` seconds old.
            `None` removes the whole daemon dir (a v2 install)."""
            d = home / "daemon"
            if age_s is None:
                shutil.rmtree(d, ignore_errors=True)
                return
            d.mkdir(parents=True, exist_ok=True)
            f = d / f"status-{PROFILE}.json"
            f.write_text("{}")
            when = time.time() - age_s
            os.utime(f, (when, when))

        def render(self, **extra):
            env = dict(os.environ, ABS_HOME=str(home))
            for k in ("TELEGRAM_STATE_DIR", "ABS_SESSION_PROFILE"):
                env.pop(k, None)
            env.update(extra)
            out = subprocess.run(
                ["bash", str(lone / "abs"), "--profile", PROFILE, "statusline"],
                capture_output=True, text=True, env=env,
            )
            assert out.returncode == 0, out.stderr
            return out.stdout

        def dots(self, **extra):
            return {name: colour for colour, name in _DOT.findall(self.render(**extra))}

    b = Bar()
    Bar.home = home
    b.rc()          # a paired profile
    b.speak(True)   # ...on a machine that can talk
    return b


# ---- both switches, both dots ------------------------------------------------


def test_both_switches_on_lights_both_dots(bar):
    bar.rc(reply_mode="both")
    assert bar.dots() == {"Text": GREEN, "Voice": GREEN}


def test_voice_off_dims_only_voice(bar):
    bar.rc(reply_mode="text")
    d = bar.dots()
    assert d["Text"] == GREEN
    assert d["Voice"] == DIM


def test_text_off_dims_only_text(bar):
    """Bug 2. `reply text off` used to leave Text green — the dot predates the
    switches and only ever consulted quiet/off."""
    bar.rc(reply_mode="voice")
    d = bar.dots()
    assert d["Text"] == DIM
    assert d["Voice"] == GREEN


# ---- the recency window is gone ----------------------------------------------


def test_voice_stays_green_long_after_the_last_note(bar):
    """Bug 1, and the one Pranjal reported. Voice is on and works; the last note
    went out two hours ago because nothing needed saying. Green."""
    bar.rc(reply_mode="both", last_voice_ts=int(time.time()) - 7200)
    assert bar.dots()["Voice"] == GREEN


def test_voice_is_green_before_any_note_has_ever_been_sent(bar):
    """No `last_voice_ts` at all — a fresh install with the switch on. The next
    reply WILL speak, so the dot must say so."""
    bar.rc(reply_mode="both")
    assert "last_voice_ts" not in json.loads(
        (bar.home / "profiles" / PROFILE / "rc.json").read_text()
    )
    assert bar.dots()["Voice"] == GREEN


def test_a_recent_note_cannot_light_a_switched_off_channel(bar):
    """The inverse, so the window is really gone rather than merely widened: a
    note sent one second ago must not override `reply voice off`."""
    bar.rc(reply_mode="text", last_voice_ts=int(time.time()))
    assert bar.dots()["Voice"] == DIM


def test_the_old_window_variable_no_longer_changes_anything(bar):
    bar.rc(reply_mode="both", last_voice_ts=int(time.time()) - 7200)
    assert bar.dots(ABS_VOICE_ACTIVE_SECS="1")["Voice"] == GREEN


# ---- a switch is not a promise the machine can keep --------------------------


def test_voice_is_dim_when_the_machine_cannot_speak(bar):
    """The switch says yes, the box has no TTS, so no note will arrive. Dim is
    the honest answer — this is the one thing worth keeping from the old
    behaviour."""
    bar.rc(reply_mode="both")
    bar.speak(False)
    d = bar.dots()
    assert d["Voice"] == DIM
    assert d["Text"] == GREEN


# ---- the global mutes still win ----------------------------------------------


def test_quiet_dims_both(bar):
    bar.rc(reply_mode="both", quiet=True)
    assert bar.dots() == {"Text": DIM, "Voice": DIM}


def test_bot_off_dims_both(bar):
    bar.rc(reply_mode="both")
    bar.access("disabled")
    assert bar.dots() == {"Text": DIM, "Voice": DIM}


def test_an_allowlisted_bot_is_not_off(bar):
    bar.rc(reply_mode="both")
    bar.access("allowlist")
    assert bar.dots() == {"Text": GREEN, "Voice": GREEN}


# ---- the daemon dot ----------------------------------------------------------


def test_daemon_dot_green_when_the_status_file_is_fresh(bar):
    bar.daemon(age_s=10)
    assert bar.dots()["Daemon"] == GREEN


def test_daemon_dot_dim_when_the_status_file_is_stale(bar):
    bar.daemon(age_s=3600)
    assert bar.dots()["Daemon"] == DIM


def test_daemon_dot_dim_when_this_profile_has_no_status_file(bar):
    """The daemon is running but isn't watching THIS profile — which is exactly
    the case where a message sent after the session ends would be missed."""
    bar.daemon(age_s=10)
    (bar.home / "daemon" / f"status-{PROFILE}.json").unlink()
    assert bar.dots()["Daemon"] == DIM


def test_no_daemon_segment_at_all_on_a_v2_install(bar):
    """No `daemon/` dir means absd was never installed; the bar must look exactly
    as it did in v2 rather than growing a permanently grey dot."""
    bar.daemon(None)
    out = bar.render()
    assert "Daemon" not in out
    assert "Text" in out and "Voice" in out


def test_daemon_freshness_window_is_overridable(bar):
    bar.daemon(age_s=600)
    assert bar.dots()["Daemon"] == DIM
    assert bar.dots(ABS_DAEMON_FRESH_MIN="30")["Daemon"] == GREEN


# ---- the contract the whole bar has to keep ----------------------------------


def test_the_bar_never_fails_on_a_profile_with_no_state(bar):
    """Claude Code re-runs this on every render: it must never error, hang, or
    exit non-zero, whatever it finds."""
    (bar.home / "profiles" / PROFILE / "rc.json").unlink()
    out = subprocess.run(
        ["bash", ABS_SH, "--profile", PROFILE, "statusline"],
        capture_output=True, text=True,
        env={**os.environ, "ABS_HOME": str(bar.home)},
    )
    assert out.returncode == 0
    assert out.stdout.strip()


def test_the_bar_survives_a_corrupt_rc_file(bar):
    (bar.home / "profiles" / PROFILE / "rc.json").write_text("{not json")
    out = bar.render()
    assert out.strip()
