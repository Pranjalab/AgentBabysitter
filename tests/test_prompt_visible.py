"""A question nobody can see is not a question.

Reported 21 Oct, at the end of an install that had otherwise gone perfectly on a
new Mac:

    Voice needs uv — a fast Python env manager (installs to ~/.local/bin, no sudo).

      Install it yourself: curl -LsSf https://astral.sh/uv/install.sh | sh
    ✗ Then re-run: abs voice setup

The question between those two lines — "Install uv now? [y/N]" — was never
printed. `voice_ask` handed its prompt to bash's `read -p`, which writes the
prompt to *stderr*, and the same line carried a `2>/dev/null` that was there to
hide the error from a failed `/dev/tty` open. The prompt went into /dev/null with
it. The run looked hung, and whatever key was pressed to find out why became an
empty answer, which is "no". Voice did not install and nothing said why.

So these tests drive the real functions through a pty and read the screen back:
the prompt has to be ON it. A pipe cannot test this — the prompt goes to the
terminal, and a terminal is the thing that was missing from the test suite.
"""

from __future__ import annotations

import os
import pty
import select
import time

import pytest

ABS_SH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "abs.sh")


def _fn(name: str) -> str:
    """One function, lifted out of abs.sh verbatim, so the test runs the shipped
    code rather than a paraphrase of it."""
    src = open(ABS_SH).read()
    head = "\n%s() {\n" % name
    start = src.index(head) + 1
    end = src.index("\n}\n", start) + len("\n}\n")
    return src[start:end]


def _drive(tmp_path, fn: str, call: str, keys: str):
    """Run `call` in a pty, type `keys`, return (YES/NO, what was on screen)."""
    out_file = tmp_path / "result"
    script = tmp_path / "drive.sh"
    script.write_text(
        "set -uo pipefail\n"
        + _fn(fn)
        + '\nif %s; then printf YES > "%s"; else printf NO > "%s"; fi\n'
        % (call, out_file, out_file)
    )

    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - the child execs immediately
        os.execvpe("bash", ["bash", str(script)], dict(os.environ, TERM="dumb"))

    # Typed straight away: the tty holds it until the read is armed. Waiting for
    # the prompt to appear before typing would make the broken version hang for
    # the whole timeout instead of failing on the assertion that matters.
    time.sleep(0.3)
    os.write(fd, keys.encode())

    out = b""
    deadline = time.time() + 15
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if ready:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        if os.waitpid(pid, os.WNOHANG)[0]:
            while True:
                ready, _, _ = select.select([fd], [], [], 0.2)
                if not ready:
                    break
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
            break
    os.close(fd)
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass

    result = out_file.read_text().strip() if out_file.exists() else "<never returned>"
    return result, out.decode(errors="replace")


@pytest.mark.parametrize(
    "fn,keys,expected",
    [
        ("voice_ask", "y\r", "YES"),
        ("voice_ask", "n\r", "NO"),
        ("voice_ask", "\r", "NO"),              # [y/N] — enter means no
        ("ask_default_yes", "y\r", "YES"),
        ("ask_default_yes", "n\r", "NO"),
        ("ask_default_yes", "\r", "YES"),       # [Y/n] — enter means yes
    ],
)
def test_the_prompt_reaches_the_screen(tmp_path, fn, keys, expected):
    result, screen = _drive(tmp_path, fn, '%s "Install uv now? [y/N]"' % fn, keys)
    assert "Install uv now?" in screen, (
        "the question was never printed — this is the reported bug:\n%r" % screen)
    assert result == expected, "answered %s, expected %s:\n%r" % (result, expected, screen)


def test_with_no_terminal_at_all_it_declines_quietly(tmp_path):
    """No tty is not a yes, and it is not a crash either: the installer runs this
    from a `curl | bash` where /dev/tty may genuinely not open, and an unanswered
    question there must leave voice unbuilt rather than start a multi-gigabyte
    download nobody agreed to."""
    script = tmp_path / "drive.sh"
    out_file = tmp_path / "result"
    script.write_text(
        "set -uo pipefail\n"
        + _fn("voice_ask")
        + '\nif voice_ask "Install uv now? [y/N]"; then printf YES > "%s"; '
        'else printf NO > "%s"; fi\n' % (out_file, out_file)
    )
    import subprocess
    r = subprocess.run(["setsid", "bash", str(script)], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert out_file.read_text().strip() == "NO"
    assert r.stderr == "", "it printed an error at the operator: %r" % r.stderr


def test_the_uv_question_defaults_to_yes_and_says_which_key():
    """It gates the voice setup the operator has just agreed to, so a default of
    no is a trap even once the prompt is visible. Reported 21 Oct: "it was not
    clear for me to press something, that's why I pressed something else"."""
    # Not the comments: the note in voice_ask quotes the old wording on purpose.
    lines = [l for l in open(ABS_SH).read().splitlines()
             if "Install uv now?" in l and not l.lstrip().startswith("#")]
    assert len(lines) == 1, lines
    line = lines[0]
    assert "ask_default_yes" in line, "enter must mean yes here: " + line
    assert "[Y/n]" in line, "the prompt must show which way enter goes: " + line
    assert "Enter" in line, "say outright which key accepts it: " + line
