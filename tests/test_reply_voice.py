"""Reply mode: voice that survives the model forgetting.

`abs config reply both|voice` is stored state, and the session hooks act on it.
The point of the feature is that it holds when the model does not remember the
instruction, so these tests drive the hooks directly — the model is not in the
loop at all.

TTS is stubbed through ABS_VOICE_CMD: a real note costs ~30s in a speech model
and tells us nothing these assertions don't.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ABS_SH = os.path.join(REPO, "abs.sh")

PROFILE = "voicetest"
REPLY_TOOL = "mcp__plugin_telegram_telegram__reply"

def _machine_can_speak():
    """Does `abs config reply voice` arm on this box?

    It refuses on a machine with no TTS — correctly, since it would suppress text
    and deliver nothing — which makes the voice-mode tests unrunnable there. Probe
    by running the real abs.sh: `voice_root` looks for the venvs *beside the
    script*, so sourcing a copy from elsewhere would answer for the wrong machine.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        prof = os.path.join(tmp, "profiles", PROFILE)
        os.makedirs(prof)
        with open(os.path.join(prof, "rc.json"), "w") as f:
            json.dump({"bot": "probe", "chat_id": 1}, f)
        env = dict(os.environ, ABS_HOME=tmp)
        env.pop("TELEGRAM_STATE_DIR", None)
        return subprocess.run(
            ["bash", ABS_SH, "--profile", PROFILE, "config", "reply", "voice"],
            capture_output=True, env=env,
        ).returncode == 0


needs_tts = pytest.mark.skipif(
    not _machine_can_speak(), reason="no TTS installed; reply mode 'voice' cannot arm"
)


@pytest.fixture
def abs_home(tmp_path):
    """A throwaway ~/.abs with one paired profile."""
    home = tmp_path / "abshome"
    prof = home / "profiles" / PROFILE
    prof.mkdir(parents=True)
    (prof / "rc.json").write_text(json.dumps({"bot": "testbot", "chat_id": 42}))
    return home


@pytest.fixture
def spoken(tmp_path):
    """Stands in for the TTS engine; returns a reader for what it was asked to say.

    Reads the text on stdin, like the real engines do when handed `-`. Writing
    `exit 1` into fail_file makes the next synthesis fail, which is how the
    fallback path gets exercised without breaking a real TTS install.
    """
    log = tmp_path / "spoken.txt"
    fail = tmp_path / "make_it_fail"
    stub = tmp_path / "stub.sh"
    stub.write_text(
        "#!/bin/sh\n"
        f'[ -f "{fail}" ] && exit 1\n'
        f'printf "%s\\n" "$(cat)" >> "{log}"\n'
    )
    stub.chmod(0o755)

    class Spoken:
        cmd = f"/bin/sh {stub}"
        fail_file = fail

        def break_the_engine(self):
            fail.write_text("")

        def fix_the_engine(self):
            fail.unlink()

        def lines(self, wait=8.0):
            """The mirror is deliberately detached, so give it a moment to land."""
            deadline = time.time() + wait
            while time.time() < deadline:
                if log.exists() and log.read_text().strip():
                    break
                time.sleep(0.1)
            return [l for l in log.read_text().splitlines() if l.strip()] if log.exists() else []

        def settled(self):
            time.sleep(1.5)
            return self.lines(wait=0)

    return Spoken()


def _env(abs_home, spoken=None, **extra):
    env = dict(os.environ, ABS_HOME=str(abs_home))
    env.pop("TELEGRAM_STATE_DIR", None)
    if spoken is not None:
        env["ABS_VOICE_CMD"] = spoken.cmd
    env.update(extra)
    return env


def abs_run(abs_home, *args, spoken=None, stdin="", **extra):
    return subprocess.run(
        ["bash", ABS_SH, "--profile", PROFILE, *args],
        input=stdin, capture_output=True, text=True, env=_env(abs_home, spoken, **extra),
    )


def sh(abs_home, snippet, spoken=None, **extra):
    """Call a function inside abs.sh directly, with `main` never running."""
    body = "".join(l for l in open(ABS_SH) if l.strip() != 'main "$@"')
    script = abs_home.parent / "call.sh"
    script.write_text(f"{body}\nuse_profile {PROFILE}\n{snippet}\n")
    return subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, env=_env(abs_home, spoken, **extra)
    )


# --- pinning what the machine can do -----------------------------------------
#
# The reply-mode default is "voice where the machine can speak", so from 3.0.3 a
# test that leaves reply_mode unset is asserting something about the box it runs
# on, not about abs. `ABS_VOICE_ROOT` makes it explicit: point it somewhere with
# a plausible engine, or somewhere empty, and the answer stops depending on
# whether the developer happens to have run `abs voice setup`.


def mute_machine(tmp_path):
    """A root with no engine in it. `voice_can_speak` says no."""
    root = tmp_path / "no-voice"
    root.mkdir(exist_ok=True)
    return {"ABS_VOICE_ROOT": str(root)}


def speaking_machine(tmp_path):
    """The shape `voice_can_speak` looks for: a kokoro venv, its script, ffmpeg.

    Nothing here is ever executed — the probe is `[ -x ]` and `[ -f ]` — so a
    stub costs nothing and keeps the test off the real engine.
    """
    root = tmp_path / "has-voice"
    (root / ".venv-kokoro" / "bin").mkdir(parents=True, exist_ok=True)
    py = root / ".venv-kokoro" / "bin" / "python"
    py.write_text("#!/bin/sh\nexit 0\n")
    py.chmod(0o755)
    (root / "speak_kokoro.py").write_text("")
    env = {"ABS_VOICE_ROOT": str(root)}
    if shutil.which("ffmpeg") is None:
        # voice_can_speak also insists on ffmpeg, since a note that cannot be
        # encoded is not a note. Stub it rather than skipping the test.
        binp = root / "bin"
        binp.mkdir(exist_ok=True)
        ff = binp / "ffmpeg"
        ff.write_text("#!/bin/sh\nexit 0\n")
        ff.chmod(0o755)
        env["PATH"] = f"{binp}{os.pathsep}{os.environ['PATH']}"
    return env


def _mark_turn_came_from_telegram(abs_home):
    """The Bash guard only bites on a remote-driven turn."""
    rc = abs_home / "profiles" / PROFILE / "rc.json"
    state = json.loads(rc.read_text())
    state["last_origin"] = "telegram"
    rc.write_text(json.dumps(state))


def hook_payload(text, tool=REPLY_TOOL, files=None):
    payload = {
        "hook_event_name": "PostToolUse",   # ignored by the PreToolUse entry point
        "tool_name": tool,
        "tool_input": {"chat_id": "42", "text": text},
    }
    if files is not None:
        payload["tool_input"]["files"] = files
    return json.dumps(payload)


# --- the setting -------------------------------------------------------------


def test_a_machine_that_cannot_speak_defaults_to_text(abs_home, tmp_path):
    """The floor. Voice cannot be the default where nothing can synthesise it —
    that would queue every reply behind an engine that isn't there."""
    assert sh(abs_home, "reply_mode", **mute_machine(tmp_path)).stdout == "text"


def test_a_machine_that_can_speak_defaults_to_both(abs_home, tmp_path):
    """3.0.3: voice on by default where the box can do it. The operator asked for
    it, and asking someone to opt in twice — install the engine, then enable it —
    is one step too many."""
    assert sh(abs_home, "reply_mode", **speaking_machine(tmp_path)).stdout == "both"


def test_the_default_is_never_voice_only(abs_home, tmp_path):
    """`voice` SUPPRESSES text. A default that silently drops the written record
    is not a default anyone consented to; it stays an explicit choice."""
    assert sh(abs_home, "reply_mode", **speaking_machine(tmp_path)).stdout != "voice"


@pytest.mark.parametrize("word,stored", [("both", "both"), ("voice", "voice"), ("text", "text")])
def test_the_mode_is_stored_and_read_back(abs_home, word, stored):
    abs_run(abs_home, "config", "reply", word)
    assert sh(abs_home, "reply_mode").stdout == stored


def test_a_mode_nobody_defined_is_refused_rather_than_half_applied(abs_home, tmp_path):
    proc = abs_run(abs_home, "config", "reply", "shout")
    assert proc.returncode != 0
    assert "text|both|voice" in proc.stderr
    assert sh(abs_home, "reply_mode", **mute_machine(tmp_path)).stdout == "text"


def test_asking_for_text_on_a_speaking_machine_actually_gets_text(abs_home, tmp_path):
    """The regression the new default creates if you are careless.

    `text` used to be stored by DELETING the key, because unset meant text. Now
    unset means "whatever this machine can do" — so deleting on a box with an
    engine would hand back `both` and make the setting look ignored.
    """
    speaks = speaking_machine(tmp_path)
    abs_run(abs_home, "config", "reply", "text", **speaks)
    assert sh(abs_home, "reply_mode", **speaks).stdout == "text"


def test_auto_hands_the_choice_back_to_the_machine(abs_home, tmp_path):
    """The way back to the default, now that `text` is a stored value."""
    speaks = speaking_machine(tmp_path)
    abs_run(abs_home, "config", "reply", "text", **speaks)
    abs_run(abs_home, "config", "reply", "auto", **speaks)
    assert sh(abs_home, "reply_mode", **speaks).stdout == "both"
    assert sh(abs_home, "reply_mode", **mute_machine(tmp_path)).stdout == "text"


def test_garbage_in_the_state_file_reads_as_text_not_as_a_broken_mode(abs_home):
    (abs_home / "profiles" / PROFILE / "rc.json").write_text(
        json.dumps({"bot": "testbot", "chat_id": 42, "reply_mode": "sing"})
    )
    assert sh(abs_home, "reply_mode").stdout == "text"


def test_the_mode_shows_up_in_config(abs_home):
    """The listing reports the two channels, not the internal three-way mode — that
    is how the setting is actually reasoned about ("text on, voice on")."""
    abs_run(abs_home, "config", "reply", "both")
    out = abs_run(abs_home, "config").stderr
    assert "reply text     on" in out
    assert "reply voice    on" in out


# --- turning markdown into something worth hearing ---------------------------


@pytest.mark.parametrize(
    "written,spoken_text",
    [
        ("**bold** and *italic*", "bold and italic"),
        ("run `abs status` now", "run abs status now"),
        ("see [the docs](https://x.dev/y)", "see the docs"),
        ("go to https://example.com/a/b now", "go to link now"),
        ("# Heading", "Heading"),
        ("- first\n- second", "first second"),
        ("line one\nline two", "line one line two"),
    ],
)
def test_markdown_syntax_is_not_read_aloud(abs_home, written, spoken_text):
    # Through a file, so a newline in the input stays a newline.
    src = abs_home.parent / "written.txt"
    src.write_text(written)
    out = sh(abs_home, f'_voice_prep "$(cat {src})"').stdout
    assert out == spoken_text


# --- emoji ---------------------------------------------------------------------
#
# The engine reads them aloud as invented words — the usage footer "📊 5h 3%"
# came back as a nonsense syllable. Stripped as raw UTF-8 byte ranges under
# LC_ALL=C rather than with a unicode class, because the class needs perl or
# python and this path must never fail: with pipefail, a machine without perl
# would lose every voice reply rather than mispronouncing one word.


@pytest.mark.parametrize(
    "written,spoken_text",
    [
        ("📊 5h 3% · wk 7%", "5h 3% · wk 7%"),          # the usage footer
        ("✅ Done", "Done"),                             # dingbats
        ("✗ Unexpected failure", "Unexpected failure"),
        ("Deploy ⭐ ready", "Deploy ready"),             # U+2B50
        ("shipped ❤️ it", "shipped it"),                 # emoji + variation selector
        ("Flags 🇮🇳 here", "Flags here"),                # regional indicators
        ("the 👨‍👩‍👧 family", "the family"),                # joined with ZWJ
        ("🔊 Recording a voice note", "Recording a voice note"),
    ],
)
def test_emoji_are_stripped_before_the_engine_sees_them(abs_home, written, spoken_text):
    src = abs_home.parent / "emoji.txt"
    src.write_text(written, encoding="utf-8")
    out = sh(abs_home, f'_voice_prep "$(cat {src})"').stdout
    assert out == spoken_text


@pytest.mark.parametrize(
    "written",
    [
        "it's done — and that's that…",   # apostrophe, em dash, ellipsis
        'he said "no" and left',
        "cost: €5, £4, ¥3 · 50% done",
        "naïve café résumé",
    ],
)
def test_the_punctuation_reports_are_written_with_survives(abs_home, written):
    """The strip is a byte range, so the neighbouring ranges are the risk. Em
    dash, ellipsis and curly quotes live one byte away from the dingbats."""
    src = abs_home.parent / "punct.txt"
    src.write_text(written, encoding="utf-8")
    assert sh(abs_home, f'_voice_prep "$(cat {src})"').stdout == written


def test_a_message_that_is_only_emoji_is_not_worth_a_voice_note(abs_home):
    """A bare 👍 used to cost thirty seconds of synthesis and arrive as a grunt.
    Stripped to nothing, it now fails the worth-saying gate on length."""
    src = abs_home.parent / "thumb.txt"
    src.write_text("👍👍👍", encoding="utf-8")
    assert sh(abs_home, f'_voice_prep "$(cat {src})"').stdout == ""
    assert sh(abs_home, f'_voice_worth_saying "$(_voice_prep "$(cat {src})")"').returncode == 1


# --- what may replace text, and what may not ---------------------------------


def test_ordinary_prose_can_be_delivered_as_voice(abs_home):
    assert sh(abs_home, "_voice_speakable 'The tests pass and I pushed the fix.'").returncode == 0


@pytest.mark.parametrize(
    "text,why",
    [
        ("here you go:\n```\nrm -rf /tmp/x\n```", "a command to copy"),
        ("the deploy is at https://vercel.com/x/y", "a link to tap"),
        ("word " * 400, "too long to sit through"),
        ("ok", "nothing left worth saying"),
    ],
)
def test_a_message_that_voice_cannot_carry_stays_text(abs_home, text, why):
    assert sh(abs_home, f"_voice_speakable {text!r}").returncode == 1, why


# --- the hooks: this is where the feature actually lives ---------------------


def test_in_text_mode_a_reply_is_never_spoken(abs_home, spoken):
    # Set explicitly: since 3.0.3 an unset mode on a box with an engine means
    # `both`, and this test is about `text`.
    abs_run(abs_home, "config", "reply", "text")
    abs_run(abs_home, "__silent-hook", spoken=spoken, stdin=hook_payload("all done"))
    assert spoken.settled() == []


def test_in_both_mode_every_reply_is_spoken_without_anyone_asking(abs_home, spoken):
    abs_run(abs_home, "config", "reply", "both")
    abs_run(abs_home, "__silent-hook", spoken=spoken, stdin=hook_payload("the tests pass"))
    assert spoken.lines() == ["the tests pass"]


def test_in_both_mode_with_voice_first_off_the_text_goes_straight_out(abs_home, spoken):
    """`both` means both, and with voice-first off the text goes first, untouched:
    the PreToolUse gate must not fire, and the PostToolUse mirror speaks after."""
    abs_run(abs_home, "config", "reply", "both")
    abs_run(abs_home, "config", "voice-first", "off")
    proc = abs_run(abs_home, "__guard-hook", spoken=spoken, stdin=hook_payload("the tests pass"))
    assert proc.returncode == 0


@needs_tts
def test_in_both_mode_voice_first_takes_over_the_delivery(abs_home, spoken):
    """Voice-first is on by default, so in mode `both` the gate DOES fire now.

    It blocks the tool's own send and hands both halves to one worker, which
    speaks and then sends the text — the order is pinned in
    ``test_voice_first.py``. What matters here is that blocking is deliberate and
    says so, because the old contract for this mode was "never block".
    """
    abs_run(abs_home, "config", "reply", "both")
    proc = abs_run(abs_home, "__guard-hook", spoken=spoken, stdin=hook_payload("the tests pass"))
    assert proc.returncode == 2
    assert "voice note first" in proc.stderr
    assert "do NOT resend" in proc.stderr


@needs_tts
def test_in_voice_mode_the_text_is_blocked_and_the_words_are_spoken(abs_home, spoken):
    abs_run(abs_home, "config", "reply", "voice")
    proc = abs_run(abs_home, "__guard-hook", spoken=spoken,
                   stdin=hook_payload("the tests pass and I pushed it"))
    assert proc.returncode == 2
    assert "voice note" in proc.stderr
    assert "do NOT resend" in proc.stderr
    assert spoken.lines() == ["the tests pass and I pushed it"]


@pytest.mark.parametrize(
    "payload,why",
    [
        (hook_payload("run:\n```\nabs status\n```"), "a command has to be copyable"),
        (hook_payload("it's live at https://example.com"), "a link has to be tappable"),
        (hook_payload("here it is", files=["/tmp/shot.png"]), "an attachment needs its message"),
        (hook_payload(""), "nothing to say"),
    ],
)
@needs_tts
def test_voice_mode_still_lets_through_what_voice_cannot_carry(abs_home, spoken, payload, why):
    abs_run(abs_home, "config", "reply", "voice")
    proc = abs_run(abs_home, "__guard-hook", spoken=spoken, stdin=payload)
    assert proc.returncode == 0, why


@needs_tts
def test_a_voice_mode_message_that_stayed_text_is_still_spoken_afterwards(abs_home, spoken):
    """Let through by the gate, so the PostToolUse mirror is what delivers the audio."""
    abs_run(abs_home, "config", "reply", "voice")
    payload = hook_payload("it's live at https://example.com and the tests pass")
    assert abs_run(abs_home, "__guard-hook", spoken=spoken, stdin=payload).returncode == 0
    abs_run(abs_home, "__silent-hook", spoken=spoken, stdin=payload)
    assert spoken.lines() == ["it's live at link and the tests pass"]


@needs_tts
def test_the_bridge_is_never_muted_by_a_machine_that_cannot_speak(abs_home, spoken, tmp_path):
    """No TTS installed must mean 'text as usual', never 'silence'."""
    abs_run(abs_home, "config", "reply", "voice")
    # A copy of abs.sh with no speak.py or venv beside it, and an ABS_HOME with no
    # voice/ — which is exactly what a machine that never ran `abs voice setup`
    # looks like to voice_root.
    lonely = tmp_path / "lonely" / "abs.sh"
    lonely.parent.mkdir()
    shutil.copy(ABS_SH, lonely)
    proc = subprocess.run(
        ["bash", str(lonely), "--profile", PROFILE, "__guard-hook"],
        input=hook_payload("the tests pass"), capture_output=True, text=True,
        env=_env(abs_home, spoken),
    )
    assert proc.returncode == 0
    assert spoken.settled() == []


def test_the_same_sentence_is_not_spoken_twice(abs_home, spoken):
    """A model that also runs `abs say` shouldn't produce the note a second time."""
    abs_run(abs_home, "config", "reply", "both")
    for _ in range(2):
        abs_run(abs_home, "__silent-hook", spoken=spoken, stdin=hook_payload("the tests pass"))
        time.sleep(1.0)
    assert spoken.settled() == ["the tests pass"]


def test_a_reply_far_too_long_to_speak_is_cut_without_deferring_to_the_text(
    abs_home, spoken
):
    """Still bounded, but it no longer ends by sending him off to read.

    "You shouldn't say 'rest' in the text. I don't want to read the text." The note
    is the answer, so a closing line that points at the written half turned every
    long reply into a trailer. What it says now is a fact about the note's length.
    The PostToolUse mirror this drives keeps the tighter 1200 ceiling; voice-first
    passes its own, larger one.
    """
    abs_run(abs_home, "config", "reply", "both")
    abs_run(abs_home, "__silent-hook", spoken=spoken, stdin=hook_payload("sentence. " * 400))
    said = spoken.lines()[0]
    assert len(said) < 1400
    assert "rest is in the text" not in said
    assert said.endswith("that is as much as one note can carry.")


# --- not breaking what was already there -------------------------------------


def test_the_destructive_command_guard_still_blocks(abs_home):
    """The reply gate shares this entry point; it must not have displaced the guard."""
    _mark_turn_came_from_telegram(abs_home)
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/x"}})
    proc = abs_run(abs_home, "__guard-hook", stdin=payload)
    assert proc.returncode == 2
    assert "Blocked by Agent Babysitter" in proc.stderr


@needs_tts
def test_turning_the_command_guard_off_does_not_turn_off_voice_mode(abs_home, spoken):
    """Two unrelated jobs, one hook command — one switch must not silence the other."""
    abs_run(abs_home, "config", "guard", "off")
    abs_run(abs_home, "config", "reply", "voice")
    proc = abs_run(abs_home, "__guard-hook", spoken=spoken, stdin=hook_payload("the tests pass"))
    assert proc.returncode == 2

    _mark_turn_came_from_telegram(abs_home)
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/x"}})
    assert abs_run(abs_home, "__guard-hook", stdin=payload).returncode == 0


@needs_tts
def test_a_tool_that_is_neither_bash_nor_a_reply_is_left_alone(abs_home, spoken):
    abs_run(abs_home, "config", "reply", "voice")
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/etc/hosts"}})
    proc = abs_run(abs_home, "__guard-hook", spoken=spoken, stdin=payload)
    assert proc.returncode == 0
    assert spoken.settled() == []


# --- the promise the gate makes ----------------------------------------------
#
# `voice` mode suppresses the text on the promise that audio will arrive, and
# tells the model not to resend. Every case where that promise can be broken is a
# message the operator never receives, from their own machine, with nothing
# logged. These are the tests for the ways it was breakable.


@needs_tts
def test_a_repeated_reply_is_spoken_again_when_voice_is_the_only_copy(abs_home, spoken):
    """The dedup exists to stop double notes. Here it would mean total silence."""
    abs_run(abs_home, "config", "reply", "voice")
    for _ in range(2):
        proc = abs_run(abs_home, "__guard-hook", spoken=spoken,
                       stdin=hook_payload("the tests pass and I pushed it"))
        assert proc.returncode == 2      # text suppressed, both times
        time.sleep(2.0)
    assert spoken.lines() == ["the tests pass and I pushed it"] * 2


def test_a_repeated_reply_is_still_deduped_when_the_text_went_out(abs_home, spoken):
    """`both` keeps the dedup: the words are already on their phone."""
    abs_run(abs_home, "config", "reply", "both")
    for _ in range(2):
        abs_run(abs_home, "__silent-hook", spoken=spoken, stdin=hook_payload("the tests pass"))
        time.sleep(1.5)
    assert spoken.settled() == ["the tests pass"]


@needs_tts
def test_a_synthesis_failure_does_not_swallow_the_message(abs_home, spoken):
    """No audio came out, so the words have to reach them some other way."""
    abs_run(abs_home, "config", "reply", "voice")
    spoken.break_the_engine()
    proc = abs_run(abs_home, "__guard-hook", spoken=spoken,
                   stdin=hook_payload("the deploy finished and nothing else needs doing"))
    assert proc.returncode == 2
    time.sleep(3.0)
    assert spoken.settled() == []
    # The fallback is a direct Telegram send; with a fake token it fails, which is
    # fine — what matters is that the failure did not also poison the retry.
    spoken.fix_the_engine()
    abs_run(abs_home, "__guard-hook", spoken=spoken,
            stdin=hook_payload("the deploy finished and nothing else needs doing"))
    assert spoken.lines() == ["the deploy finished and nothing else needs doing"]


def test_a_failed_attempt_is_not_recorded_as_something_already_said(abs_home, spoken):
    """Stamping the hash before the attempt made one failure silence 5 minutes."""
    abs_run(abs_home, "config", "reply", "both")
    spoken.break_the_engine()
    abs_run(abs_home, "__silent-hook", spoken=spoken, stdin=hook_payload("all green, nothing left"))
    time.sleep(2.5)
    spoken.fix_the_engine()
    abs_run(abs_home, "__silent-hook", spoken=spoken, stdin=hook_payload("all green, nothing left"))
    assert spoken.lines() == ["all green, nothing left"]


# --- text that isn't ASCII ---------------------------------------------------
#
# Model output is full of em dashes and curly quotes, and the operator writes in
# more than one language. This went out through a shell -c string once, where a
# non-UTF-8 locale turned "cafe" into literal backslash escapes in the audio.


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_accents_and_dashes_survive_the_trip_to_the_engine(abs_home, spoken, locale):
    """Everything non-ASCII EXCEPT emoji has to arrive intact.

    The emoji used to be in this message too. It is gone from the expectation on
    purpose since 3.0.3 — the strip is the point — but the rest is the assertion
    that matters here, and it matters more now: the strip runs under LC_ALL=C on
    byte ranges that sit next to the em dash, the guillemets and the Cyrillic.
    """
    abs_run(abs_home, "config", "reply", "both")
    message = "Déploiement terminé — café ready 🆕 «готово»"
    heard = "Déploiement terminé — café ready «готово»"
    abs_run(abs_home, "__silent-hook", spoken=spoken, stdin=hook_payload(message),
            LC_ALL=locale, LANG=locale)
    assert spoken.lines() == [heard]


def test_a_reply_that_is_only_emoji_is_not_spoken_as_nothing(abs_home, spoken):
    """Nothing sayable is left after prep, so it must not reach the engine at all."""
    abs_run(abs_home, "config", "reply", "both")
    abs_run(abs_home, "__silent-hook", spoken=spoken, stdin=hook_payload("👍"))
    assert spoken.settled() == []


def test_control_characters_never_reach_the_engine(abs_home, spoken):
    """A stray \\x01 used to become an unterminated quote and drop the message."""
    abs_run(abs_home, "config", "reply", "both")
    abs_run(abs_home, "__silent-hook", spoken=spoken,
            stdin=hook_payload("the tests \x01 pass \x07 cleanly"))
    assert spoken.lines() == ["the tests pass cleanly"]


def test_the_message_body_never_lands_in_a_process_command_line(abs_home, spoken, tmp_path):
    """/proc/<pid>/cmdline is world-readable for the ~30s synthesis takes."""
    argv_log = tmp_path / "argv.txt"
    stub = tmp_path / "argvstub.sh"
    stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{argv_log}"\ncat >/dev/null\n')
    stub.chmod(0o755)
    abs_run(abs_home, "config", "reply", "both")
    abs_run(abs_home, "__silent-hook", stdin=hook_payload("the token is 123456789:AAHsecret"),
            ABS_VOICE_CMD=f"/bin/sh {stub}")
    deadline = time.time() + 8
    while time.time() < deadline and not argv_log.exists():
        time.sleep(0.1)
    assert argv_log.exists(), "the stub was never invoked"
    assert "AAHsecret" not in argv_log.read_text()
