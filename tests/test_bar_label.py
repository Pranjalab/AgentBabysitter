"""`abs config label` — the name before the colon in the status bar.

`abs:@Claudepranbot` becomes `Pran:@Claudepranbot`. Cosmetic, with one sharp
edge: the value is printed into a terminal status bar surrounded by real ESC
bytes, on every Claude Code render. So the command **sanitises** rather than
validates, and refuses input that sanitises to nothing instead of silently
storing something the bar will ignore.

The label is **generated, not typed**: it names the Claude account this session is
spending, so it follows that account and re-checks as the session runs. It reads
`.oauthAccount.displayName` from `~/.claude.json` — exactly that one field, never
the file, which also holds account tokens — gated on mtime and throttled to once a
minute, because that file is large and rewritten constantly.

Two operator choices outrank it, and only two: `label <name>` pins one, `--clear`
returns the plain default. Anything else, including a label written by a version
that recorded nothing about its origin, gets corrected.

Rendering is covered in `test_statusline_dots.py`; this is the setting.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ABS_SH = os.path.join(REPO, "abs.sh")

PROFILE = "labeltest"


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "abshome"
    (h / "profiles" / PROFILE).mkdir(parents=True)
    (h / "profiles" / PROFILE / "rc.json").write_text(
        json.dumps({"bot": "testbot", "chat_id": 42})
    )
    return h


def run(home, *args, fake_home=None):
    env = dict(os.environ, ABS_HOME=str(home))
    for k in ("TELEGRAM_STATE_DIR", "ABS_SESSION_PROFILE"):
        env.pop(k, None)
    if fake_home is not None:
        env["HOME"] = str(fake_home)
    return subprocess.run(
        ["bash", ABS_SH, "--profile", PROFILE, *args],
        capture_output=True, text=True, env=env,
    )


def stored(home):
    return json.loads(
        (home / "profiles" / PROFILE / "rc.json").read_text()
    ).get("bar_label")


# ---- setting it --------------------------------------------------------------


def test_setting_a_plain_name(home):
    out = run(home, "config", "label", "Pran")
    assert out.returncode == 0, out.stderr
    assert stored(home) == "Pran"
    assert "Pran:@testbot" in out.stderr


def test_showing_it_when_unset(home):
    out = run(home, "config", "label")
    assert out.returncode == 0
    assert "abs" in out.stderr and "default" in out.stderr


def test_clearing_it_goes_back_to_abs(home):
    run(home, "config", "label", "Pran")
    out = run(home, "config", "label", "--clear")
    assert out.returncode == 0, out.stderr
    assert stored(home) is None
    assert "abs" in out.stderr


def test_it_appears_in_the_config_listing(home):
    run(home, "config", "label", "Pran")
    out = run(home, "config")
    assert "bar label" in out.stderr
    assert "Pran" in out.stderr


# ---- sanitising --------------------------------------------------------------


def test_an_escape_sequence_is_stripped_to_its_printable_residue(home):
    """The ESC and the `[` go; the digits and letters of the sequence don't. The
    result is nonsense, but visible nonsense the operator is told about — and it
    cannot move the cursor, which is the only thing that actually matters for a
    string reprinted on every render."""
    out = run(home, "config", "label", "\x1b[31mred")
    assert out.returncode == 0, out.stderr
    assert stored(home) == "31mred"
    assert "trimmed" in out.stderr


def test_a_too_long_label_is_truncated_and_says_so(home):
    out = run(home, "config", "label", "A" * 50)
    assert out.returncode == 0, out.stderr
    assert stored(home) == "A" * 12
    assert "trimmed" in out.stderr


def test_an_unusable_label_is_refused_not_silently_dropped(home):
    """An emoji label would store fine and then never render. Say so, rather than
    leaving the operator wondering why the bar still says abs."""
    out = run(home, "config", "label", "🙂🙂")
    assert out.returncode != 0
    assert stored(home) is None
    assert "Nothing usable" in out.stderr


def test_a_newline_cannot_be_stored(home):
    """Claude Code renders the bar as one line; a stored newline would split it."""
    out = run(home, "config", "label", "Pran\nEVIL")
    assert out.returncode == 0, out.stderr
    assert stored(home) == "PranEVIL"


def test_spaces_inside_a_name_are_kept_but_trimmed_at_the_edges(home):
    out = run(home, "config", "label", "  Pran K  ")
    assert out.returncode == 0, out.stderr
    assert stored(home) == "Pran K"


# ---- auto --------------------------------------------------------------------


def _claude_json(fake_home, **oauth):
    fake_home.mkdir(parents=True, exist_ok=True)
    (fake_home / ".claude.json").write_text(json.dumps({"oauthAccount": oauth}))


def test_auto_takes_the_claude_display_name(home, tmp_path):
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="Pran", emailAddress="x@y.z")
    out = run(home, "config", "label", "auto", fake_home=fake)
    assert out.returncode == 0, out.stderr
    assert stored(home) == "Pran"
    assert "Claude account" in out.stderr


def test_auto_stores_the_resolved_value_not_a_marker(home, tmp_path):
    """It must resolve once and freeze. Storing "auto" would mean every render
    reads ~/.claude.json, and the label would change silently if the account
    display name ever did."""
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="Pran")
    run(home, "config", "label", "auto", fake_home=fake)
    assert stored(home) == "Pran"
    (fake / ".claude.json").write_text(json.dumps({"oauthAccount": {"displayName": "Someone Else"}}))
    assert stored(home) == "Pran"


def test_auto_sanitises_the_account_name_too(home, tmp_path):
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="A Very Long Display Name Indeed")
    out = run(home, "config", "label", "auto", fake_home=fake)
    assert out.returncode == 0, out.stderr
    # 12 chars would end on a space; the edge trim takes it off.
    assert stored(home) == "A Very Long"


def test_auto_fails_clearly_with_no_claude_json(home, tmp_path):
    fake = tmp_path / "emptyhome"
    fake.mkdir()
    out = run(home, "config", "label", "auto", fake_home=fake)
    assert out.returncode != 0
    assert "No display name" in out.stderr
    assert "abs config label <name>" in out.stderr
    assert stored(home) is None


def test_auto_fails_clearly_when_the_account_has_no_display_name(home, tmp_path):
    fake = tmp_path / "fakehome"
    _claude_json(fake, emailAddress="x@y.z")
    out = run(home, "config", "label", "auto", fake_home=fake)
    assert out.returncode != 0
    assert "No display name" in out.stderr


def test_auto_does_not_read_the_rest_of_claude_json(home, tmp_path):
    """~/.claude.json holds account tokens. Only the display name may be touched,
    and nothing from that file may reach the terminal beyond it."""
    fake = tmp_path / "fakehome"
    fake.mkdir(parents=True, exist_ok=True)
    (fake / ".claude.json").write_text(json.dumps({
        "oauthAccount": {"displayName": "Pran", "accessToken": "sk-ant-SECRETVALUE"},
        "primaryApiKey": "sk-ant-ANOTHERSECRET",
    }))
    out = run(home, "config", "label", "auto", fake_home=fake)
    assert out.returncode == 0, out.stderr
    assert "SECRET" not in out.stdout + out.stderr
    assert "SECRET" not in json.dumps(
        json.loads((home / "profiles" / PROFILE / "rc.json").read_text())
    )


# ---- the default, since 3.0.3 ------------------------------------------------
#
# "abs" told you nothing except that abs was running. The bar now says who you
# are, taken from the Claude account — but resolved ONCE at session launch and
# stored, never at render time. ~/.claude.json is a large file that also holds
# account tokens, and bar_label runs on every frame Claude Code draws.


def seed(home, fake_home=None):
    """Run just the seeding step, the way cmd_run does at launch."""
    body = "".join(l for l in open(ABS_SH) if l.strip() != 'main "$@"')
    script = home.parent / "seed.sh"
    script.write_text(f"{body}\nuse_profile {PROFILE}\nbar_label_seed\nbar_label\n")
    env = dict(os.environ, ABS_HOME=str(home))
    for k in ("TELEGRAM_STATE_DIR", "ABS_SESSION_PROFILE"):
        env.pop(k, None)
    if fake_home is not None:
        env["HOME"] = str(fake_home)
    return subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)


def test_launching_seeds_the_label_from_the_claude_account(home, tmp_path):
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="Pran")
    out = seed(home, fake_home=fake)
    assert out.returncode == 0, out.stderr
    assert stored(home) == "Pran"
    assert out.stdout == "Pran"


def test_seeding_never_overwrites_a_label_you_chose(home, tmp_path):
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="Pran")
    run(home, "config", "label", "boxA")
    seed(home, fake_home=fake)
    assert stored(home) == "boxA"


def test_seeding_happens_once_so_clearing_stays_cleared(home, tmp_path):
    """The trap the reply-mode default fell into as well: if `--clear` only
    removed the value, the next launch would put the account name straight back
    and clearing would look broken."""
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="Pran")
    seed(home, fake_home=fake)
    assert stored(home) == "Pran"
    out = run(home, "config", "label", "--clear")
    assert out.returncode == 0, out.stderr
    assert stored(home) is None
    seed(home, fake_home=fake)
    assert stored(home) is None, "cleared means cleared"
    assert seed(home, fake_home=fake).stdout == "abs"


def test_an_account_with_no_display_name_falls_back_to_abs(home, tmp_path):
    fake = tmp_path / "emptyhome"
    fake.mkdir()
    out = seed(home, fake_home=fake)
    assert out.returncode == 0, out.stderr
    assert stored(home) is None
    assert out.stdout == "abs"


def test_an_unusable_display_name_falls_back_rather_than_storing_nothing(home, tmp_path):
    """A name that sanitises to nothing — an emoji handle, say — must not be
    stored as an empty label, which would render as a bare colon."""
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="🎧🎧🎧")
    out = seed(home, fake_home=fake)
    assert out.returncode == 0, out.stderr
    assert stored(home) is None
    assert out.stdout == "abs"


def test_seeding_does_not_leak_the_rest_of_claude_json(home, tmp_path):
    fake = tmp_path / "fakehome"
    fake.mkdir(parents=True, exist_ok=True)
    (fake / ".claude.json").write_text(json.dumps({
        "oauthAccount": {"displayName": "Pran", "accessToken": "sk-ant-SECRETVALUE"},
        "primaryApiKey": "sk-ant-ANOTHERSECRET",
    }))
    out = seed(home, fake_home=fake)
    assert "SECRET" not in out.stdout + out.stderr
    assert json.dumps(json.loads((home / "profiles" / PROFILE / "rc.json").read_text())).count("SECRET") == 0


# ---- following a change of Claude account ------------------------------------
#
# Reported after logging into a second account: the bar kept the old name
# forever. The seed was guarded by a boolean — "have we done this?" — and a
# boolean cannot answer the question that matters, which is "is this label still
# yours?". The account it came FROM is stored now, so a mismatch is visible.


def test_the_label_follows_a_change_of_account(home, tmp_path):
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="Pran")
    seed(home, fake_home=fake)
    assert stored(home) == "Pran"

    _claude_json(fake, displayName="Pranfold")          # logged into another account
    seed(home, fake_home=fake)
    assert stored(home) == "Pranfold", "the bar kept the old account's name"


def test_a_label_you_chose_is_never_overwritten_by_an_account_switch(home, tmp_path):
    """The other half, and the one that would be worse to get wrong: replacing a
    deliberate choice with a name read out of a file."""
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="Pran")
    run(home, "config", "label", "Work")
    _claude_json(fake, displayName="SomeoneElse")
    seed(home, fake_home=fake)
    assert stored(home) == "Work"


def test_a_label_from_an_older_abs_is_corrected(home, tmp_path):
    """The reversal, decided 19 Aug. The previous rule left an unattributed label
    alone in case it had been typed — and so never fixed the one case it was
    written for, because every label written before the source was recorded is
    unattributed. The bar is system-generated: an unattributed name is stale, not
    sacred. Only an explicit `label <name>` is protected now.
    """
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="Pranfold")
    import json as _json
    rc = home / "profiles" / PROFILE / "rc.json"
    d = _json.loads(rc.read_text()); d["bar_label"] = "Legacy"; rc.write_text(_json.dumps(d))
    seed(home, fake_home=fake)
    assert stored(home) == "Pranfold"


def test_clearing_survives_an_account_switch(home, tmp_path):
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="Pran")
    seed(home, fake_home=fake)
    run(home, "config", "label", "--clear")
    _claude_json(fake, displayName="Pranfold")
    seed(home, fake_home=fake)
    assert stored(home) is None
    assert seed(home, fake_home=fake).stdout == "abs"


def test_label_auto_re_arms_following_the_account(home, tmp_path):
    """After setting one by hand, `label auto` has to put you back on the
    follow-the-account path — otherwise it is a one-shot with no way back."""
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="Pran")
    run(home, "config", "label", "Work")
    run(home, "config", "label", "auto", fake_home=fake)
    assert stored(home) == "Pran"
    _claude_json(fake, displayName="Pranfold")
    seed(home, fake_home=fake)
    assert stored(home) == "Pranfold"


def test_an_unreadable_account_file_does_not_reset_the_label(home, tmp_path):
    """Mid-login ~/.claude.json can be briefly absent. Reverting to "abs" because
    of a transient read failure would look like the tool forgetting who you are."""
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="Pran")
    seed(home, fake_home=fake)
    (fake / ".claude.json").unlink()
    seed(home, fake_home=fake)
    assert stored(home) == "Pran"


def test_the_exact_state_that_was_reported_broken(home, tmp_path):
    """The operator's own profile, byte for byte: a label seeded by 3.1.0 with no
    recorded source, against an account he has since changed. It survived two
    attempted fixes — the first could not see it, the second saw it and chose to
    leave it — and this is the assertion that would have caught both.
    """
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="Pranfold")
    import json as _json
    rc = home / "profiles" / PROFILE / "rc.json"
    d = _json.loads(rc.read_text())
    d["bar_label"] = "Pran"; d["bar_label_seeded"] = True      # exactly the 3.1.0 shape
    rc.write_text(_json.dumps(d))

    out = seed(home, fake_home=fake)
    assert out.returncode == 0, out.stderr
    assert stored(home) == "Pranfold"
    assert out.stdout == "Pranfold"


def test_the_3_5_3_shape_also_follows(home, tmp_path):
    """A label carrying the old `bar_label_auto` marker: still not manual, so it
    follows, and the dead key is cleaned up on the way past."""
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="Pranfold")
    import json as _json
    rc = home / "profiles" / PROFILE / "rc.json"
    d = _json.loads(rc.read_text())
    d["bar_label"] = "Pran"; d["bar_label_auto"] = "Pran"; d["bar_label_seeded"] = True
    rc.write_text(_json.dumps(d))
    seed(home, fake_home=fake)
    assert stored(home) == "Pranfold"
    assert "bar_label_auto" not in _json.loads(rc.read_text())


def test_only_an_explicit_label_is_protected(home, tmp_path):
    """`config label <name>` records that a human chose it. Nothing else does."""
    import json as _json
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="Pran")
    run(home, "config", "label", "Work")
    rc = home / "profiles" / PROFILE / "rc.json"
    assert _json.loads(rc.read_text()).get("bar_label_manual") is True
    _claude_json(fake, displayName="SomeoneElse")
    seed(home, fake_home=fake)
    assert stored(home) == "Work"


def test_label_auto_hands_the_name_back_to_the_account(home, tmp_path):
    import json as _json
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="Pranfold")
    run(home, "config", "label", "Work")
    run(home, "config", "label", "auto", fake_home=fake)
    assert stored(home) == "Pranfold"
    assert _json.loads((home / "profiles" / PROFILE / "rc.json").read_text()).get(
        "bar_label_manual") is None
    _claude_json(fake, displayName="Third")
    seed(home, fake_home=fake)
    assert stored(home) == "Third"


# ---- live, not only at launch ------------------------------------------------
#
# "All of the other information is also useful for the user as live as possible so
# that it can understand which account and which bot" — 19 Aug. Seeding at launch
# left a mid-session /login showing the wrong account until the next restart.


def statusline(home, fake_home=None):
    env = dict(os.environ, ABS_HOME=str(home))
    for k in ("TELEGRAM_STATE_DIR", "ABS_SESSION_PROFILE"):
        env.pop(k, None)
    if fake_home is not None:
        env["HOME"] = str(fake_home)
    return subprocess.run(
        ["bash", ABS_SH, "--profile", PROFILE, "statusline"],
        capture_output=True, text=True, env=env, input="",
    )


def test_the_bar_picks_up_a_mid_session_account_change(home, tmp_path):
    import json as _json
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="Pran")
    seed(home, fake_home=fake)
    assert "Pran" in statusline(home, fake_home=fake).stdout

    # Logged into another account without restarting. Age the throttle out.
    _claude_json(fake, displayName="Pranfold")
    rc = home / "profiles" / PROFILE / "rc.json"
    d = _json.loads(rc.read_text()); d["bar_label_at"] = 0; rc.write_text(_json.dumps(d))

    assert "Pranfold" in statusline(home, fake_home=fake).stdout
    assert stored(home) == "Pranfold"


def test_the_bar_does_not_re_read_the_account_file_every_frame(home, tmp_path):
    """~/.claude.json is large and holds tokens. Within the TTL the render path
    must not touch it — the guard that keeps this from being a per-frame cost.
    Corrupting the file is the test: if it were read, the label would go."""
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="Pran")
    seed(home, fake_home=fake)
    (fake / ".claude.json").write_text("{ this is not json")   # would fail if read
    out = statusline(home, fake_home=fake)
    assert "Pran" in out.stdout
    assert stored(home) == "Pran"


def test_a_pinned_label_is_not_touched_by_the_render_path(home, tmp_path):
    import json as _json
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="Pran")
    run(home, "config", "label", "Work")
    _claude_json(fake, displayName="Pranfold")
    rc = home / "profiles" / PROFILE / "rc.json"
    d = _json.loads(rc.read_text()); d["bar_label_at"] = 0; rc.write_text(_json.dumps(d))
    assert "Work" in statusline(home, fake_home=fake).stdout
    assert stored(home) == "Work"


def test_a_cleared_label_is_not_touched_by_the_render_path(home, tmp_path):
    import json as _json
    fake = tmp_path / "fakehome"
    _claude_json(fake, displayName="Pran")
    seed(home, fake_home=fake)
    run(home, "config", "label", "--clear")
    rc = home / "profiles" / PROFILE / "rc.json"
    d = _json.loads(rc.read_text()); d["bar_label_at"] = 0; rc.write_text(_json.dumps(d))
    statusline(home, fake_home=fake)
    assert stored(home) is None
