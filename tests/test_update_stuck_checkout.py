"""`abs update` on a git checkout that will not fast-forward.

Reported from a server on 1 Oct 2026. The launch offered the update, the user said
yes, and this came back:

    Updating the checkout at /home/pranjal/AgentBabysitter  (git pull --ff-only)…
    error: Your local changes to the following files would be overwritten by merge:
            abs.sh
    ! git pull --ff-only failed — the checkout has local changes or diverged.
      Resolve it by hand in /home/pranjal/AgentBabysitter, then relaunch.
    ! Continuing with 3.6.2.

Nothing there says WHAT changed, how much of it there is, or what to type. Over
SSH, on a box you are not sitting at, "resolve it by hand" is a dead end — and the
machine stayed on 3.6.2 for a fortnight because of it.

The fix does not make the update forceful. Local changes are somebody's work and
`--ff-only` refusing to rewrite history is correct. What changed is that the
failure now names its reason, prints the damage, and offers the one recovery that
cannot lose anything: `git stash`, which keeps the work and says how to get it
back. It is never run without an explicit yes, and never without a terminal.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ABS_SH = REPO / "abs.sh"


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


@pytest.fixture
def checkout(tmp_path):
    """An `abs` checkout with an upstream that has moved on — the shape every
    server install has."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git("init", "-q", "-b", "main", cwd=origin)
    (origin / "abs.sh").write_text('readonly ABS_VERSION="3.6.2"\n')
    git("add", "-A", cwd=origin)
    git("commit", "-qm", "v3.6.2", cwd=origin)

    work = tmp_path / "AgentBabysitter"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True,
                   capture_output=True)

    # upstream ships a new version
    (origin / "abs.sh").write_text('readonly ABS_VERSION="3.7.1"\n')
    git("commit", "-qam", "v3.7.1", cwd=origin)
    git("fetch", "-q", "origin", cwd=work)
    return work


def probe(snippet, cwd, tmp_path, stdin=None, tty=False):
    """Run one abs.sh function with main() stripped off."""
    src = ABS_SH.read_text().replace('main "$@"\n', "")
    script = tmp_path / "probe.sh"
    script.write_text(src + "\n" + snippet + "\n")
    cmd = ["bash", str(script)]
    if tty:  # a terminal on both 0 and 1, which the stash offer requires
        cmd = ["script", "-qec", f"bash {script}", "/dev/null"]
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          input=stdin, env=dict(os.environ, HOME=str(tmp_path),
                                                ABS_HOME=str(tmp_path / ".abs")))


# ---- what the operator actually saw ------------------------------------------------


def test_it_says_which_file_is_in_the_way(checkout, tmp_path):
    (checkout / "abs.sh").write_text('readonly ABS_VERSION="3.6.2"\n# edited here\n')
    r = probe(f'_update_git_stuck "{checkout}"', checkout, tmp_path)
    out = r.stdout + r.stderr
    assert "abs.sh" in out, out
    assert "could not be applied" in out.lower()


def test_it_hands_over_a_command_that_loses_nothing(checkout, tmp_path):
    (checkout / "abs.sh").write_text('readonly ABS_VERSION="3.6.2"\n# edited here\n')
    r = probe(f'_update_git_stuck "{checkout}"', checkout, tmp_path)
    out = r.stdout + r.stderr
    assert "stash" in out, "the way out has to be printed, not implied"
    assert "pull --ff-only" in out


def test_a_clean_but_diverged_branch_is_told_apart_from_local_edits(checkout, tmp_path):
    """--ff-only also refuses when the branch has its own commits. That is a
    different problem and needs a different answer — rebase, not stash."""
    (checkout / "mine.txt").write_text("local work\n")
    git("add", "-A", cwd=checkout)
    git("commit", "-qm", "my own commit", cwd=checkout)
    r = probe(f'_update_git_stuck "{checkout}"', checkout, tmp_path)
    out = r.stdout + r.stderr
    assert "rebase" in out, out
    assert "commit(s)" in out


def test_a_branch_with_no_upstream_says_so(checkout, tmp_path):
    git("checkout", "-qb", "detached-work", cwd=checkout)
    r = probe(f'_update_git_stuck "{checkout}"', checkout, tmp_path)
    out = r.stdout + r.stderr
    assert "not tracking a remote" in out
    assert "set-upstream-to" in out


# ---- the recovery itself -------------------------------------------------------------


def test_nothing_is_stashed_without_a_terminal(checkout, tmp_path):
    """A systemd or cron launch must never silently move somebody's work."""
    (checkout / "abs.sh").write_text('readonly ABS_VERSION="3.6.2"\n# edited here\n')
    probe(f'_update_git_stuck "{checkout}"', checkout, tmp_path)
    assert git("stash", "list", cwd=checkout).stdout.strip() == ""
    assert "# edited here" in (checkout / "abs.sh").read_text()


def test_answering_no_leaves_the_edit_exactly_where_it_was(checkout, tmp_path):
    (checkout / "abs.sh").write_text('readonly ABS_VERSION="3.6.2"\n# edited here\n')
    probe(f'_update_git_stuck "{checkout}"', checkout, tmp_path, stdin="n\n", tty=True)
    assert git("stash", "list", cwd=checkout).stdout.strip() == ""
    assert "# edited here" in (checkout / "abs.sh").read_text()
    assert '3.6.2' in (checkout / "abs.sh").read_text()


def test_answering_yes_updates_and_keeps_the_work(checkout, tmp_path):
    (checkout / "abs.sh").write_text('readonly ABS_VERSION="3.6.2"\n# edited here\n')
    r = probe(f'_update_git_stuck "{checkout}"', checkout, tmp_path, stdin="y\n", tty=True)
    out = r.stdout + r.stderr

    # the update landed
    assert '3.7.1' in (checkout / "abs.sh").read_text(), out
    # and the edit is recoverable, not gone
    assert git("stash", "list", cwd=checkout).stdout.strip() != "", "the work must be in the stash"
    assert "stash pop" in out, "and the operator must be told how to get it back"

    git("stash", "pop", cwd=checkout)
    assert "# edited here" in (checkout / "abs.sh").read_text()


def test_a_permission_only_difference_is_named_as_such(checkout, tmp_path):
    """Two machines with different umasks produce a dirty tree with no edits in
    it. Telling someone to stash that is useless; the fix is core.fileMode."""
    os.chmod(checkout / "abs.sh", 0o755)
    git("update-index", "--chmod=-x", "abs.sh", cwd=checkout)
    if not git("status", "--porcelain", cwd=checkout).stdout.strip():
        pytest.skip("this filesystem does not track the executable bit")
    r = probe(f'_update_git_stuck "{checkout}"', checkout, tmp_path)
    out = r.stdout + r.stderr
    assert "core.fileMode" in out, out
