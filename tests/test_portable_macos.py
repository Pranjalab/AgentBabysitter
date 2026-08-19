"""The three things the status bar got wrong on macOS and right on Linux.

Reported 19 Aug with both bars side by side:

    mac:   Pran:@abs_test_002_bot · ● Text · ● Voice · Week 8% · 5H 10% (resets Aug 19 at 5:49pm) · v3.5.3
    linux: Pranfold:@Claudepranbot · ● Text · ● Voice · Week 8% (resets on Thu) · 5H 8% (resets in 4h 38m) · ctx 91% · v3.5.3

Three symptoms, two causes, and neither was visible on Linux because on Linux the
broken code never runs.

**No context percentage, and reset stamps in the `/usage` text format.** Both say
the same thing: the Mac never absorbed Claude Code's render payload, and fell back
to polling `claude -p /usage`. The payload is read through `with_timeout 1 cat`,
and `with_timeout` only falls back to its own watchdog when GNU `timeout(1)` is
absent — which is every stock macOS and no Linux. That fallback backgrounds the
command, and bash redirects an async command's stdin from /dev/null unless the
command carries an explicit redirection. So `cat` read /dev/null and returned
nothing, on every frame.

**No weekday on the weekly limit, and a raw stamp on the five-hour one.** `date -d`
is GNU. BSD date rejects it and wants `-j -f`. macOS ships only BSD.

Both are tested here the only way they can be tested from Linux: by taking the
GNU tools away.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ABS_SH = os.path.join(REPO, "abs.sh")

PROFILE = "mactest"


@pytest.fixture(scope="module")
def no_timeout_path(tmp_path_factory):
    """A PATH with every tool on this box except `timeout` and `gtimeout`.

    That is precisely what macOS looks like to `with_timeout`: coreutils absent,
    everything else present. Simulating it by unsetting PATH entirely would prove
    nothing — the bug is specific to which single binary is missing.
    """
    d = tmp_path_factory.mktemp("nobin")
    for src in ("/usr/bin", "/bin", "/usr/local/bin"):
        if not os.path.isdir(src):
            continue
        for name in os.listdir(src):
            if name in ("timeout", "gtimeout"):
                continue
            link = d / name
            if not link.exists():
                try:
                    link.symlink_to(os.path.join(src, name))
                except OSError:
                    pass
    assert not (d / "timeout").exists()
    return str(d)


def _lib(tmp_path, snippet):
    """Run a snippet against abs.sh's functions, with main() stripped."""
    body = "".join(l for l in open(ABS_SH) if l.strip() != 'main "$@"')
    f = tmp_path / "lib.sh"
    f.write_text(body + "\n" + snippet + "\n")
    return f


def run(script, path=None, stdin="", **env_extra):
    env = dict(os.environ)
    for k in ("TELEGRAM_STATE_DIR", "ABS_SESSION_PROFILE"):
        env.pop(k, None)
    if path is not None:
        env["PATH"] = path
    env.update(env_extra)
    return subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, env=env, input=stdin
    )


# ---- with_timeout must pass stdin through, like timeout(1) does ---------------


def test_with_timeout_reads_stdin_when_coreutils_is_missing(tmp_path, no_timeout_path):
    """The bug, in one line. This returned empty on every Mac."""
    s = _lib(tmp_path, 'printf "[%s]" "$(with_timeout 1 cat)"')
    out = run(s, path=no_timeout_path, stdin="hello payload")
    assert out.stdout == "[hello payload]", out.stderr


def test_with_timeout_reads_stdin_when_coreutils_is_present(tmp_path):
    """The Linux path, unchanged — and the reason this went unnoticed for weeks."""
    s = _lib(tmp_path, 'printf "[%s]" "$(with_timeout 1 cat)"')
    out = run(s, stdin="hello payload")
    assert out.stdout == "[hello payload]", out.stderr


def test_with_timeout_still_kills_what_overruns(tmp_path, no_timeout_path):
    """Passing stdin through must not cost the timeout its teeth: a command that
    holds the pipe open forever still has to be cut off. `|| true` because a
    killed child exits 143 and every real caller already writes it that way."""
    s = _lib(tmp_path, 'with_timeout 1 sleep 30 || true; printf "done"')
    began = time.time()
    out = run(s, path=no_timeout_path, stdin="")
    assert out.stdout == "done", out.stderr
    assert time.time() - began < 10, "the watchdog did not fire"


def test_with_timeout_returns_promptly_on_success(tmp_path, no_timeout_path):
    """And the watchdog must not hold the capture pipe for its full deadline
    after the command has already finished."""
    s = _lib(tmp_path, 'printf "[%s]" "$(with_timeout 5 echo quick)"')
    began = time.time()
    out = run(s, path=no_timeout_path)
    assert "quick" in out.stdout
    assert time.time() - began < 4, "waited out the deadline on a fast command"


# ---- dates, without GNU date -------------------------------------------------


def test_an_epoch_stamp_needs_no_date_at_all(tmp_path):
    """The live path. Claude Code's payload delivers `resets_at` as an epoch, and
    an epoch is arithmetic — so the whole GNU/BSD split stops applying to it."""
    soon = int(time.time()) + 9000
    s = _lib(tmp_path, f'until_reset "{soon}"')
    assert run(s).stdout == "in 2h 30m"


def test_the_weekday_matches_gnu_date_everywhere(tmp_path):
    """`epoch_weekday` is hand-rolled from `date +%z`, which is the one spelling
    both dialects share. Cross-checked against GNU date across half-hour steps and
    five timezones, including a +14 and a -11."""
    snippet = r'''
      fail=0
      for i in $(seq 0 199); do
        e=$(( 1600000000 + i * 43201 ))
        mine="$(epoch_weekday $e)"
        theirs="$(date -d "@$e" +%a)"
        [ "$mine" = "$theirs" ] || { fail=$((fail+1)); echo "MISMATCH $e $mine/$theirs"; }
      done
      echo "fail=$fail"
    '''
    s = _lib(tmp_path, snippet)
    for tz in ("UTC", "Asia/Kolkata", "America/Los_Angeles",
               "Pacific/Kiritimati", "Pacific/Midway"):
        out = run(s, TZ=tz)
        assert "fail=0" in out.stdout, f"{tz}: {out.stdout}"


def test_a_stamp_that_cannot_be_read_degrades_instead_of_breaking(tmp_path):
    s = _lib(tmp_path, 'until_reset "not a date at all"')
    assert run(s).stdout == "not a date at all"


def test_a_reset_already_past_reads_as_now_not_as_a_year_away(tmp_path):
    """The `resets in 8755h` bug: a stale cache whose window has rolled over must
    not be mistaken for a year-wrap."""
    past = int(time.time()) - 600
    s = _lib(tmp_path, f'until_reset "{past}"')
    assert run(s).stdout == "now"


# ---- and the bar itself ------------------------------------------------------


@pytest.fixture
def mac(tmp_path):
    """A profile plus a fake HOME, so the bar's account read is sandboxed."""
    home = tmp_path / "abshome"
    (home / "profiles" / PROFILE).mkdir(parents=True)
    (home / "profiles" / PROFILE / "rc.json").write_text(
        json.dumps({"bot": "abs_test_002_bot", "chat_id": 1})
    )
    fake = tmp_path / "fakehome"
    fake.mkdir()
    (fake / ".claude.json").write_text(
        json.dumps({"oauthAccount": {"displayName": "Pranfold"}})
    )
    return home, fake


def _payload(ctx=91.4, five=10.2, week=8.1):
    now = int(time.time())
    return json.dumps({
        "context_window": {"remaining_percentage": ctx},
        "rate_limits": {
            "five_hour": {"used_percentage": five, "resets_at": str(now + 18000)},
            "seven_day": {"used_percentage": week, "resets_at": str(now + 259200)},
        },
    })


def _bar(home, fake, path=None, stdin=""):
    env = dict(os.environ, ABS_HOME=str(home), HOME=str(fake))
    for k in ("TELEGRAM_STATE_DIR", "ABS_SESSION_PROFILE"):
        env.pop(k, None)
    if path is not None:
        env["PATH"] = path
    out = subprocess.run(
        ["bash", ABS_SH, "--profile", PROFILE, "statusline"],
        capture_output=True, text=True, env=env, input=stdin,
    )
    assert out.returncode == 0, out.stderr
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", out.stdout)


def test_the_mac_bar_now_says_everything_the_linux_bar_says(mac, no_timeout_path):
    """The whole report, as one assertion. Every field the operator listed as
    missing has to be there, on a box with no GNU coreutils."""
    home, fake = mac
    bar = _bar(home, fake, path=no_timeout_path, stdin=_payload())
    assert "Pranfold:@abs_test_002_bot" in bar
    assert "ctx 91%" in bar, "the context percentage was the one thing with no other source"
    assert "5H 10% (resets in " in bar, f"no countdown: {bar}"
    assert "Week 8% (resets on " in bar, f"no weekday: {bar}"
    assert "at 5:49pm" not in bar and "resets Aug" not in bar


def test_the_payload_reaches_the_cache_without_coreutils(mac, no_timeout_path):
    """Not just rendered — stored, which is what the Telegram footer reads."""
    home, fake = mac
    _bar(home, fake, path=no_timeout_path, stdin=_payload())
    cache = json.loads((home / "profiles" / PROFILE / "usage.json").read_text())
    assert cache["source"] == "statusline"
    assert cache["ctx_left_pct"] == 91
    assert cache["session_pct"] == 10 and cache["week_pct"] == 8


def test_both_platforms_render_the_same_bar(mac, no_timeout_path, tmp_path):
    """The point of the whole fix: one payload, two environments, one string."""
    home, fake = mac
    payload = _payload()
    with_gnu = _bar(home, fake, stdin=payload)
    without = _bar(home, fake, path=no_timeout_path, stdin=payload)
    assert with_gnu == without, f"\nlinux: {with_gnu}\nmacos: {without}"


def test_an_unreadable_reset_drops_the_note_rather_than_printing_a_stamp(mac):
    """Ten columns of bar have no room to show the thing we failed to convert.
    `abs usage` does, and still shows it."""
    home, fake = mac
    (home / "profiles" / PROFILE / "usage.json").write_text(json.dumps({
        "session_pct": 10, "week_pct": 8,
        "session_reset": "Aug 19 at 5:49pm", "week_reset": "Aug 21 at 5:49pm",
        "fetched_at": 4102444800, "source": "statusline",
    }))
    bar = _bar(home, fake, stdin="")
    assert "5:49pm" not in bar, bar
    assert "5H 10%" in bar and "Week 8%" in bar
