"""`abs send` — the outbound path that does not depend on the Telegram plugin.

This exists because of a failure the operator actually hit. The plugin runs as an
MCP server; it dropped mid-task, the session's `reply` tool disappeared with it, and
the session had no other way to reach him. It finished the work, wrote its report to
a terminal nobody was watching, and he waited in silence — from a tool whose entire
promise is that you hear from it.

`abs say` was the only other outbound path, and it is not a substitute: it needs the
TTS venvs and ffmpeg, it delivers audio, and it cannot carry a link or a command.
This one is plain text over the same `tg_send` the daemon already uses, and it needs
nothing but the token.

Telegram is stubbed with a `curl` earlier on PATH that records the request body, so
these tests send nothing and touch no network.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ABS_SH = REPO / "abs.sh"
PROFILE = "sendtest"

_STUB_CURL = """#!/bin/sh
args="$*"
case "$args" in *-K*) cat >/dev/null 2>&1 ;; esac
body=""
while [ $# -gt 0 ]; do
  case "$1" in --data-binary) body="$2"; shift 2 ;; *) shift ;; esac
done
printf '%s' "$body" | tr -d '\\n' >> "$ABS_TEST_SENT"
printf '\\n' >> "$ABS_TEST_SENT"
[ -f "$ABS_TEST_REJECT" ] && { printf '{"ok":false,"description":"chat not found"}'; exit 0; }
printf '{"ok":true,"result":{"message_id":3}}'
"""


@pytest.fixture
def box(tmp_path):
    tg = tmp_path / "tg"
    tg.mkdir()
    (tg / ".env").write_text("TELEGRAM_BOT_TOKEN=1:fake\n")
    prof = tmp_path / ".abs" / "profiles" / PROFILE
    prof.mkdir(parents=True)
    (prof / "rc.json").write_text(json.dumps({
        "bot": "b", "chat_id": "42", "tg_dir": str(tg),
    }))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    curl = bindir / "curl"
    curl.write_text(_STUB_CURL)
    curl.chmod(0o755)
    sent = tmp_path / "sent.log"
    sent.write_text("")
    reject = tmp_path / "reject"

    class Box:
        rc = prof / "rc.json"

        def run(self, *args, stdin=""):
            env = dict(os.environ)
            for k in list(env):
                if k.startswith("ABS_") or k.startswith("TELEGRAM_"):
                    env.pop(k, None)
            env.update(
                HOME=str(tmp_path), ABS_HOME=str(tmp_path / ".abs"),
                PATH=f"{bindir}:{env.get('PATH', '')}",
                ABS_TEST_SENT=str(sent), ABS_TEST_REJECT=str(reject),
            )
            return subprocess.run(
                ["bash", str(ABS_SH), "--profile", PROFILE, "send", *args],
                input=stdin, capture_output=True, text=True, env=env, timeout=60,
            )

        def sent(self):
            return [json.loads(l) for l in sent.read_text().splitlines() if l.strip()]

        def make_telegram_reject(self):
            reject.write_text("")

        def set(self, **kw):
            data = json.loads(self.rc.read_text())
            data.update(kw)
            self.rc.write_text(json.dumps(data))

    return Box()


def test_a_message_reaches_the_chat(box):
    run = box.run("the bridge dropped, sending directly")
    assert run.returncode == 0, run.stdout + run.stderr
    (msg,) = box.sent()
    assert msg["text"] == "the bridge dropped, sending directly"
    assert msg["chat_id"] == "42"


def test_several_words_are_one_message_not_several(box):
    """`abs send these are words` is the shape someone types without thinking about
    quoting; joining the arguments is friendlier than sending only the first."""
    box.run("tests", "pass", "and", "the", "merge", "is", "clean")
    (msg,) = box.sent()
    assert msg["text"] == "tests pass and the merge is clean"


def test_stdin_carries_a_multi_line_report(box):
    """The realistic use: a report has newlines, and quoting a wall of text through
    a shell is exactly the friction that makes a fallback go unused."""
    run = box.run("-", stdin="line one\nline two\nline three")
    assert run.returncode == 0, run.stderr
    (msg,) = box.sent()
    assert msg["text"] == "line one\nline two\nline three"


def test_no_arguments_also_reads_stdin(box):
    run = box.run(stdin="piped with no dash")
    assert run.returncode == 0, run.stderr
    assert box.sent()[0]["text"] == "piped with no dash"


def test_an_empty_message_is_refused_rather_than_sent(box):
    run = box.run("-", stdin="")
    assert run.returncode != 0
    assert box.sent() == []
    assert "Nothing to send" in run.stdout + run.stderr


def test_a_rejection_from_telegram_is_reported_not_swallowed(box):
    """The whole point of this path is that the operator finds out. A sender that
    fails quietly would reproduce the bug it exists to fix."""
    box.make_telegram_reject()
    run = box.run("hello")
    assert run.returncode != 0
    combined = run.stdout + run.stderr
    assert "chat not found" in combined, combined


def test_an_over_long_message_is_truncated_and_says_so(box):
    """Telegram's ceiling is 4096. Sending nothing would be the worst outcome, so it
    trims and warns rather than failing."""
    run = box.run("x" * 5000)
    assert run.returncode == 0, run.stderr
    (msg,) = box.sent()
    assert len(msg["text"]) == 4096
    assert "4096" in run.stdout + run.stderr


def test_an_unpaired_profile_says_what_to_do(box):
    box.set(chat_id=None)
    run = box.run("hello")
    assert run.returncode != 0
    assert box.sent() == []


def test_it_is_offered_in_the_help(box):
    """A fallback nobody knows about is not a fallback. It has to be in `abs help`
    and in the injected prompt — this covers the first half."""
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("ABS_") or k.startswith("TELEGRAM_"):
            env.pop(k, None)
    out = subprocess.run(["bash", str(ABS_SH), "help"], capture_output=True,
                         text=True, env=env).stdout
    assert "abs send" in out


def test_the_prompt_tells_the_agent_about_it(box):
    """The second half, and the one that actually gets used: the session has to know
    to reach for this when the reply tool is gone."""
    body = "".join(l for l in open(ABS_SH) if l.strip() != 'main "$@"')
    script = Path(os.environ.get("TMPDIR", "/tmp")) / "abs_prompt_probe.sh"
    script.write_text(body + f"\nuse_profile {PROFILE}\nbuild_prompt 42\n")
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("ABS_") or k.startswith("TELEGRAM_"):
            env.pop(k, None)
    env.update(HOME=str(box.rc.parents[2].parent), ABS_HOME=str(box.rc.parents[2]))
    prompt = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                            env=env).stdout
    assert "send" in prompt
    assert "DO NOT GO SILENT" in prompt
