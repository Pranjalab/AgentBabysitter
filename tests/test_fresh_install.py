"""A fresh machine: the plugin installs without login, and login is named once.

24 Sep, reported from a new install: "the Claude Telegram plugin is not getting
installed because the user is not logged in." The login was a red herring — a
logged-out `claude plugin install telegram@claude-plugins-official` succeeds.
What a fresh machine lacks is the MARKETPLACE: without it the install fails with
"not found in marketplace … your local copy may be out of date", which reads
like a broken ABS.

So: ABS adds the marketplace itself (no login, no SSH key needed — it falls back
to https), retries once after a refresh, and prints the two commands if it still
cannot. Login is required only to START a session, and is now checked before the
update prompt and the menus rather than after the operator has chosen a persona
and a project.

`claude` is stubbed here: the real one clones a repository and takes a minute.
The stub records what it was asked to do, in order.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ABS_SH = REPO / "abs.sh"


@pytest.fixture
def box(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    abs_home = home / ".abs"
    abs_home.mkdir()
    binp = tmp_path / "bin"
    binp.mkdir()
    log = tmp_path / "claude.log"
    state = tmp_path / "claude_state"
    state.mkdir()

    # A stub `claude` with the real one's shape: the marketplace is absent until
    # added, and `plugin install` fails while it is.
    (binp / "claude").write_text(f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> {log}
mk={state}/market
inst={state}/installed
case "$1 $2" in
  "plugin list")
    if [ -f "$inst" ]; then echo "  telegram@claude-plugins-official"; else echo "No plugins installed."; fi
    exit 0 ;;
  "plugin marketplace")
    case "$3" in
      list)   if [ -f "$mk" ]; then echo "  claude-plugins-official"; else echo "No marketplaces configured"; fi; exit 0 ;;
      add)    if [ -f "{state}/no_market" ]; then echo "✘ failed" >&2; exit 1; fi
              : > "$mk"; echo "✔ Successfully added marketplace"; exit 0 ;;
      update) exit 0 ;;
    esac ;;
  "plugin install")
    if [ -f "$mk" ]; then : > "$inst"; echo "✔ installed"; exit 0; fi
    echo '✘ Plugin "telegram" not found in marketplace "claude-plugins-official".' >&2
    exit 1 ;;
esac
exit 0
""")
    (binp / "claude").chmod(0o755)

    # Bound outside the class body: a class body cannot read a name it also
    # defines, and `home = home` is exactly that.
    _home, _log, _state = home, log, state

    class Box:
        home = _home
        log = _log
        state = _state

        def login(self, yes=True):
            """Set or clear the logged-in state of this fake machine.

            The conftest autouse fixture seeds a credentials file into any temp
            HOME (a launch now refuses without one); this suite is the one that
            is ABOUT that gate, so `login(False)` also blocks the reseed.
            """
            f = home / ".claude" / ".credentials.json"
            j = home / ".claude.json"
            if yes:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text('{"token": "x"}')
            else:
                for path in (f, j):
                    if path.exists():
                        path.unlink()
                self._logged_out = True

        def _reclear(self):
            if getattr(self, "_logged_out", False):
                for path in (home / ".claude" / ".credentials.json",):
                    if path.exists():
                        path.unlink()

        def run(self, *args, **extra):
            env = dict(os.environ, HOME=str(home), ABS_HOME=str(abs_home),
                       PATH=f"{binp}:{os.environ.get('PATH', '')}")
            env.pop("TELEGRAM_STATE_DIR", None)
            env.update(extra)
            out = subprocess.run(["bash", "-c", "true"], env=env)   # let the seeder fire first
            self._reclear()                                          # …then clear, if this test is logged out
            return subprocess.run(["bash", str(ABS_SH), *args],
                                  capture_output=True, text=True, env=env, input="")

        def calls(self):
            return [l for l in log.read_text().splitlines()] if log.exists() else []

        def probe(self, snippet, **extra):
            """Run one abs function against this home, with `main` stripped."""
            src = ABS_SH.read_text().replace('main "$@"\n', "")
            script = tmp_path / "probe.sh"
            script.write_text(src + f'\nPROFILE=default; ABS_DIR="$ABS_HOME"\n{snippet}\n')
            env = dict(os.environ, HOME=str(home), ABS_HOME=str(abs_home),
                       PATH=f"{binp}:{os.environ.get('PATH', '')}")
            env.pop("TELEGRAM_STATE_DIR", None)
            env.update(extra)
            subprocess.run(["bash", "-c", "true"], env=env)
            self._reclear()
            return subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)

    return Box()


# ---- the marketplace, which was the real bug -----------------------------------


def test_a_fresh_machine_gets_the_marketplace_added_then_the_plugin(box):
    box.login(False)
    r = box.probe("ensure_plugin")
    assert r.returncode == 0, r.stderr
    calls = box.calls()
    assert any("marketplace add anthropics/claude-plugins-official" in c for c in calls), calls
    assert any(c.startswith("plugin install telegram@claude-plugins-official") for c in calls), calls
    assert "Installed telegram@claude-plugins-official" in r.stderr + r.stdout


def test_being_logged_out_does_not_stop_the_plugin(box):
    """The reported cause. It is not one: the install needs no credentials."""
    box.login(False)
    assert box.probe("ensure_plugin").returncode == 0


def test_an_existing_marketplace_is_not_added_again(box):
    box.login(True)
    (box.state / "market").write_text("")
    box.probe("ensure_plugin")
    assert not any("marketplace add" in c for c in box.calls()), box.calls()


def test_an_installed_plugin_is_left_alone(box):
    (box.state / "market").write_text("")
    (box.state / "installed").write_text("")
    box.probe("ensure_plugin")
    assert not any("plugin install" in c for c in box.calls()), box.calls()


def test_a_failed_marketplace_add_names_both_commands_and_stops(box):
    (box.state / "no_market").write_text("")
    box.login(False)
    r = box.probe("ensure_plugin")
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert "marketplace add anthropics/claude-plugins-official" in out
    assert "plugin install telegram@claude-plugins-official --scope user" in out
    assert "log in first" in out          # logged out, so the hint is there too


def test_the_marketplace_is_refreshed_before_the_one_retry(box):
    """The other way this fails: a cached marketplace older than the plugin."""
    (box.state / "market").write_text("")          # present, but the stub will fail once
    box.probe('claude() { case "$*" in *"plugin install"*) return 1 ;; esac; command claude "$@"; }; ensure_plugin || true')
    assert any("marketplace update claude-plugins-official" in c for c in box.calls()), box.calls()


# ---- login: needed to run, not to install --------------------------------------


def test_login_detection_reads_presence_only(box):
    box.login(True)
    assert box.probe("claude_logged_in && echo YES").stdout.strip().endswith("YES")
    box.login(False)
    assert "YES" not in box.probe("claude_logged_in && echo YES").stdout


def test_an_oauth_account_in_claude_json_also_counts(box):
    """macOS can keep the token in the keychain; ~/.claude.json gets the account."""
    box.login(False)
    (box.home / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": "x@y.z"}}))
    assert box.probe("claude_logged_in && echo YES").stdout.strip().endswith("YES")


def test_a_logged_out_launch_says_what_to_run_and_stops(box):
    box.login(False)
    r = box.run()
    out = r.stdout + r.stderr
    assert "not logged in" in out
    assert "run:" in out and "claude" in out
    assert r.returncode != 0
    assert "Who am I this session?" not in out       # stopped before any choice
