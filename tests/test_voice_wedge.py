"""A hung speech engine must cost you a note, never a message.

The bug, reported from a Mac and reproduced here: three replies produced three
TTS processes, all wedged, none finishing. The operator got the text and never
the audio.

Two defects, and Linux only looked healthy because one of them hid the other.

**Serialisation was `flock`, which is Linux-only.** The old code knew and called
the macOS case "a weaker ordering guarantee, not a failure". It was a failure: on
a Mac every reply started its own synthesis, so three replies meant three copies
of a multi-gigabyte model loading at once. `mkdir` is the portable atomic
test-and-set and is what guards it now.

**There was no timeout anywhere, on any platform.** flock kept Linux to one
process at a time, so a hang looked like slowness rather than a wedge — but a
single hung engine wedges Linux just as hard, and in voice-first mode it takes
the TEXT with it, because the words are held until the note has gone. That is the
worse failure, and it is the one these tests are really about.

Everything here drives `_voice_mirror` and the voice-first worker directly with a
stub engine, so nothing spends a minute in a real speech model.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ABS_SH = REPO / "abs.sh"

PROFILE = "wedgetest"


@pytest.fixture
def box(tmp_path):
    """A paired profile, a stub engine whose behaviour the test chooses, and a
    stub curl so nothing reaches Telegram."""
    home = tmp_path / "abshome"
    tg = tmp_path / "tg"
    tg.mkdir()
    (tg / ".env").write_text("TELEGRAM_BOT_TOKEN=123:fake\n")
    prof = home / "profiles" / PROFILE
    prof.mkdir(parents=True)
    (prof / "rc.json").write_text(json.dumps({
        "bot": "testbot", "chat_id": 42, "tg_dir": str(tg), "reply_mode": "both",
    }))

    log = tmp_path / "events.log"
    hang_for = tmp_path / "hang_for"

    # Reads the text on stdin like the real engines do, then sleeps for however
    # long the test asked. "wedged" is just a very long sleep.
    tts = tmp_path / "tts.sh"
    tts.write_text(
        "#!/bin/sh\n"
        'text="$(cat)"\n'
        f'printf "START %s\\n" "$text" >> "{log}"\n'
        f'sleep "$(cat {hang_for} 2>/dev/null || echo 0)"\n'
        f'printf "DONE %s\\n" "$text" >> "{log}"\n'
    )
    tts.chmod(0o755)
    hang_for.write_text("0")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    curl = bindir / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        'cfg="$(cat)"\n'
        'body=""\n'
        "while [ $# -gt 0 ]; do\n"
        '  case "$1" in --data-binary) body="$2"; shift 2 ;; *) shift ;; esac\n'
        "done\n"
        f'printf "TEXT %s\\n" "$(printf %s "$body" | tr -d \'\\n\')" >> "{log}"\n'
        'printf \'{"ok":true,"result":{"message_id":7}}\'\n'
    )
    curl.chmod(0o755)

    class Box:
        abs_home = home
        rc = prof / "rc.json"
        lock = prof / "voice.lock.d"

        def hangs_for(self, secs):
            hang_for.write_text(str(secs))

        def events(self):
            return [l for l in log.read_text().splitlines() if l.strip()] if log.exists() else []

        def tags(self):
            return [l.split(" ", 1)[0] for l in self.events()]

        def set(self, **kw):
            d = json.loads(self.rc.read_text())
            d.update(kw)
            self.rc.write_text(json.dumps(d))

        def env(self, **extra):
            e = dict(os.environ, ABS_HOME=str(home),
                     ABS_VOICE_CMD=f"/bin/sh {tts}",
                     PATH=f"{bindir}:{os.environ.get('PATH', '')}")
            e.pop("TELEGRAM_STATE_DIR", None)
            e.update(extra)
            return e

        def run(self, *args, **extra):
            """Any abs subcommand against this throwaway profile."""
            return subprocess.run(
                ["bash", str(ABS_SH), "--profile", PROFILE, *args],
                capture_output=True, text=True, env=self.env(**extra), timeout=60)

        def call(self, snippet, timeout=120, **extra):
            """Run a function inside abs.sh with `main` never running."""
            body = "".join(l for l in open(ABS_SH) if l.strip() != 'main "$@"')
            script = tmp_path / "call.sh"
            script.write_text(f"{body}\nuse_profile {PROFILE}\n{snippet}\n")
            return subprocess.run(["bash", str(script)], capture_output=True,
                                  text=True, env=self.env(**extra), timeout=timeout)

    return Box()


def _hook_gate(box, text):
    """Run the PreToolUse guard on a reply and return its exit code.

    2 means the gate took the message (blocked the tool, will speak it); 0 means
    it declined and the plugin sends text as normal.
    """
    payload = json.dumps({
        "tool_name": "mcp__plugin_telegram_telegram__reply",
        "session_id": "s-1",
        "tool_input": {"chat_id": "42", "text": text},
    })
    return subprocess.run(
        ["bash", str(ABS_SH), "--profile", PROFILE, "__guard-hook"],
        input=payload, capture_output=True, text=True, env=box.env(), timeout=90,
    ).returncode


# ---- the timeout --------------------------------------------------------------


def test_a_wedged_engine_is_abandoned_rather_than_waited_on(box):
    """The whole bug in one assertion. Without a timeout this call never returns."""
    box.hangs_for(600)
    started = time.time()
    out = box.call('_voice_mirror "the suite is green and the daemon came back" || echo GAVEUP',
                   timeout=60, ABS_VOICE_TIMEOUT="3")
    elapsed = time.time() - started
    assert "GAVEUP" in out.stdout, "it reported success for a note that never happened"
    assert elapsed < 40, f"took {elapsed:.0f}s — it waited on the hung engine"
    assert "START" in " ".join(box.tags()), "the engine was never invoked at all"
    assert "DONE" not in " ".join(box.tags()), "the stub cannot have finished"


def test_the_lock_is_released_when_synthesis_times_out(box):
    """Otherwise the first wedge is the last note this profile ever sends."""
    box.hangs_for(600)
    box.call('_voice_mirror "the first message, which will hang"',
             timeout=60, ABS_VOICE_TIMEOUT="3")
    assert not box.lock.exists(), "the lock survived the timeout"

    box.hangs_for(0)
    started = time.time()
    box.call('_voice_mirror "the second message, which should be spoken"',
             timeout=60, ABS_VOICE_TIMEOUT="30")
    assert time.time() - started < 30
    assert any(l.startswith("DONE ") and "second message" in l for l in box.events()), box.events()


def test_a_timed_out_note_is_not_recorded_as_spoken(box):
    """The dedup stamp is written only on success. Stamping a failure would make
    the retry of that sentence silently do nothing for five minutes."""
    box.hangs_for(600)
    box.call('_voice_mirror "a message that will not survive synthesis"',
             timeout=60, ABS_VOICE_TIMEOUT="3")
    rc = json.loads(box.rc.read_text())
    assert "last_voice_hash" not in rc, rc


# ---- serialisation, on every OS ----------------------------------------------


def test_two_notes_do_not_synthesise_at_the_same_time(box):
    """The macOS failure: `flock` is Linux-only, so every reply started its own
    copy of the model. mkdir is the portable atomic test-and-set."""
    box.hangs_for(4)
    body = "".join(l for l in open(ABS_SH) if l.strip() != 'main "$@"')
    script = box.rc.parent / "first.sh"
    script.write_text(f'{body}\nuse_profile {PROFILE}\n_voice_mirror "the first message here"\n')
    first = subprocess.Popen(
        ["bash", str(script)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=box.env(),
    )
    time.sleep(1.5)                      # let the first one take the lock
    assert box.lock.exists(), "no lock was taken at all"
    box.call('_voice_mirror "the second message here"', timeout=120)
    first.wait(timeout=60)

    ev = box.events()
    starts = [i for i, l in enumerate(ev) if l.startswith("START ")]
    assert len(starts) == 2, ev
    # The second START must come after the first DONE — never interleaved.
    first_done = next(i for i, l in enumerate(ev) if l.startswith("DONE "))
    assert starts[1] > first_done, f"the two engines overlapped: {ev}"


def test_a_lock_held_by_a_dead_process_is_reaped_immediately(box):
    """Age alone would make this wait out a full synthesis timeout — six minutes
    of silence queued behind a process that no longer exists. The holder's pid is
    the fast signal, and it is checked first."""
    box.lock.mkdir(parents=True)
    (box.lock / "ts").write_text(str(int(time.time())))     # fresh: age says "wait"
    (box.lock / "pid").write_text("2147483646")             # but nothing is there
    started = time.time()
    out = box.call('_voice_lock_acquire && echo GOT || echo BLOCKED',
                   timeout=60, ABS_VOICE_LOCK_WAIT="30", ABS_VOICE_TIMEOUT="600")
    assert "GOT" in out.stdout, out.stdout + out.stderr
    assert time.time() - started < 15, "it waited on the age instead of the pid"


def test_a_live_holder_is_not_reaped_just_because_it_is_slow(box):
    """The other direction. Reaping on sight would let two engines run at once,
    which is the bug this whole file is about."""
    box.lock.mkdir(parents=True)
    (box.lock / "ts").write_text(str(int(time.time())))
    (box.lock / "pid").write_text(str(os.getpid()))          # very much alive
    out = box.call('_voice_lock_acquire && echo GOT || echo BLOCKED',
                   timeout=60, ABS_VOICE_LOCK_WAIT="4", ABS_VOICE_TIMEOUT="600")
    assert "BLOCKED" in out.stdout, out.stdout


def test_a_lock_left_by_a_dead_process_does_not_silence_voice_forever(box):
    """A machine that is rebooted mid-note, or an engine killed by the OOM killer,
    must not mean this profile never speaks again."""
    box.lock.mkdir(parents=True)
    (box.lock / "ts").write_text(str(int(time.time()) - 100_000))
    box.hangs_for(0)
    box.call('_voice_mirror "this should still be spoken"',
             timeout=60, ABS_VOICE_TIMEOUT="30")
    assert any(l.startswith("DONE ") for l in box.events()), box.events()


def test_a_lock_with_no_timestamp_is_adopted_rather_than_trusted_forever(box):
    """A process killed between mkdir and the stamp write leaves a lock with no
    `ts`, and there is no way to tell that from a lock taken a millisecond ago.

    So it is stamped now rather than deleted on sight — deleting would race a
    live holder — and it then ages out like any other. What must not happen is
    "no stamp, therefore wait forever".
    """
    box.lock.mkdir(parents=True)
    assert not (box.lock / "ts").exists()
    box.call('_voice_lock_acquire >/dev/null 2>&1 || true',
             timeout=60, ABS_VOICE_LOCK_WAIT="2", ABS_VOICE_TIMEOUT="1",
             ABS_VOICE_LOCK_STALE_MARGIN="1")
    assert (box.lock / "ts").exists(), "it was neither adopted nor cleared"

    # And once the adopted stamp is old enough, the next caller takes it.
    time.sleep(3)
    box.hangs_for(0)
    out = box.call('_voice_lock_acquire && echo GOT || echo BLOCKED',
                   timeout=60, ABS_VOICE_LOCK_WAIT="6", ABS_VOICE_TIMEOUT="1",
                   ABS_VOICE_LOCK_STALE_MARGIN="1")
    assert "GOT" in out.stdout, out.stdout + out.stderr


def test_waiting_gives_up_rather_than_hanging(box):
    """A caller that cannot get the lock must return, so `voice` mode can fall
    back to text instead of the message evaporating."""
    box.lock.mkdir(parents=True)
    (box.lock / "ts").write_text(str(int(time.time())))
    started = time.time()
    out = box.call('_voice_lock_acquire && echo GOT || echo BLOCKED',
                   timeout=60, ABS_VOICE_LOCK_WAIT="4", ABS_VOICE_TIMEOUT="600")
    assert "BLOCKED" in out.stdout, out.stdout
    assert time.time() - started < 30


# ---- what the operator actually experiences ----------------------------------


def test_in_voice_only_mode_a_wedged_engine_still_delivers_the_words(box):
    """`voice` suppresses the text on the promise that audio will arrive. If the
    engine wedges, that promise has to be paid out as text — the alternative is a
    message the operator never receives and never knows about."""
    box.set(reply_mode="voice")
    box.hangs_for(600)
    box.call('_voice_mirror "the deploy finished and the migration ran clean"',
             timeout=60, ABS_VOICE_TIMEOUT="3")
    sent = [l for l in box.events() if l.startswith("TEXT ")]
    assert sent, f"the message vanished entirely: {box.events()}"
    assert "voice failed" in sent[-1]
    assert "migration ran clean" in sent[-1]


def test_in_voice_first_mode_a_wedged_engine_still_delivers_the_text(box):
    """The worst case of the original bug, and the reason the timeout matters more
    than the lock: voice-first holds the words until the note has gone, so a hung
    engine loses the message outright rather than just the audio."""
    box.hangs_for(600)
    payload = json.dumps({
        "text": "the suite is green and the daemon came back up on its own",
        "chat": "42",
        "lead": "the suite is green and the daemon came back up on its own",
    })
    body = "".join(l for l in open(ABS_SH) if l.strip() != 'main "$@"')
    script = box.rc.parent / "worker.sh"
    script.write_text(f"{body}\nuse_profile {PROFILE}\ncmd_voice_then_text\n")
    started = time.time()
    subprocess.run(["bash", str(script)], input=payload, capture_output=True,
                   text=True, env=box.env(ABS_VOICE_FIRST_TIMEOUT="3"), timeout=90)
    assert time.time() - started < 60

    sent = [l for l in box.events() if l.startswith("TEXT ")]
    assert sent, f"the text never went out: {box.events()}"
    assert "daemon came back up" in sent[-1]


# ---- saying so when the note did not make it ---------------------------------
#
# The operator waited five minutes on a Mac running the slow engine, got text,
# and had no way to tell whether voice had failed, was still coming, or had never
# been switched on. Silence about a failure is what turns "degraded" into "broken"
# in the only opinion that matters — the person holding the phone.


def _worker(box, tmp_path, text, **env):
    body = "".join(l for l in open(ABS_SH) if l.strip() != 'main "$@"')
    script = box.rc.parent / "vftworker.sh"
    script.write_text(f"{body}\nuse_profile {PROFILE}\ncmd_voice_then_text\n")
    payload = json.dumps({"text": text, "chat": "42", "lead": text})
    return subprocess.run(["bash", str(script)], input=payload, capture_output=True,
                          text=True, env=box.env(**env), timeout=120)


def test_a_failed_note_says_so_in_the_text(box, tmp_path):
    box.hangs_for(600)
    _worker(box, tmp_path, "the deploy finished and the migration ran clean",
            ABS_VOICE_FIRST_TIMEOUT="3")
    sent = [l for l in box.events() if l.startswith("TEXT ")]
    assert sent, box.events()
    assert "voice note didn" in sent[-1], sent[-1]
    assert "migration ran clean" in sent[-1]


def test_a_note_that_worked_adds_no_apology(box, tmp_path):
    """The line has to be rare enough to mean something. On the happy path it
    must not appear at all."""
    box.hangs_for(0)
    _worker(box, tmp_path, "the deploy finished and the migration ran clean",
            ABS_VOICE_FIRST_TIMEOUT="60")
    sent = [l for l in box.events() if l.startswith("TEXT ")]
    assert sent, box.events()
    assert "voice note didn" not in sent[-1], sent[-1]


def test_the_text_is_not_held_for_the_full_synthesis_ceiling(box, tmp_path):
    """Voice-first holds the words until the note is made. The ceiling that stops
    a wedged engine hanging forever is far too long to be the ceiling on how long
    a written answer waits — those are different questions with different answers.
    """
    box.hangs_for(600)
    started = time.time()
    _worker(box, tmp_path, "the deploy finished and the migration ran clean",
            ABS_VOICE_FIRST_TIMEOUT="3", ABS_VOICE_TIMEOUT="600")
    elapsed = time.time() - started
    assert elapsed < 40, f"the text waited {elapsed:.0f}s — it used the wrong budget"
    assert [l for l in box.events() if l.startswith("TEXT ")]


# ---- voice earns its place by length -----------------------------------------
#
# The operator's rule, and his reasoning: "the user doesn't want to read more, but
# hearing everything is easier." A short answer is faster to read than to listen
# to; a long one is the opposite. So in mode `both`, only a long reply is spoken.
#
# The two paths have to agree. The voice-first gate and the PostToolUse mirror
# both send notes, by different routes, and a rule applied to one of them is not a
# rule — it is a coin toss decided by whether voice-first happens to be on.

SHORT = "Pushed and green. Nothing left on my side."
LONG = " ".join(["the deploy finished and the migration ran clean"] * 60)   # ~480 words


def test_a_short_reply_is_not_spoken(box, tmp_path):
    box.hangs_for(0)
    assert _hook_gate(box, SHORT) == 0, "the gate should decline and let text through"
    assert "VOICE" not in " ".join(box.tags()), box.events()


def test_a_long_reply_is_spoken(box, tmp_path):
    """Exit 2 means the gate took the message: it blocked the tool's own send and
    handed the reply to the worker that speaks it and then sends the text.

    That decision IS the length rule, and it is what this test is about. Whether
    the detached worker then reaches the engine is covered above, by the tests
    that drive `cmd_voice_then_text` directly rather than through a `setsid`.
    """
    box.hangs_for(0)
    assert _hook_gate(box, LONG) == 2, "the gate should take a long reply"


def test_the_mirror_applies_the_same_rule(box, tmp_path):
    """Without this the setting does nothing: the gate declines a short reply and
    the PostToolUse mirror speaks it anyway, so whether you hear it depends on
    whether voice-first happens to be on."""
    box.hangs_for(0)
    out = box.call(f'_voice_long_enough {SHORT!r} && echo SPEAK || echo QUIET')
    assert "QUIET" in out.stdout, out.stdout
    out = box.call(f'_voice_long_enough {LONG!r} && echo SPEAK || echo QUIET')
    assert "SPEAK" in out.stdout, out.stdout


def test_voice_only_mode_ignores_the_length_rule(box, tmp_path):
    """The important exemption. In mode `voice` the note REPLACES the text, so a
    length floor would mean a short reply is never delivered at all. Silence is
    not a shorter message."""
    box.set(reply_mode="voice")
    out = box.call(f'_voice_long_enough {SHORT!r} && echo SPEAK || echo QUIET')
    assert "SPEAK" in out.stdout, out.stdout


def test_the_threshold_is_tunable(box, tmp_path):
    out = box.call(f'_voice_long_enough {SHORT!r} && echo SPEAK || echo QUIET',
                   ABS_VOICE_MIN_WORDS="3")
    assert "SPEAK" in out.stdout, out.stdout


def test_length_is_judged_on_the_whole_reply_not_the_spoken_part(box, tmp_path):
    """A long report whose first paragraph is brisk is still a long report. It is
    the length of the thing you would otherwise have to READ that decides this."""
    text = "Done.\n\n" + LONG
    out = box.call(f'_voice_long_enough {text!r} && echo SPEAK || echo QUIET')
    assert "SPEAK" in out.stdout, out.stdout


# ---- the threshold is a guess, so it has to be tunable -----------------------
#
# It shipped at 300 words and that was wrong by a lot: a four-item status report
# came out at 273 and stayed silent, which was exactly the message the operator
# wanted to hear. The number is stored now rather than compiled in, because the
# right value is a matter of feel and finding it should not cost a release.

MEDIUM = " ".join(["word"] * 200)


def test_the_default_speaks_a_two_hundred_word_report(box):
    """273 words is a status report. If that is silent the feature is not doing
    the job it was asked to do."""
    out = box.call(f'_voice_long_enough {MEDIUM!r} && echo SPEAK || echo QUIET')
    assert "SPEAK" in out.stdout, out.stdout


def test_the_threshold_can_be_stored_without_a_release(box):
    assert box.run("config", "voice-words", "500").returncode == 0
    out = box.call(f'_voice_long_enough {MEDIUM!r} && echo SPEAK || echo QUIET')
    assert "QUIET" in out.stdout, out.stdout


def test_clearing_it_returns_to_the_default(box):
    box.run("config", "voice-words", "500")
    assert box.run("config", "voice-words", "--clear").returncode == 0
    out = box.call(f'_voice_long_enough {MEDIUM!r} && echo SPEAK || echo QUIET')
    assert "SPEAK" in out.stdout, out.stdout


def test_the_env_override_still_wins(box):
    """The env var is what the tests use to pin this off; a stored value must not
    be able to break them."""
    box.run("config", "voice-words", "1")
    out = box.call(f'_voice_long_enough {MEDIUM!r} && echo SPEAK || echo QUIET',
                   ABS_VOICE_MIN_WORDS="5000")
    assert "QUIET" in out.stdout, out.stdout


def test_a_non_number_is_refused(box):
    out = box.run("config", "voice-words", "lots")
    assert out.returncode != 0
    assert "voice-words" in out.stderr
