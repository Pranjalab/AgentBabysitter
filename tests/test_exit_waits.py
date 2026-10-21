"""`abs exit` on a session that will not close.

Reported 1 Oct:

    The other Claude session had a shell and an agent running in the background.
    Because of this it was not closing properly and I was not able to get it
    back. I had to close everything forcefully.

The old `cmd_exit` sent SIGTERM, printed a tick, and deleted `session.pid` — all
without once checking whether the process had gone. Two things went wrong at
once, and the second is the one that made it unrecoverable:

  * it reported success for a session that was still running, and
  * it threw away the pid, so there was nothing left to run `abs exit` against.

What remained was reclaiming the bot or killing things by hand, which is exactly
what the operator ended up doing.

The fix does not make exiting more forceful. SIGKILL on a Claude Code session
mid-task has work to lose, so it stays behind an explicit `--force`. What changed
is that the command now waits, says only what is true, names the child processes
holding the session open, and *keeps* `session.pid` when it fails so the operator
can try again.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ABS_SH = REPO / "abs.sh"


@pytest.fixture
def box(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    abs_home = tmp_path / "abs"
    prof = abs_home / "profiles" / "default"
    prof.mkdir(parents=True)
    (prof / "rc.json").write_text('{"bot":"t","chat_id":42,"tg_dir":"%s"}' % (tmp_path / "tg"))
    (tmp_path / "tg").mkdir()

    class Box:
        def __init__(self):
            self.home, self.abs_home, self.dir = home, abs_home, prof
            self.procs = []

        def session(self, cmd: str):
            """Record a running process as this profile's session."""
            pr = subprocess.Popen(["bash", "-c", cmd],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.procs.append(pr)
            time.sleep(0.4)
            (prof / "session.pid").write_text(str(pr.pid))
            return pr

        def exit(self, *args, timeout=40):
            return subprocess.run(["bash", str(ABS_SH), "--profile", "default", "exit", *args],
                                  capture_output=True, text=True, timeout=timeout,
                                  env=dict(os.environ, HOME=str(home), ABS_HOME=str(abs_home)),
                                  cwd=str(home))

        def cleanup(self):
            for pr in self.procs:
                try:
                    pr.kill(); pr.wait(timeout=5)
                except Exception:
                    pass

    b = Box()
    yield b
    b.cleanup()


# ---- the ordinary case still works ----------------------------------------------------


def test_a_session_that_exits_is_reported_as_exited(box):
    # `exec` so the pid we record IS the sleep: bash defers a trap until the
    # running command finishes, so a wrapper with `trap ... TERM` around `sleep`
    # would not exit on TERM at all — which is how this test was wrong first time.
    box.session("exec sleep 60")
    r = box.exit()
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "has exited" in out, out
    assert not (box.dir / "session.pid").exists(), "a session that really went takes its pid with it"


# ---- the reported case ----------------------------------------------------------------


def test_a_session_that_ignores_term_is_not_called_a_success(box):
    """The heart of the report: it said it had worked when it had not."""
    box.session("trap '' TERM; sleep 60")
    r = box.exit()
    out = r.stdout + r.stderr
    assert r.returncode != 0, "a session still running is not a success:\n" + out
    assert "still running" in out, out


def test_the_pid_is_kept_so_it_can_be_tried_again(box):
    """Deleting session.pid while the session lived is what left him with no way
    back — `abs exit` had nothing to act on the second time."""
    box.session("trap '' TERM; sleep 60")
    box.exit()
    assert (box.dir / "session.pid").exists(), "losing the pid is what made this unrecoverable"
    # and a second attempt is therefore possible
    assert "still running" in (box.exit().stdout + box.exit().stderr)


def test_it_names_what_is_holding_the_session_open(box):
    """His was 'a shell and an agent running in the background'. Naming them is
    the difference between a dead end and something the operator can act on."""
    box.session("trap '' TERM; sleep 55 & sleep 60")
    r = box.exit()
    out = r.stdout + r.stderr
    assert "holding it open" in out, out
    assert "sleep" in out, "the child process must actually be listed:\n" + out


def test_it_offers_both_ways_out(box):
    box.session("trap '' TERM; sleep 60")
    out = box.exit().stdout + box.exit().stderr
    assert "--force" in out
    assert "--reclaim" in out


# ---- force, which is never automatic --------------------------------------------------


def test_force_ends_a_session_that_ignores_term(box):
    pr = box.session("trap '' TERM; sleep 60")
    r = box.exit("--force")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "killed" in out.lower(), out
    time.sleep(0.5)
    assert pr.poll() is not None or _reaped(pr.pid), "the process should be gone"
    assert not (box.dir / "session.pid").exists()


def test_sigkill_is_never_sent_without_force(box):
    """A Claude Code session mid-task has work to lose; escalating on its behalf
    is not ABS's call to make."""
    pr = box.session("trap '' TERM; sleep 60")
    box.exit()
    time.sleep(0.3)
    assert pr.poll() is None, "plain `abs exit` must not kill anything"


def _reaped(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return True
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split(") ", 1)[1].split()[0] == "Z"
    except OSError:
        return True


# ---- and the empty cases --------------------------------------------------------------


def test_a_dead_pid_is_cleaned_up_and_said_plainly(box):
    (box.dir / "session.pid").write_text("999999")
    r = box.exit()
    assert r.returncode != 0
    assert "is gone" in (r.stdout + r.stderr)
    assert not (box.dir / "session.pid").exists()
