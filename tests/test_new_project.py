"""Creating a project from the terminal start menu.

"Another project…" used to be a dead end. With nothing registered it printed a
warning and launched you in the folder you were already standing in — which is
exactly the moment a new user needs a way forward and has none.

Two things are being pinned here.

**The name is a name, not a path.** It goes straight to `mkdir`, so it is
*refused* rather than sanitised. Every other cleaner in this script strips and
carries on, because a label that loses a character is still a label. Not this
one: someone who typed a slash meant a path, and quietly turning `a/b` into `ab`
would create a folder they never asked for and then start a session inside it.

**Nothing is ever overwritten.** An existing folder is offered as-is. There is no
branch anywhere in this flow that removes a path a person just typed.

This is terminal-only by design — the registry has said so since 5.3/D6, because
a compromised phone must never be able to name a path on the machine, let alone
create one. Nothing here is reachable from Telegram.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ABS_SH = REPO / "abs.sh"


def call(snippet: str, tmp_path: Path, **env_extra) -> subprocess.CompletedProcess:
    """Run a function from abs.sh with `main` never running."""
    body = "".join(l for l in open(ABS_SH) if l.strip() != 'main "$@"')
    script = tmp_path / "call.sh"
    script.write_text(f"{body}\n{snippet}\n")
    env = dict(os.environ, ABS_HOME=str(tmp_path / "abshome"))
    for k in ("TELEGRAM_STATE_DIR", "ABS_SESSION_PROFILE"):
        env.pop(k, None)
    env.update(env_extra)
    return subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)


# ---- the name is refused, not cleaned ----------------------------------------


@pytest.mark.parametrize("name,because", [
    ("", "empty"),
    (".", "a directory reference"),
    ("..", "the parent directory"),
    ("a/b", "a path, not a name"),
    ("/etc", "absolute"),
    ("../escape", "traversal"),
    ("-rf", "looks like a flag"),
    ("x" * 65, "too long"),
])
def test_an_unusable_name_is_refused_with_a_reason(name, because, tmp_path):
    out = call(f'_project_name_problem {name!r}', tmp_path)
    assert out.stdout.strip(), f"{because}: accepted silently, which means mkdir would run"


@pytest.mark.parametrize("name", [
    "my-new-thing", "api_v2", "Project 2026", "abs.web", "x",
])
def test_an_ordinary_name_is_accepted(name, tmp_path):
    out = call(f'_project_name_problem {name!r}', tmp_path)
    assert out.stdout.strip() == "", f"{name!r} was rejected: {out.stdout!r}"


def test_a_slash_says_what_to_do_instead(tmp_path):
    """A refusal that does not say where the path part belongs just gets retyped
    the same way."""
    out = call('_project_name_problem "src/thing"', tmp_path)
    assert "location" in out.stdout, out.stdout


def test_the_reason_never_contains_the_name_unescaped(tmp_path):
    """The message is printed into a terminal. A name carrying an escape sequence
    must not be able to paint with it."""
    out = call('_project_name_problem "$(printf \'a\\033[31mb\')"', tmp_path)
    assert "\x1b[31m" not in out.stdout, repr(out.stdout)


# ---- the prompt reads the human, not the pipe --------------------------------


def test_the_text_prompt_reads_the_terminal_not_stdin(tmp_path):
    """/dev/tty, for the same reason voice_ask uses it: stdin may be a pipe — the
    installer hands off to abs — while the person is still at the keyboard.

    With no tty available it must fail rather than silently read the pipe and
    treat a stray line as a folder name.
    """
    out = call('menu_ask_text "Project name:" && echo GOT || echo NOTTY', tmp_path)
    assert "NOTTY" in out.stdout, out.stdout


# ---- nothing is destroyed ----------------------------------------------------


def test_the_flow_contains_no_removal_of_a_typed_path(tmp_path):
    """A structural check, deliberately. This function takes a path from a person
    at a prompt and creates it; if a future edit ever adds an rm to that path, the
    blast radius is whatever they typed. There is no good reason for one to
    appear, so its absence is worth asserting rather than trusting.
    """
    src = ABS_SH.read_text()
    start = src.index("_start_menu_new_project() {")
    end = src.index("\n}\n", start)
    body = src[start:end]
    assert "rm " not in body and "rm -" not in body, "a removal appeared in the create-project flow"
    assert "mkdir -p" in body, "it should create the folder it promised"
