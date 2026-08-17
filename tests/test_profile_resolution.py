"""Which Telegram directory a profile resolves to, and who gets to override it.

`cmd_run` exports `TELEGRAM_STATE_DIR` at launch so the plugin can find the token.
Every `abs` command typed *inside* that session inherits it — and `use_profile`
used to apply it to whatever profile it was resolving, not just the session's own.

The visible symptom: `abs profiles` run from inside a session showed EVERY profile
as "live (pid N)" carrying the running bot's pid, because each one resolved to the
running bot's directory and read its `bot.pid`. The quieter one: `abs --profile
work` inside a `default` session resolved work's token and allowlist to default's
directory.

`ABS_SESSION_PROFILE` names the profile the variable belongs to. Unset means
nobody launched through abs, so it is the user's own export and the documented
pre-profiles two-bot trick keeps working exactly as before.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ABS_SH = os.path.join(REPO, "abs.sh")


@pytest.fixture
def two_profiles(tmp_path):
    """Two paired profiles, each with its own telegram dir. Only `alpha` has a
    live bot.pid — pointing at this test process, which is certainly alive."""
    home = tmp_path / "abshome"
    for name, live in (("alpha", True), ("beta", False)):
        prof = home / "profiles" / name
        prof.mkdir(parents=True)
        tg = home / "tg" / name
        tg.mkdir(parents=True)
        (prof / "rc.json").write_text(
            json.dumps({"bot": f"{name}bot", "chat_id": 42, "tg_dir": str(tg)})
        )
        (tg / ".env").write_text("TELEGRAM_BOT_TOKEN=123:FAKE\n")
        (tg / "access.json").write_text(json.dumps({"dmPolicy": "allowlist", "allowFrom": ["42"]}))
        if live:
            (tg / "bot.pid").write_text(f"{os.getpid()}\n")
    return home


def run(home, *args, env_extra=None, drop=()):
    env = dict(os.environ, ABS_HOME=str(home))
    for k in ("TELEGRAM_STATE_DIR", "ABS_SESSION_PROFILE", *drop):
        env.pop(k, None)
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", ABS_SH, *args], capture_output=True, text=True, env=env
    )


def _alpha_tg(home) -> str:
    return str(home / "tg" / "alpha")


# ---- the bug ----------------------------------------------------------------


def test_inside_a_session_other_profiles_are_not_marked_live(two_profiles):
    """The symptom. Inside an `alpha` session, `beta` must still read as idle —
    it has no bot.pid of its own, and borrowing alpha's is a lie."""
    out = run(
        two_profiles,
        "profiles",
        env_extra={
            "TELEGRAM_STATE_DIR": _alpha_tg(two_profiles),
            "ABS_SESSION_PROFILE": "alpha",
        },
    )
    assert out.returncode == 0, out.stderr
    lines = {
        line.split()[0]: line
        for line in out.stderr.splitlines()
        if line.startswith("  ") and "bot" in line
    }
    assert "live" in lines["alpha"], out.stderr
    assert "idle" in lines["beta"], out.stderr


def test_the_sessions_own_profile_still_uses_the_exported_dir(two_profiles):
    """The variable must keep working for the profile it belongs to — that is what
    makes the plugin find the right token."""
    out = run(
        two_profiles,
        "profiles",
        env_extra={
            "TELEGRAM_STATE_DIR": _alpha_tg(two_profiles),
            "ABS_SESSION_PROFILE": "alpha",
        },
    )
    assert "live" in [l for l in out.stderr.splitlines() if "alpha" in l][0]


def test_without_the_variable_liveness_is_per_profile(two_profiles):
    """Baseline: from a plain terminal, each profile reads its own bot.pid."""
    out = run(two_profiles, "profiles")
    assert out.returncode == 0, out.stderr
    alpha = [l for l in out.stderr.splitlines() if "alpha" in l][0]
    beta = [l for l in out.stderr.splitlines() if "beta" in l][0]
    assert "live" in alpha
    assert "idle" in beta


# ---- the legacy escape hatch is untouched ------------------------------------


def test_a_user_set_variable_still_overrides_every_profile(two_profiles):
    """The documented pre-profiles trick: export TELEGRAM_STATE_DIR yourself and it
    wins. With no ABS_SESSION_PROFILE beside it, nobody launched through abs, so
    it is the user's own and must keep behaving exactly as it always did."""
    out = run(
        two_profiles,
        "profiles",
        env_extra={"TELEGRAM_STATE_DIR": _alpha_tg(two_profiles)},
    )
    assert out.returncode == 0, out.stderr
    # BOTH read alpha's bot.pid — the old behaviour, deliberately preserved.
    assert "live" in [l for l in out.stderr.splitlines() if "beta" in l][0]


def test_a_stale_session_profile_for_a_gone_profile_does_not_break_resolution(two_profiles):
    """ABS_SESSION_PROFILE naming a profile that no longer exists must simply mean
    "not mine" for everyone, not an error."""
    out = run(
        two_profiles,
        "profiles",
        env_extra={
            "TELEGRAM_STATE_DIR": _alpha_tg(two_profiles),
            "ABS_SESSION_PROFILE": "deleted",
        },
    )
    assert out.returncode == 0, out.stderr
    assert "idle" in [l for l in out.stderr.splitlines() if "beta" in l][0]


def _resolved_tg_dir(home, profile, env_extra):
    """TG_DIR as `use_profile` resolves it — the value that decides which token and
    which allowlist get used."""
    body = "".join(l for l in open(ABS_SH) if l.strip() != 'main "$@"')
    script = home.parent / "resolve.sh"
    script.write_text(f'{body}\nuse_profile {profile}\nprintf "%s" "$TG_DIR"\n')
    env = dict(os.environ, ABS_HOME=str(home))
    for k in ("TELEGRAM_STATE_DIR", "ABS_SESSION_PROFILE"):
        env.pop(k, None)
    env.update(env_extra)
    return subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, env=env
    ).stdout.strip()


def test_a_named_profile_resolves_its_own_token_dir_inside_a_session(two_profiles):
    """The quieter half of the bug, and the one that actually matters: TG_DIR is
    where the token and the allowlist are read from. Inside an `alpha` session,
    `abs --profile beta` used to point at ALPHA's directory — i.e. drive the wrong
    bot with the wrong allowlist."""
    got = _resolved_tg_dir(
        two_profiles,
        "beta",
        {"TELEGRAM_STATE_DIR": _alpha_tg(two_profiles), "ABS_SESSION_PROFILE": "alpha"},
    )
    assert got == str(two_profiles / "tg" / "beta"), got


def test_the_sessions_own_profile_keeps_the_exported_dir(two_profiles):
    """And the export must still win for the profile it belongs to."""
    got = _resolved_tg_dir(
        two_profiles,
        "alpha",
        {"TELEGRAM_STATE_DIR": _alpha_tg(two_profiles), "ABS_SESSION_PROFILE": "alpha"},
    )
    assert got == _alpha_tg(two_profiles)
