"""Running two bots is the normal shape of two projects, not a conflict.

Reported 1 Oct, minutes after ABS told the operator to start a second bot:

    Two of the bots are running. … now on both of them there is a tagline:
    "Telegram conflict ABS reconnect."

Nothing was conflicting. The 3.7.2 detector counted `server.ts` processes across
the whole machine and called two of them a conflict, on the premise that one
poller per machine is correct. That premise is only true when the machine runs
one bot — and running several, one per project, is a feature this tool
advertises on its own front page.

A 409 is two pollers on the SAME token. Two pollers on two tokens is two
profiles working. So the count means nothing until it is compared with how many
bots are legitimately being polled: `pollers_accounted`, one per profile whose
recorded bot.pid is still alive. More pollers than that, and one is unaccounted
for; otherwise it is just somebody's second project.

The same report exposed a worse latent bug, covered at the bottom: with a false
"conflict", the per-turn watchdog could reclaim a *working* poller of its own.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ABS_SH = REPO / "abs.sh"
SRC = ABS_SH.read_text()


def health(tmp_path, pollers: int, profiles: int):
    """bridge_health with the two counts stubbed, and the MCP log forced absent so
    the passive branch is the one under test."""
    script = tmp_path / "probe.sh"
    script.write_text(SRC.replace('main "$@"\n', "") + f"""
pollers_running() {{ seq 1 {pollers} 2>/dev/null || true; }}
pollers_accounted() {{ printf '{profiles}'; }}
bridge_log_says() {{ return 1; }}
bridge_health
printf 'STATE=%s DETAIL=%s\\n' "$BRIDGE_STATE" "$BRIDGE_DETAIL"
""")
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                       env=dict(os.environ, HOME=str(tmp_path),
                                ABS_HOME=str(tmp_path / ".abs")))
    return r.stdout.strip().split("\n")[-1]


# ---- the report ----------------------------------------------------------------------


@pytest.mark.parametrize("n", [2, 3, 5])
def test_one_poller_per_live_profile_is_never_a_conflict(tmp_path, n):
    out = health(tmp_path, pollers=n, profiles=n)
    assert "STATE=retrying" not in out, (
        f"{n} bots, {n} pollers — that is {n} projects working, not a conflict:\n{out}")


def test_a_single_bot_is_still_fine(tmp_path):
    assert "STATE=retrying" not in health(tmp_path, pollers=1, profiles=1)


def test_nothing_running_is_not_a_conflict_either(tmp_path):
    out = health(tmp_path, pollers=0, profiles=0)
    assert "STATE=retrying" not in out
    assert "no poller" in out


# ---- what the detector is actually for -----------------------------------------------


def test_a_poller_more_than_there_are_profiles_is_a_conflict(tmp_path):
    """The real 409 signature: something is polling a token nobody accounts for."""
    out = health(tmp_path, pollers=2, profiles=1)
    assert "STATE=retrying" in out, out
    assert "unaccounted" in out


def test_it_still_fires_with_several_bots_running(tmp_path):
    """Three pollers for two profiles — the stray must not hide behind the others."""
    out = health(tmp_path, pollers=3, profiles=2)
    assert "STATE=retrying" in out, out


def test_the_plugins_own_words_still_win_over_the_count(tmp_path):
    """When the MCP log carries a real 409 it is evidence, and beats arithmetic."""
    script = tmp_path / "probe2.sh"
    script.write_text(SRC.replace('main "$@"\n', "") + """
pollers_running() { seq 1 2; }
pollers_accounted() { printf '2'; }
bridge_log_says() { printf 'telegram channel: 409 Conflict, retrying in 3s'; }
bridge_health
printf 'STATE=%s\\n' "$BRIDGE_STATE"
""")
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                       env=dict(os.environ, HOME=str(tmp_path), ABS_HOME=str(tmp_path / ".abs")))
    assert "STATE=retrying" in r.stdout


# ---- the latent bug the false positive exposed ----------------------------------------


def test_the_watchdog_never_kills_a_poller_on_an_unknown_verdict():
    """With the false conflict, any profile whose own poller came back `unknown`
    would have had it reclaimed — silently, on an ordinary turn. `unknown` means
    the owner could not be found, which is not evidence that there is none.
    `abs reconnect`, which the operator runs deliberately, still handles it."""
    body = SRC[SRC.index("_bridge_watchdog() {"):]
    body = body[:body.index("\n}\n")]
    assert "orphan|unknown" not in body, (
        "the automatic path must not reclaim on a guess")
    assert "orphan)" in body, "an orphan is still unambiguous and still reclaimed"
    # and the deliberate command keeps the broader handling
    rec = SRC[SRC.index("cmd_reconnect() {"):]
    rec = rec[:rec.index("\n}\n")]
    assert "orphan|unknown" in rec, "abs reconnect should still clear an unknown holder"


def test_pollers_accounted_restores_the_profile_it_was_called_from():
    """It walks every profile with use_profile; leaving PROFILE pointing somewhere
    else would silently retarget whatever ran next — including the status bar."""
    body = SRC[SRC.index("pollers_accounted() {"):]
    body = body[:body.index("\n}\n")]
    assert 'saved="$PROFILE"' in body
    assert 'use_profile "$saved"' in body
