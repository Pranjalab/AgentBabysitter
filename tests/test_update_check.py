"""The launch-time update check, and its one job: never get in the way.

It exists to say "3.0.1 is out". It is optional, it is advisory, and it talks to
the network — so the only behaviour that really matters is what happens when the
network does not answer.

Until this file there were no tests here at all, and the failure was the obvious
one in hindsight. `curl` exits 28 on a timeout; `pipefail` makes the pipeline
report that even though `tr` succeeded; `set -e` then kills the command
substitution *before* the function's own `return 0` can restore the "always
succeeds" contract its comment promised. The status propagated up three frames
into the ERR trap and took `abs` with it: four "Unexpected failure" lines and no
session, because a version check could not reach GitHub.

An unreachable host is not an edge case. It is an offline laptop, hotel wifi, a
corporate firewall, or GitHub having a bad ten minutes.

`10.255.255.1` is non-routable, so curl reaches `--connect-timeout` and exits 28 —
a real timeout, no network access, no mocking of our own code. A reachable URL is
faked with `file://`, which curl handles natively.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ABS_SH = REPO / "abs.sh"
PROFILE = "updtest"

UNREACHABLE = "https://10.255.255.1/VERSION"

pytestmark = pytest.mark.skipif(
    shutil.which("curl") is None, reason="curl required"
)


@pytest.fixture
def box(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    ahome = tmp_path / "abs"
    prof = ahome / "profiles" / PROFILE
    prof.mkdir(parents=True)
    (prof / "rc.json").write_text(json.dumps({"bot": "b", "chat_id": "42"}))

    class Box:
        # Bound from the enclosing scope explicitly: a class body cannot read a
        # local of the same name from the function around it.
        path = tmp_path
        abs_home = ahome
        profile_dir = prof

        def call(self, snippet, url=UNREACHABLE, **extra):
            """Run a snippet inside abs.sh with `main` never running."""
            body = "".join(l for l in open(ABS_SH) if l.strip() != 'main "$@"')
            script = tmp_path / "call.sh"
            script.write_text(f"{body}\nuse_profile {PROFILE}\n{snippet}\n")
            env = dict(os.environ)
            for k in list(env):
                if k.startswith("ABS_") or k.startswith("TELEGRAM_"):
                    env.pop(k, None)
            env.update(HOME=str(home), ABS_HOME=str(ahome), ABS_VERSION_URL=url)
            env.update(extra)
            return subprocess.run(
                ["bash", str(script)], capture_output=True, text=True,
                env=env, timeout=120,
            )

        def serve(self, text):
            """A reachable VERSION, without a network: curl speaks file://."""
            f = tmp_path / "VERSION"
            f.write_text(text)
            return f"file://{f}"

        def cache(self, version):
            (ahome / "profiles" / PROFILE).mkdir(parents=True, exist_ok=True)
            (ahome / "profiles" / PROFILE / "update.json").write_text(
                json.dumps({"latest": version, "checked_at": 1})
            )

    return Box()


# ---- the regression ----------------------------------------------------------


def test_an_unreachable_host_does_not_kill_the_script(box):
    """The bug Pranjal hit mid-release: `abs` exited 28 and started no session."""
    run = box.call('latest="$(_latest_known)"; echo "reached-the-end [$latest]"')
    assert run.returncode == 0, run.stderr
    assert "reached-the-end []" in run.stdout


def test_an_unreachable_host_prints_no_error_at_all(box):
    """Four ERR-trap lines for an optional version check is its own bug, even if
    the script survived them — they are unexplainable to the person reading them."""
    run = box.call('_latest_known >/dev/null')
    assert "Unexpected failure" not in run.stderr, run.stderr
    assert run.stderr.strip() == "", run.stderr


# Stub `curl`: exit 28 for the version URL (a real timeout's status), answer
# anything else — i.e. Telegram — with an ok. Keeps the test off the network in
# both directions while reproducing the exact failure faithfully; what is under
# test is our error propagation, not curl.
#
# stdin is only drained when `-K` is present. tg_api passes its URL that way;
# `_fetch_latest` puts the URL in argv, and an unconditional `cat` would hang
# forever waiting on a terminal that is never going to type anything.
_STUB_CURL = """#!/bin/sh
args="$*"
case "$args" in *-K*) cat >/dev/null 2>&1 ;; esac
case "$args" in
  *10.255.255.1*) exit 28 ;;
  *) printf '{"ok":true,"result":{"message_id":1}}' ;;
esac
"""
_STUB_CLAUDE = (
    "#!/usr/bin/env bash\n"
    'case "${1:-}" in plugin) echo "telegram@claude-plugins-official"; exit 0 ;; esac\n'
    "exit 0\n"
)


def test_the_launch_survives_an_unreachable_host(box):
    """The path that actually matters: a real launch with the network down.

    This started out driving ``--daemon-start`` and was **vacuous** — that flag
    sets ``ABS_DAEMON_START=1``, and the call site reads
    ``[ "${ABS_DAEMON_START:-0}" = "1" ] || update_prompt``, so the update check
    never ran and reverting the fix broke nothing here. Caught by mutation testing,
    which is the only reason it is now a plain launch: the failure Pranjal hit was
    `abs` typed at a terminal, and that is the path that has to be exercised.
    """
    from tests.conftest import write_profile

    stub = box.path / "bin"
    stub.mkdir()
    for name, body in (
        ("claude", _STUB_CLAUDE),
        ("bun", "#!/usr/bin/env bash\nexit 0\n"),
        ("curl", _STUB_CURL),
    ):
        p = stub / name
        p.write_text(body)
        p.chmod(0o755)

    launch_home = box.path / "lhome"
    launch_home.mkdir()
    abs_home = box.path / "labs"
    prof = write_profile(abs_home, "default", allow_ids=[42])
    rc = json.loads((prof / "rc.json").read_text())
    rc["no_start_menu"] = True  # nothing should stop to ask; there is no terminal
    (prof / "rc.json").write_text(json.dumps(rc))

    env = dict(os.environ)
    for k in list(env):
        if k.startswith("ABS_") or k.startswith("TELEGRAM_") or k == "CLAUDERC_HOME":
            env.pop(k, None)
    env.update(
        HOME=str(launch_home), ABS_HOME=str(abs_home),
        PATH=f"{stub}:{env.get('PATH', '')}",
        ABS_VERSION_URL=UNREACHABLE,
        ABS_REPO="http://127.0.0.1:1/never",
    )
    run = subprocess.run(
        ["bash", str(ABS_SH), "--profile", "default"],
        capture_output=True, text=True, env=env, timeout=120, cwd=str(box.path),
    )
    combined = run.stdout + run.stderr
    assert "Unexpected failure" not in combined, combined
    assert run.returncode == 0, combined
    assert (abs_home / "profiles" / "default" / "session.pid").exists(), combined


# ---- what it should still do -------------------------------------------------


def test_a_reachable_version_is_read_and_cached(box):
    url = box.serve("3.4.5\n")
    run = box.call('printf "[%s]" "$(_fetch_latest)"', url=url)
    assert run.returncode == 0, run.stderr
    assert "[3.4.5]" in run.stdout
    cached = json.loads((box.profile_dir / "update.json").read_text())
    assert cached["latest"] == "3.4.5"


def test_the_cache_answers_when_the_network_does_not(box):
    """An offline launch still knows what it last saw — the reason the cache
    exists, and worth pinning separately from the crash: a `return 0` bolted on
    to silence the error would satisfy the tests above and quietly lose this."""
    box.cache("9.9.9")
    run = box.call('printf "[%s]" "$(_latest_known)"')
    assert run.returncode == 0, run.stderr
    assert "[9.9.9]" in run.stdout


def test_a_junk_response_is_not_a_version(box):
    """A captive portal or a 404 page returns HTML with a 200. Treating that as a
    version string would offer an update to `<!DOCTYPE`."""
    url = box.serve("<!DOCTYPE html><title>Not found</title>")
    run = box.call('printf "[%s]" "$(_fetch_latest)"', url=url)
    assert run.returncode == 0, run.stderr
    assert "[]" in run.stdout


def test_an_older_remote_version_offers_nothing(box):
    url = box.serve("1.0.0\n")
    run = box.call('update_prompt; echo "rc=$?"', url=url)
    assert "rc=0" in run.stdout
    assert "update" not in run.stdout.lower().replace("update_prompt", "")


def test_update_check_off_never_touches_the_network(box):
    """Not just "no prompt" — no fetch. With the switch off, an unreachable host
    must not even cost the timeout."""
    rc = json.loads((box.profile_dir / "rc.json").read_text())
    rc["no_update_check"] = True
    (box.profile_dir / "rc.json").write_text(json.dumps(rc))
    run = box.call('time update_prompt; echo "rc=$?"')
    assert "rc=0" in run.stdout
    assert "Unexpected failure" not in run.stderr
    # A real fetch costs ~2s of connect timeout; skipping it is near-instant.
    assert "0m0.0" in run.stderr or "0m0.1" in run.stderr, run.stderr
