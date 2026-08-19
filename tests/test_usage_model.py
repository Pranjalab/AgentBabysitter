"""The per-model weekly line, whatever the model is called.

`/usage` reports a per-model line only once that model has been used this week,
and it is named after the model: "Current week (Fable)", "Current week (Opus)".

The parser grepped for the literal word **Fable**. Any other model reported
nothing at all — which is what the operator hit after switching accounts, and
why a figure he expected to climb sat at zero.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ABS_SH = REPO / "abs.sh"
PROFILE = "usagetest"

SESSION = "Current session: 28% used · resets Aug 19, 10:19am (Asia/Kolkata)"
WEEK = "Current week (all models): 3% used · resets Aug 20, 5:29pm (Asia/Kolkata)"


@pytest.fixture
def box(tmp_path):
    home = tmp_path / "abshome"
    (home / "profiles" / PROFILE).mkdir(parents=True)
    (home / "profiles" / PROFILE / "rc.json").write_text(
        json.dumps({"bot": "b", "chat_id": 42}))

    class Box:
        abs_home = home
        cache = home / "profiles" / PROFILE / "usage.json"

        def absorb(self, raw):
            body = "".join(l for l in open(ABS_SH) if l.strip() != 'main "$@"')
            sc = tmp_path / "u.sh"
            sc.write_text(f"{body}\nuse_profile {PROFILE}\n"
                          f"usage_cache_write \"$(cat {tmp_path}/raw.txt)\"\n"
                          "usage_glance_str\n")
            (tmp_path / "raw.txt").write_text(raw)
            env = dict(os.environ, ABS_HOME=str(home))
            env.pop("TELEGRAM_STATE_DIR", None)
            return subprocess.run(["bash", str(sc)], capture_output=True,
                                  text=True, env=env, timeout=60).stdout

    return Box()


def test_a_fable_line_is_read(box):
    out = box.absorb(f"{SESSION}\n{WEEK}\nCurrent week (Fable): 41% used · resets Aug 20, 5:29pm")
    assert "Fable 41%" in out, out


def test_a_model_that_is_not_fable_is_read_too(box):
    """The bug. Hardcoding one model name meant every other account reported
    nothing, and nothing renders the same as never having used it."""
    out = box.absorb(f"{SESSION}\n{WEEK}\nCurrent week (Opus): 41% used · resets Aug 20, 5:29pm")
    assert "Opus 41%" in out, out
    assert "Fable" not in out, "it labelled an Opus figure as Fable"


def test_the_all_models_line_is_not_mistaken_for_a_model(box):
    """It comes FIRST in the output, so the obvious `grep -m1 | grep -v` returns
    nothing at all — the filter runs after the line has already been chosen."""
    out = box.absorb(f"{SESSION}\n{WEEK}")
    assert "Week 3%" in out, out
    assert "all models" not in out


def test_no_per_model_line_shows_no_segment(box):
    out = box.absorb(f"{SESSION}\n{WEEK}")
    assert "Model" not in out and "Fable" not in out, out


def test_a_zero_is_shown_rather_than_hidden(box):
    """0% is a real reading — "used none of it this week" — and dropping it looks
    identical to the parse having failed, which is the confusion being fixed."""
    out = box.absorb(f"{SESSION}\n{WEEK}\nCurrent week (Fable): 0% used")
    assert "Fable 0%" in out, out


def test_the_model_name_cannot_paint_the_status_bar(box):
    """It ends up between ESC sequences on every render."""
    out = box.absorb(f"{SESSION}\n{WEEK}\nCurrent week (Op\x1b[31mus): 12% used")
    assert "\x1b[31m" not in out, repr(out)
