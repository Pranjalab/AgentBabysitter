"""Voice before text: in reply mode `both`, the note arrives first.

The mirror sends text and then speaks it, which is backwards for someone holding
a phone — by the time the note plays they have already read the words. Voice-first
blocks the reply tool's own send and hands the message to one worker that speaks
it and *then* sends the text.

That worker is now the only thing that will ever deliver the message, so the
assertions here are mostly about the failure directions:

  * order — the note goes out before the words, which is the whole feature;
  * the text goes out even when synthesis fails, and exactly once;
  * every case the gate declines (an attachment, formatted text, a code block, a
    link, a wall of text) falls back to the old order rather than to silence.

Both halves are stubbed and share one log file, so the *order* they were called
in is what the test reads: TTS through ABS_VOICE_CMD, Telegram through a `curl`
earlier on PATH. Nothing here touches the network or a speech model.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ABS_SH = os.path.join(REPO, "abs.sh")

PROFILE = "vftest"
REPLY_TOOL = "mcp__plugin_telegram_telegram__reply"

SAYABLE = "Done. The suite is green and the daemon came back up on its own."


@pytest.fixture
def box(tmp_path):
    """A profile in mode `both`, plus stubbed TTS and Telegram sharing one log.

    ``log`` is the assertion surface: each stub appends a tagged line, so
    ``["VOICE …", "TEXT …"]`` means the note went first and ``["TEXT …"]`` means
    the gate declined and the plugin's own send was left alone.
    """
    home = tmp_path / "abshome"
    tg = tmp_path / "tg"
    tg.mkdir()
    (tg / ".env").write_text("TELEGRAM_BOT_TOKEN=123:fake\n")
    prof = home / "profiles" / PROFILE
    prof.mkdir(parents=True)
    (prof / "rc.json").write_text(json.dumps({
        "bot": "testbot", "chat_id": 42, "tg_dir": str(tg), "reply_mode": "both",
    }))

    log = tmp_path / "order.log"
    fail = tmp_path / "tts_fails"

    tts = tmp_path / "tts.sh"
    tts.write_text(
        "#!/bin/sh\n"
        'text="$(cat)"\n'
        f'[ -f "{fail}" ] && exit 1\n'
        f'printf "VOICE %s\\n" "$text" >> "{log}"\n'
    )
    tts.chmod(0o755)

    # Stands in for curl. tg_api hands the url over stdin via `-K -` and the JSON
    # body in argv, so the body is readable without ever holding the token.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    curl = bindir / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        "cat >/dev/null 2>&1\n"
        'body=""\n'
        "while [ $# -gt 0 ]; do\n"
        '  case "$1" in --data-binary) body="$2"; shift 2 ;; *) shift ;; esac\n'
        "done\n"
        # One line per send. jq pretty-prints the request body, and a multi-line
        # entry would make "what arrived, in what order" unreadable.
        f'printf "TEXT %s\\n" "$(printf %s "$body" | tr -d \'\\n\')" >> "{log}"\n'
        'printf \'{"ok":true,"result":{"message_id":7}}\'\n'
    )
    curl.chmod(0o755)

    class Box:
        abs_home = home
        tts_cmd = f"/bin/sh {tts}"
        path = f"{bindir}:{os.environ.get('PATH', '')}"
        rc = prof / "rc.json"

        def break_tts(self):
            fail.write_text("")

        def order(self, wait=8.0):
            """The worker is detached on purpose, so give it a moment to land."""
            deadline = time.time() + wait
            while time.time() < deadline:
                if log.exists() and log.read_text().strip():
                    break
                time.sleep(0.1)
            time.sleep(1.2)  # and a beat more, so a *second* line can show up
            if not log.exists():
                return []
            return [l for l in log.read_text().splitlines() if l.strip()]

        def tags(self, wait=8.0):
            return [l.split(" ", 1)[0] for l in self.order(wait)]

        def set(self, **kw):
            data = json.loads(self.rc.read_text())
            data.update(kw)
            self.rc.write_text(json.dumps(data))

    return Box()


def _hook(box, tool_input, tool=REPLY_TOOL, **extra):
    """Feed the PreToolUse hook exactly what Claude Code feeds it."""
    payload = json.dumps({
        "tool_name": tool, "session_id": "s-1", "tool_input": tool_input,
    })
    env = dict(
        os.environ,
        ABS_HOME=str(box.abs_home),
        ABS_VOICE_CMD=box.tts_cmd,
        PATH=box.path,
    )
    env.pop("TELEGRAM_STATE_DIR", None)
    env.update(extra)
    return subprocess.run(
        ["bash", ABS_SH, "--profile", PROFILE, "__guard-hook"],
        input=payload, capture_output=True, text=True, env=env,
    )


def _reply(text=SAYABLE, **kw):
    out = {"chat_id": "42", "text": text}
    out.update(kw)
    return out


# ---- the feature -------------------------------------------------------------


def test_the_note_goes_out_before_the_text(box):
    run = _hook(box, _reply())
    assert run.returncode == 2, run.stderr
    assert box.tags() == ["VOICE", "TEXT"]


def test_the_blocked_reply_tells_the_model_not_to_resend(box):
    run = _hook(box, _reply())
    assert "do NOT resend" in run.stderr
    assert "voice note first" in run.stderr


def test_the_words_spoken_and_the_words_sent_are_the_same_message(box):
    _hook(box, _reply())
    lines = box.order()
    spoken = lines[0].split(" ", 1)[1]
    sent = json.loads(lines[1].split(" ", 1)[1])
    assert "suite is green" in spoken
    assert sent["text"] == SAYABLE
    assert str(sent["chat_id"]) == "42"


def test_the_reply_resets_auto_silence_even_though_posttooluse_never_runs(box):
    """Blocking the tool skips PostToolUse, where this bookkeeping used to live.

    Without it a voice-first session would drift toward auto-silence while
    replying perfectly well — muting itself for being too quiet.
    """
    box.set(terminal_streak=4, auto_silent=True)
    _hook(box, _reply())
    state = json.loads(box.rc.read_text())
    assert state["terminal_streak"] == 0
    assert state["auto_silent"] is False


# ---- the failure directions --------------------------------------------------


def test_a_failed_synthesis_still_delivers_the_text(box):
    """The nicety is the audio. The message is not optional."""
    box.break_tts()
    run = _hook(box, _reply())
    assert run.returncode == 2
    assert box.tags() == ["TEXT"]


def test_the_text_is_sent_exactly_once(box):
    _hook(box, _reply())
    assert box.tags().count("TEXT") == 1


def test_a_reply_with_nowhere_to_send_it_is_not_blocked(box):
    """Blocking a message the worker cannot address would lose it outright.

    The chat id normally rides along with the reply and falls back to the stored
    pairing. With neither, the gate has to decline and leave it to the plugin,
    which knows its own chat.
    """
    box.set(chat_id=None)
    run = _hook(box, {"text": SAYABLE})
    assert run.returncode == 0
    assert box.order(wait=2.0) == []


# ---- what it declines, and why ----------------------------------------------


@pytest.mark.parametrize(
    "tool_input, why",
    [
        (_reply(files=["/tmp/x.png"]), "an attachment travels with its message"),
        (_reply(format="markdownv2"), "the plugin owns the escaping"),
        (_reply(text="Here:\n```py\nprint(1)\n```"), "code is for copying"),
        (_reply(text="It's at https://example.com/report — have a look"), "a link is for tapping"),
        (_reply(text="word " * 400), "too long to listen to"),
        (_reply(text="ok"), "not worth a note"),
    ],
)
def test_the_gate_declines_and_lets_the_text_go(box, tool_input, why):
    run = _hook(box, tool_input)
    assert run.returncode == 0, f"{why}: {run.stderr}"


def test_a_declined_reply_is_left_entirely_to_the_normal_path(box):
    """Declining must not half-act: no note, no send, no bookkeeping — the reply
    tool and the PostToolUse mirror handle it exactly as before."""
    _hook(box, _reply(text="See https://example.com"))
    assert box.order(wait=2.0) == []


def test_mode_text_never_reorders_anything(box):
    box.set(reply_mode="text")
    run = _hook(box, _reply())
    assert run.returncode == 0
    assert box.order(wait=2.0) == []


def test_voice_first_off_keeps_the_old_order(box):
    box.set(no_voice_first=True)
    run = _hook(box, _reply())
    assert run.returncode == 0
    assert box.order(wait=2.0) == []


def test_a_non_reply_tool_is_none_of_this_gates_business(box):
    run = _hook(box, {"command": "ls"}, tool="Bash")
    assert run.returncode == 0
    assert box.order(wait=2.0) == []


# ---- the wiring --------------------------------------------------------------


_STUB_CLAUDE = """#!/usr/bin/env bash
case "${1:-}" in plugin) echo "telegram@claude-plugins-official"; exit 0 ;; esac
exit 0
"""
_STUB_NOOP = "#!/usr/bin/env bash\nexit 0\n"


def _machine_can_speak():
    """What ``voice_can_speak`` asks: an installed engine, and ffmpeg.

    Duplicated rather than probed through abs.sh because ``voice_root`` looks for
    the venvs *beside the script* — a copy sourced from elsewhere would answer for
    the wrong machine.
    """
    import shutil
    root = REPO
    kokoro = os.path.exists(f"{root}/.venv-kokoro/bin/python") and os.path.exists(
        f"{root}/speak_kokoro.py")
    chatter = os.path.exists(f"{root}/.venv-tts/bin/python") and os.path.exists(
        f"{root}/speak.py")
    return (kokoro or chatter) and shutil.which("ffmpeg") is not None


needs_tts = pytest.mark.skipif(
    not _machine_can_speak(), reason="no TTS installed; voice-first never arms"
)


def _launch_and_read_hooks(tmp_path, **rc):
    """Run the REAL launch far enough to write the session settings, then read them.

    The gate is only as good as its wiring: if the reply matcher is missing from
    hooks.json the hook never runs and voice-first silently does nothing, which no
    amount of testing the gate itself would notice. `--daemon-start` skips the
    interactive prompts and execs a stub `claude`, so this reaches the real
    settings writer without a session.
    """
    from tests.conftest import write_profile

    home = tmp_path / "launchhome"
    home.mkdir()
    abs_home = tmp_path / "launchabs"
    prof = write_profile(abs_home, "default", allow_ids=[42])
    data = json.loads((prof / "rc.json").read_text())
    data.update(rc)
    (prof / "rc.json").write_text(json.dumps(data))

    bindir = tmp_path / "launchbin"
    bindir.mkdir()
    for name, body in (("claude", _STUB_CLAUDE), ("curl", _STUB_NOOP), ("bun", _STUB_NOOP)):
        p = bindir / name
        p.write_text(body)
        p.chmod(0o755)

    env = dict(os.environ)
    for key in list(env):
        if key.startswith("ABS_") or key.startswith("TELEGRAM_") or key == "CLAUDERC_HOME":
            env.pop(key, None)
    env.update(HOME=str(home), ABS_HOME=str(abs_home),
               PATH=f"{bindir}:{env.get('PATH', '')}",
               ABS_REPO="http://127.0.0.1:1/never")
    subprocess.run(["bash", ABS_SH, "--profile", "default", "--daemon-start"],
                   capture_output=True, text=True, env=env, timeout=60, cwd=str(tmp_path))

    hooks = abs_home / "profiles" / "default" / "hooks.json"
    assert hooks.exists(), "the launch never wrote its settings"
    return json.loads(hooks.read_text())


def _reply_matchers(hooks):
    return [e.get("matcher") for e in hooks.get("hooks", {}).get("PreToolUse", [])
            if e.get("matcher") == REPLY_TOOL]


@needs_tts
def test_the_launch_arms_the_reply_hook_in_mode_both(tmp_path):
    hooks = _launch_and_read_hooks(tmp_path, reply_mode="both")
    assert _reply_matchers(hooks) == [REPLY_TOOL]


@needs_tts
def test_the_launch_does_not_arm_it_when_voice_first_is_off(tmp_path):
    hooks = _launch_and_read_hooks(tmp_path, reply_mode="both", no_voice_first=True)
    assert _reply_matchers(hooks) == []


def test_the_launch_does_not_arm_it_in_mode_text(tmp_path):
    """Mode `text` has no note to put first, so a session must not pay for a hook
    that would decline on every reply."""
    hooks = _launch_and_read_hooks(tmp_path, reply_mode="text")
    assert _reply_matchers(hooks) == []


def test_the_config_switch_round_trips(box):
    env = dict(os.environ, ABS_HOME=str(box.abs_home))
    env.pop("TELEGRAM_STATE_DIR", None)

    def cfg(*args):
        return subprocess.run(
            ["bash", ABS_SH, "--profile", PROFILE, "config", *args],
            capture_output=True, text=True, env=env,
        )

    assert "on" in cfg("voice-first").stdout + cfg("voice-first").stderr
    assert cfg("voice-first", "off").returncode == 0
    out = cfg("voice-first")
    assert "off" in out.stdout + out.stderr
    assert cfg("voice-first", "on").returncode == 0
    out = cfg("voice-first")
    assert "on" in out.stdout + out.stderr
    assert cfg("voice-first", "sideways").returncode != 0
