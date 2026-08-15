"""`abs config label` — the name before the colon in the status bar.

`abs:@Claudepranbot` becomes `Pran:@Claudepranbot`. Cosmetic, with one sharp
edge: the value is printed into a terminal status bar surrounded by real ESC
bytes, on every Claude Code render. So the command **sanitises** rather than
validates, and refuses input that sanitises to nothing instead of silently
storing something the bar will ignore.

`auto` reads `.oauthAccount.displayName` from `~/.claude.json` — exactly that one
field, never the file, which also holds account tokens. It resolves ONCE and
stores the result, so no render pays for the read and the label can't change
under the operator later.

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
