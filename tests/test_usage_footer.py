"""The usage footer, appended by abs rather than remembered by the model.

`📊 Fable 0% · Week 43% · 5H 62% · ctx 68%` used to reach Telegram only if the
model ran `usage-glance` and pasted the output. It didn't, reliably, and the
operator reported never seeing the numbers or the context percentage on his
phone at all. Same lesson as reply mode: an instruction in the prompt is a wish,
a hook is a guarantee.

The honest limit, pinned by a test at the bottom: abs can only append to a
message abs is sending. In voice-first `both` — the default on a machine that
can speak — the gate blocks the reply tool and abs does the send, so the footer
is guaranteed. In plain `text` mode the plugin sends and abs never sees the
bytes.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ABS_SH = os.path.join(REPO, "abs.sh")

PROFILE = "footertest"

# What the cache holds after a status-bar render absorbs a payload.
CACHE = {
    "session_pct": 62,
    "week_pct": 43,
    "fable_pct": 0,
    "ctx_left_pct": 68,
    "fetched_at": 4102444800,   # far future, so nothing kicks a refresh
    "source": "statusline",
}


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "abshome"
    prof = h / "profiles" / PROFILE
    prof.mkdir(parents=True)
    (prof / "rc.json").write_text(json.dumps({"bot": "testbot", "chat_id": 42}))
    (prof / "usage.json").write_text(json.dumps(CACHE))
    return h


def _env(home, **extra):
    env = dict(os.environ, ABS_HOME=str(home))
    for k in ("TELEGRAM_STATE_DIR", "ABS_SESSION_PROFILE"):
        env.pop(k, None)
    env.update(extra)
    return env


def run(home, *args, **extra):
    return subprocess.run(["bash", ABS_SH, "--profile", PROFILE, *args],
                          capture_output=True, text=True, env=_env(home, **extra))


def sh(home, snippet, **extra):
    """Call a function inside abs.sh directly, with `main` never running."""
    body = "".join(l for l in open(ABS_SH) if l.strip() != 'main "$@"')
    script = home.parent / "call.sh"
    script.write_text(f"{body}\nuse_profile {PROFILE}\n{snippet}\n")
    return subprocess.run(["bash", str(script)], capture_output=True, text=True,
                          env=_env(home, **extra))


# ---- the line itself ---------------------------------------------------------


def test_the_footer_reads_the_same_cache_the_status_bar_does(home):
    out = sh(home, 'usage_footer_line').stdout
    assert out.startswith("📊 ")
    assert "Week 43%" in out and "5H 62%" in out and "ctx 68%" in out


def test_the_footer_carries_no_colour(home):
    """It is going into a chat message, where an ESC sequence is literal junk."""
    assert "\x1b[" not in sh(home, 'usage_footer_line').stdout


def test_a_cold_cache_produces_no_footer_rather_than_a_wrong_one(home):
    (home / "profiles" / PROFILE / "usage.json").unlink()
    assert sh(home, 'usage_footer_line').stdout == ""


# ---- appending it ------------------------------------------------------------


def test_it_is_appended_after_a_blank_line(home):
    out = sh(home, 'with_usage_footer "the suite is green"').stdout
    body, sep, footer = out.partition("\n\n")
    assert body == "the suite is green"
    assert sep == "\n\n", out
    assert footer.startswith("📊 ")


def test_a_cold_cache_leaves_the_message_exactly_as_it_was(home):
    (home / "profiles" / PROFILE / "usage.json").unlink()
    assert sh(home, 'with_usage_footer "the suite is green"').stdout == "the suite is green"


def test_a_message_that_already_ends_in_a_footer_does_not_get_a_second(home):
    text = "done\\n\\n📊 Week 43% · 5H 62%"
    out = sh(home, f'with_usage_footer "$(printf "%b" "{text}")"').stdout
    assert out.count("📊") == 1, out


def test_a_report_may_mention_the_chart_emoji_in_its_body(home):
    """The dedup looks at the LAST line only. Skipping the footer because the
    word 📊 appears in a sentence is the exact failure this feature exists to
    prevent, arriving by a different door."""
    text = "I put 📊 next to the numbers in the doc.\\n\\nand that is all"
    out = sh(home, f'with_usage_footer "$(printf "%b" "{text}")"').stdout
    assert out.rstrip().splitlines()[-1].startswith("📊 "), out
    assert out.count("📊") == 2, out


def test_a_message_near_telegrams_ceiling_keeps_the_report_and_drops_the_footer(home):
    """Telegram REJECTS a message over 4096 characters rather than truncating it,
    and the retry would fail the same way. Losing a whole report to add a status
    line is the wrong trade."""
    out = sh(home, 'with_usage_footer "$(printf "x%.0s" $(seq 1 4090))"').stdout
    assert "📊" not in out
    assert len(out) == 4090


def test_a_message_that_fits_with_the_footer_still_gets_it(home):
    out = sh(home, 'with_usage_footer "$(printf "x%.0s" $(seq 1 100))"').stdout
    assert "📊" in out


# ---- the switch --------------------------------------------------------------


def test_it_can_be_turned_off(home):
    assert run(home, "config", "footer", "off").returncode == 0
    assert sh(home, 'with_usage_footer "done"').stdout == "done"


def test_it_can_be_turned_back_on(home):
    run(home, "config", "footer", "off")
    assert run(home, "config", "footer", "on").returncode == 0
    assert "📊" in sh(home, 'with_usage_footer "done"').stdout


def test_it_is_on_without_anyone_asking(home):
    out = run(home, "config", "footer")
    assert "on" in out.stderr


def test_it_shows_in_the_config_listing(home):
    assert "usage footer" in run(home, "config").stderr


def test_garbage_is_refused_rather_than_guessed(home):
    out = run(home, "config", "footer", "maybe")
    assert out.returncode != 0
    assert "on|off" in out.stderr
