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


# ---- 4. the launch has to get PAST the bypass disclaimer ---------------------
#
# Claude Code will not enter bypassPermissions on trust alone. In a terminal it
# shows a modal "1. No, exit / 2. Yes, I accept" and waits; non-interactively it
# downgrades the mode to the default and says so. Either way an Away session that
# does nothing about it is broken — the first is a session hung before it starts
# (which is how Pranjal found it, with the phone reporting "waiting for input"),
# the second is the silent loss of Away that the acceptEdits version already had.
#
# `skipDangerousModePermissionPrompt` is the documented way past it, and Claude
# Code reads it from the --settings file among other scopes, so it belongs in the
# per-session settings abs.sh already writes — NOT in the operator's global config,
# where it would quietly apply to every future `claude` they ever run.

_STUB_CLAUDE_LAUNCH = """#!/usr/bin/env bash
case "${1:-}" in plugin) echo "telegram@claude-plugins-official"; exit 0 ;; esac
exit 0
"""
_STUB_NOOP_LAUNCH = "#!/usr/bin/env bash\nexit 0\n"


def _launch_settings(tmp_path, *extra_args):
    """Run the real launch far enough to write its settings file, and read it back."""
    from tests.conftest import write_profile

    launch_home = tmp_path / "lhome"
    launch_home.mkdir(exist_ok=True)
    abs_home = tmp_path / "labs"
    write_profile(abs_home, "default", allow_ids=[42])

    bind = tmp_path / "lbin"
    bind.mkdir(exist_ok=True)
    for name, body in (
        ("claude", _STUB_CLAUDE_LAUNCH),
        ("curl", _STUB_NOOP_LAUNCH),
        ("bun", _STUB_NOOP_LAUNCH),
    ):
        p = bind / name
        p.write_text(body)
        p.chmod(0o755)

    env = dict(os.environ)
    for key in list(env):
        if key.startswith("ABS_") or key.startswith("TELEGRAM_") or key == "CLAUDERC_HOME":
            env.pop(key, None)
    env.update(
        HOME=str(launch_home),
        ABS_HOME=str(abs_home),
        PATH=f"{bind}:{env.get('PATH', '')}",
        ABS_REPO="http://127.0.0.1:1/never",
    )
    proc = subprocess.run(
        ["bash", ABS_SH, "--profile", "default", "--daemon-start", *extra_args],
        capture_output=True, text=True, env=env, timeout=60, cwd=str(tmp_path),
    )
    hooks = abs_home / "profiles" / "default" / "hooks.json"
    assert hooks.exists(), proc.stdout + proc.stderr
    return json.loads(hooks.read_text()), proc.stdout + proc.stderr


def test_an_away_launch_clears_the_bypass_disclaimer(tmp_path):
    settings, _ = _launch_settings(tmp_path, "--away")
    assert settings.get("skipDangerousModePermissionPrompt") is True


def test_a_normal_launch_leaves_the_disclaimer_alone(tmp_path):
    """A normal session never asks for bypass, so it must not pre-accept anything —
    the flag is scoped to the one launch that genuinely needs it."""
    settings, _ = _launch_settings(tmp_path)
    assert "skipDangerousModePermissionPrompt" not in settings


def test_the_away_warning_says_the_disclaimer_was_accepted(tmp_path):
    """Accepting a disclaimer on someone's behalf is not something to do quietly."""
    _, output = _launch_settings(tmp_path, "--away")
    assert "disclaimer" in output
    assert "THIS session only" in output


def test_the_away_settings_still_carry_the_guard(tmp_path):
    """The disclaimer and the guard travel in the same file, so a mistake in the
    merge could drop the hooks and leave an auto-approving session with nothing in
    front of it."""
    settings, _ = _launch_settings(tmp_path, "--away")
    matchers = [e.get("matcher") for e in settings["hooks"]["PreToolUse"]]
    assert "Bash" in matchers


# ---- 5. the guard cannot be edited out from inside the session it guards ------
#
# A security pass on this branch found the guarantee two sections up to be false.
# The guard's whole trust state lived in files the guarded session can write, and
# under bypassPermissions it can write them without being asked:
#
#   * `cmd_guard_hook` began with `[ -f "$ABS_STATE" ] || return 0`, so deleting
#     rc.json failed the guard OPEN for the rest of the session;
#   * `.session_away` and `.no_guard` were read live from that same file, so
#     `jq 'del(.session_away)'` followed by `abs config guard off` restored the old
#     origin-gated behaviour and then switched the guard off entirely;
#   * only `Bash` and the reply tool were matched, so a single `Edit` to abs.sh —
#     the script the hook re-invokes on every call — needed no Bash at all.
#
# None of it was in the blocklist, and none of it should have to be: a control that
# the thing it controls can edit is not a control. The fix moves the fact of being
# unattended into the hook's ARGV, where the session cannot reach it.


def _guard_away_argv(home, command, tool="Bash", file_path=None):
    """Run the hook the way an AWAY launch wires it: with --session-away in argv."""
    tool_input = {"command": command} if file_path is None else {"file_path": file_path}
    payload = json.dumps({
        "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
    })
    env = dict(os.environ, ABS_HOME=str(home))
    for k in ("TELEGRAM_STATE_DIR", "ABS_SESSION_PROFILE"):
        env.pop(k, None)
    out = subprocess.run(
        ["bash", ABS_SH, "--profile", PROFILE, "--session-away", "__guard-hook"],
        input=payload, capture_output=True, text=True, env=env,
    )
    return out.returncode == 2, out.stderr


def test_argv_says_away_even_when_the_state_file_says_otherwise(home):
    """The disarm one-liner was `jq 'del(.session_away)' rc.json`. Now irrelevant."""
    _rc(home, session_away=None, last_origin="terminal")
    blocked, err = _guard_away_argv(home, DESTRUCTIVE)
    assert blocked, err


def test_argv_says_away_so_guard_off_is_ignored(home):
    _rc(home, session_away=None, no_guard=True, last_origin="terminal")
    blocked, err = _guard_away_argv(home, DESTRUCTIVE)
    assert blocked, err


def test_an_away_session_with_no_state_file_fails_closed(home):
    """Deleting rc.json used to disarm the guard for the whole session. An Away
    session with nothing to check refuses instead — the operator loses a session,
    not the machine. A NORMAL session still fails open: a hook that errors on every
    Bash call would be worse than the gap."""
    (home / "profiles" / PROFILE / "rc.json").unlink()
    blocked, err = _guard_away_argv(home, "ls -la")
    assert blocked, err
    assert "state file is missing" in err


def test_a_normal_session_with_no_state_file_still_fails_open(home):
    (home / "profiles" / PROFILE / "rc.json").unlink()
    blocked, _ = _guard(home, DESTRUCTIVE)
    assert not blocked


def test_writing_to_the_guards_own_state_is_refused(home):
    """Neither `rm rc.json` nor `mv` over it is in the blocklist — a bare rm of one
    file is deliberately allowed — yet either ends the guard for the session."""
    rc = home / "profiles" / PROFILE / "rc.json"
    for command in (
        f"rm {rc}",
        f"mv /tmp/x {rc}",
        f"jq 'del(.session_away)' {rc} > /tmp/r",
        f": > {rc}",
        f"truncate -s 0 {rc}",
        f"sed -i 's/true/false/' {rc}",
    ):
        blocked, err = _guard_away_argv(home, command)
        assert blocked, f"{command!r} was allowed: {err}"


def test_editing_abs_itself_is_refused_in_an_away_session(home):
    """No Bash needed for this one: `Edit` was matched by no hook at all, and under
    bypassPermissions it does not prompt."""
    blocked, err = _guard_away_argv(home, "", tool="Edit", file_path=ABS_SH)
    assert blocked, err
    assert "enforces the guard" in err


def test_editing_the_profile_directory_is_refused_in_an_away_session(home):
    target = str(home / "profiles" / PROFILE / "hooks.json")
    blocked, err = _guard_away_argv(home, "", tool="Write", file_path=target)
    assert blocked, err


def test_editing_ordinary_project_files_is_untouched(home):
    """The point is the guard's own files, not the operator's work. An Away session
    that could not edit code would be useless."""
    for tool in ("Write", "Edit", "MultiEdit"):
        blocked, err = _guard_away_argv(
            home, "", tool=tool, file_path="/home/pranjal/Projects/thing/src/main.py")
        assert not blocked, f"{tool} on a project file was blocked: {err}"


def test_a_normal_session_never_inspects_file_writes(home):
    """Only Away pays for this check; a normal session has Claude's own prompts."""
    _rc(home, session_away=None, last_origin="telegram")
    payload = json.dumps({
        "hook_event_name": "PreToolUse", "tool_name": "Edit",
        "tool_input": {"file_path": ABS_SH},
    })
    env = dict(os.environ, ABS_HOME=str(home))
    for k in ("TELEGRAM_STATE_DIR", "ABS_SESSION_PROFILE"):
        env.pop(k, None)
    out = subprocess.run(
        ["bash", ABS_SH, "--profile", PROFILE, "__guard-hook"],
        input=payload, capture_output=True, text=True, env=env,
    )
    assert out.returncode == 0, out.stderr


def test_an_away_launch_puts_the_flag_in_the_hook_command(tmp_path):
    """The wiring: if the flag is not in argv, everything above is theatre."""
    settings, _ = _launch_settings(tmp_path, "--away")
    bash_hooks = [
        h["command"]
        for e in settings["hooks"]["PreToolUse"] if e.get("matcher") == "Bash"
        for h in e["hooks"]
    ]
    assert bash_hooks and all("--session-away" in c for c in bash_hooks), bash_hooks


def test_a_normal_launch_does_not(tmp_path):
    settings, _ = _launch_settings(tmp_path)
    bash_hooks = [
        h["command"]
        for e in settings["hooks"].get("PreToolUse", []) if e.get("matcher") == "Bash"
        for h in e["hooks"]
    ]
    assert bash_hooks and not any("--session-away" in c for c in bash_hooks), bash_hooks
