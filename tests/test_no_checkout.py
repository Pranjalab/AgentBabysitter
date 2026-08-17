"""What the v3 commands do on a single-file install (`curl … | bash`).

abs.sh alone is a complete v2: pairing, voice, reports, the kill ladder. Every v3
feature — daemon, sandboxes, the restricted assistant — needs the Python package
and its venv, which only exist in a git checkout. So on a curl install those
commands cannot work, and the only question is whether they fail *well*.

The bug this pins: `_restricted_py` used to `die` while being read as
``py="$(_restricted_py)"``. `exit` inside a command substitution ends the
subshell, so the parent carried on with an empty `$py`, ran the python invocation
with no interpreter, and the ERR trap printed a second "Unexpected failure (exit
1) at line N" underneath the real message. Two errors, the useless one last.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ABS_SH = os.path.join(REPO, "abs.sh")


@pytest.fixture
def lone_abs(tmp_path):
    """abs.sh copied somewhere with no `absd/` or `.venv/` beside it — exactly what
    the installer leaves behind when there is no checkout to link to."""
    binv = tmp_path / "bin"
    binv.mkdir()
    dst = binv / "abs"
    shutil.copy(ABS_SH, dst)
    dst.chmod(0o755)
    return dst


def run(lone_abs, tmp_path, *args):
    env = dict(os.environ, ABS_HOME=str(tmp_path / "abshome"))
    env.pop("TELEGRAM_STATE_DIR", None)
    return subprocess.run(
        [str(lone_abs), *args], capture_output=True, text=True, env=env
    )


def test_restricted_list_fails_once_and_cleanly(lone_abs, tmp_path):
    out = run(lone_abs, tmp_path, "restricted", "list")
    assert out.returncode != 0
    # The real message, once.
    assert "needs the v3 source" in out.stderr
    # NOT the ERR trap's follow-up, which is the actual regression.
    assert "Unexpected failure" not in out.stderr
    assert out.stderr.count("✗") == 1


@pytest.mark.parametrize(
    "args",
    [
        ("restricted", "list"),
        ("restricted", "start", "x"),
        ("restricted", "stop", "x"),
        ("restricted", "destroy", "x"),
        ("restricted", "nonsense"),
    ],
)
def test_no_restricted_subcommand_double_errors(lone_abs, tmp_path, args):
    """Whichever guard fires first — the missing checkout, an unknown profile, or a
    bad subcommand — exactly one error comes out. `start x` and friends check the
    profile name before the venv, which is the right order; what matters is that
    none of them reach the ERR trap."""
    out = run(lone_abs, tmp_path, *args)
    assert out.returncode != 0
    assert "Unexpected failure" not in out.stderr
    assert out.stderr.count("✗") == 1


def test_restricted_says_how_to_get_it(lone_abs, tmp_path):
    """A dead end with no way out is a bad error. Name the fix.

    Since 3.2.0 the fix is `abs src install`, not `git clone`. Telling someone to
    clone was a dead end of its own once the installer stopped offering it — the
    v3 source now arrives as a tarball in ~/.abs/src.
    """
    out = run(lone_abs, tmp_path, "restricted", "list")
    assert "abs src install" in out.stderr
    assert "git clone" not in out.stderr


def test_sandbox_also_fails_once(lone_abs, tmp_path):
    out = run(lone_abs, tmp_path, "sandbox", "list")
    assert out.returncode != 0
    assert "Unexpected failure" not in out.stderr


def test_v2_commands_still_work_without_a_checkout(lone_abs, tmp_path):
    """The point of the single-file install: everything v2 is unaffected."""
    out = run(lone_abs, tmp_path, "help")
    assert out.returncode == 0
    assert "Unexpected failure" not in out.stderr


def test_config_works_without_a_checkout(lone_abs, tmp_path):
    out = run(lone_abs, tmp_path, "config", "reply-voice")
    assert out.returncode == 0
    assert "Unexpected failure" not in out.stderr
