"""Away mode: nothing prompts, so the guard is not optional.

Away used to launch with ``--permission-mode acceptEdits``, which auto-approves
file edits and nothing else. The thing that actually halts a session is a Bash
approval, so Away didn't deliver what its name promises — you'd come back after
an hour to a session that had been waiting on a prompt the whole time.

It now launches with ``bypassPermissions``. That removes Claude's own safety net
entirely, so three things have to hold, and each is a test below.

1. **The guard is forced on**, whatever ``abs config guard off`` says. Otherwise
   an Away launch puts nothing at all between a Telegram message and the machine.
2. **The guard bites on every turn**, not only Telegram-driven ones. Unattended
   is a property of the SESSION. Attaching at the desk to type one command must
   not disarm the remaining hours — the old origin check would have done exactly
   that.
3. **A normal session is untouched.** The blast radius stops at Away: the guard
   stays optional and origin-gated, because Claude is still asking.

The premise underneath all of it — that PreToolUse hooks still fire under
``bypassPermissions`` — was measured against a real ``claude -p`` run before any
of this was written, not assumed.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ABS_SH = os.path.join(REPO, "abs.sh")
PROFILE = "awaytest"

DESTRUCTIVE = "rm -rf /home/pranjal/Projects"
HARMLESS = "ls -la"


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "abshome"
    (h / "profiles" / PROFILE).mkdir(parents=True)
    (h / "profiles" / PROFILE / "rc.json").write_text(
        json.dumps({"bot": "b", "chat_id": 42})
    )
    return h


def _rc(home, **fields):
    p = home / "profiles" / PROFILE / "rc.json"
    state = json.loads(p.read_text())
    for k, v in fields.items():
        if v is None:
            state.pop(k, None)
        else:
            state[k] = v
    p.write_text(json.dumps(state))


def _guard(home, command):
    """Run the real PreToolUse guard hook. Returns True if it BLOCKED (exit 2)."""
    payload = json.dumps({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    })
    env = dict(os.environ, ABS_HOME=str(home))
    for k in ("TELEGRAM_STATE_DIR", "ABS_SESSION_PROFILE"):
        env.pop(k, None)
    out = subprocess.run(
        ["bash", ABS_SH, "--profile", PROFILE, "__guard-hook"],
        input=payload, capture_output=True, text=True, env=env,
    )
    return out.returncode == 2, out.stderr


# ---- 1. the guard cannot be switched off in an Away session ------------------


def test_guard_off_does_not_disarm_an_away_session(home):
    _rc(home, no_guard=True, session_away=True, last_origin="telegram")
    blocked, err = _guard(home, DESTRUCTIVE)
    assert blocked, err
    assert "Away session" in err


def test_guard_off_still_works_for_a_normal_session(home):
    """The setting is honoured where Claude is still asking — turning it off is a
    legitimate choice there, and this change must not quietly revoke it."""
    _rc(home, no_guard=True, session_away=None, last_origin="telegram")
    blocked, _ = _guard(home, DESTRUCTIVE)
    assert not blocked


# ---- 2. unattended is a property of the session, not of who spoke last -------


def test_an_away_session_guards_a_terminal_turn_too(home):
    """The hole this closes: launch Away, attach at the desk, type one command —
    origin flips to `terminal` and the old guard went quiet for the rest of a
    session that is still auto-approving with nobody watching."""
    _rc(home, session_away=True, last_origin="terminal")
    blocked, err = _guard(home, DESTRUCTIVE)
    assert blocked, err


def test_an_away_session_guards_when_no_origin_was_ever_recorded(home):
    """First turn of a daemon-launched session: `.last_origin` is cleared at
    launch, so there is no origin at all. Away must still guard."""
    _rc(home, session_away=True, last_origin=None)
    blocked, _ = _guard(home, DESTRUCTIVE)
    assert blocked


def test_a_normal_session_still_ignores_a_terminal_turn(home):
    """Unchanged, and deliberately so: at the desk you are trusted, and Claude
    prompts anyway."""
    _rc(home, session_away=None, last_origin="terminal")
    blocked, _ = _guard(home, DESTRUCTIVE)
    assert not blocked


# ---- 3. Away does not mean "block everything" --------------------------------


def test_ordinary_work_runs_unattended(home):
    """The whole point of Away. If routine commands were blocked, an unattended
    session would stall on the guard instead of on a prompt — the same failure
    with a different name."""
    _rc(home, session_away=True, last_origin="terminal")
    blocked, _ = _guard(home, HARMLESS)
    assert not blocked


def test_the_away_refusal_explains_why_it_differs(home):
    """A blocked command in an Away session must not tell the operator to 'run it
    at the terminal' as though origin were the problem — they may be at the
    terminal already."""
    _rc(home, session_away=True, last_origin="terminal")
    _, err = _guard(home, DESTRUCTIVE)
    assert "nothing prompts" in err
    assert "the turn came from Telegram" not in err


# ---- the flag's own lifecycle ------------------------------------------------


def test_a_stale_away_flag_only_ever_makes_the_guard_stricter(home):
    """`.session_away` is written per launch and cleared on exit. If a crash
    leaves it set, the consequence is a guard that bites at the desk — annoying,
    never dangerous. Pinning the direction of the failure."""
    _rc(home, session_away=True, last_origin="terminal")
    blocked, _ = _guard(home, DESTRUCTIVE)
    assert blocked
    _rc(home, session_away=None)
    blocked, _ = _guard(home, DESTRUCTIVE)
    assert not blocked


def test_abs_exit_clears_the_away_flag(home):
    """Otherwise the next normal session inherits Away's strictness."""
    (home / "profiles" / PROFILE / "session.pid").write_text("999999999\n")
    _rc(home, session_away=True)
    env = dict(os.environ, ABS_HOME=str(home))
    for k in ("TELEGRAM_STATE_DIR", "ABS_SESSION_PROFILE"):
        env.pop(k, None)
    subprocess.run(
        ["bash", ABS_SH, "--profile", PROFILE, "exit"],
        capture_output=True, text=True, env=env,
    )
    rc = json.loads((home / "profiles" / PROFILE / "rc.json").read_text())
    assert "session_away" not in rc
