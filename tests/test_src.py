"""`abs src` — the v3 source without a git clone.

abs.sh alone is a complete v2. Everything v3 — the daemon, sandboxes, the start
menu's registry and recents — is the `absd` Python package plus a venv, and for
two releases the only way to have those was a git checkout with abs.sh living
inside it. So the installer asked "clone the repository?", and the operator's
verdict was that an installer should install, not interview.

The prompt is gone and this replaces it: the release tarball, unpacked into
~/.abs/src, with its own venv. Two behaviours are being pinned.

**A checkout always wins.** `abs_src_root` looks beside abs.sh first, exactly as
`voice_root` does, so working on the repo can never pick up a stale copy from
~/.abs/src — and `abs src install` refuses rather than building something that
would then never be read.

**Failing without it must name the fix.** Every v3 command on a bare install now
says `abs src install`. It used to say "needs the full checkout", which pointed
at a clone the installer had deliberately stopped offering.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ABS_SH = REPO / "abs.sh"


@pytest.fixture
def lone(tmp_path):
    """abs.sh copied where there is no absd/ beside it — a curl install."""
    binv = tmp_path / "bin"
    binv.mkdir()
    dst = binv / "abs"
    shutil.copy(ABS_SH, dst)
    dst.chmod(0o755)
    return dst


@pytest.fixture
def abs_home(tmp_path):
    h = tmp_path / "abshome"
    h.mkdir()
    return h


def run(script, abs_home, *args, **extra):
    env = dict(os.environ, ABS_HOME=str(abs_home))
    for k in ("TELEGRAM_STATE_DIR", "ABS_SESSION_PROFILE", "ABS_SRC_ROOT"):
        env.pop(k, None)
    env.update(extra)
    return subprocess.run([str(script), *args], capture_output=True, text=True, env=env)


# ---- where the source is looked for ------------------------------------------


def test_a_lone_abs_looks_under_abs_home(lone, abs_home):
    out = run(lone, abs_home, "src", "path")
    assert out.stdout.strip() == str(abs_home / "src")


def test_a_checkout_beside_abs_sh_wins(abs_home):
    """The dev case. Running the repo's own abs.sh must resolve to the repo, or a
    day's work would be shadowed by whatever happens to sit in ~/.abs/src."""
    out = run(ABS_SH, abs_home, "src", "path")
    assert out.stdout.strip() == str(REPO)


def test_the_root_can_be_pinned_for_a_test(lone, abs_home, tmp_path):
    pinned = tmp_path / "elsewhere"
    out = run(lone, abs_home, "src", "path", ABS_SRC_ROOT=str(pinned))
    assert out.stdout.strip() == str(pinned)


# ---- status ------------------------------------------------------------------


def test_status_on_a_bare_install_says_what_is_missing_and_how(lone, abs_home):
    out = run(lone, abs_home, "src", "status")
    assert out.returncode == 0
    assert "not installed" in out.stderr
    assert "abs src install" in out.stderr
    # And that the tool is not broken — this is the message that stops someone
    # concluding a curl install was a failed install.
    assert "complete v2" in out.stderr


def test_status_in_a_checkout_says_the_checkout_is_the_source(abs_home):
    out = run(ABS_SH, abs_home, "src", "status")
    assert out.returncode == 0
    assert str(REPO) in out.stderr


def test_status_reports_a_version_mismatch(lone, abs_home):
    """A source older than abs means the daemon is running last release's code
    against this release's state. Worth saying out loud."""
    src = abs_home / "src"
    (src / "absd").mkdir(parents=True)
    (src / ".venv" / "bin").mkdir(parents=True)
    py = src / ".venv" / "bin" / "python"
    py.write_text("#!/bin/sh\nexit 0\n")
    py.chmod(0o755)
    (src / "VERSION").write_text("0.0.1\n")
    out = run(lone, abs_home, "src", "status")
    assert "0.0.1" in out.stderr
    assert "--force" in out.stderr


# ---- installing --------------------------------------------------------------


def test_installing_into_a_checkout_is_refused_not_duplicated(abs_home):
    """Building ~/.abs/src from inside a checkout would produce something that is
    then never read — which is worse than doing nothing, because it looks like it
    worked."""
    out = run(ABS_SH, abs_home, "src", "install")
    assert out.returncode == 0
    assert "already has the v3 source" in out.stderr
    assert not (abs_home / "src").exists()


def test_a_bad_subcommand_is_refused(lone, abs_home):
    out = run(lone, abs_home, "src", "nonsense")
    assert out.returncode != 0
    assert "Usage: abs src" in out.stderr


# ---- the real thing, against a local tarball ---------------------------------
#
# `ABS_TARBALL_BASE` is pointed at a file:// URL holding a tarball built from
# this working tree, so the download and unpack paths run for real without
# reaching GitHub. The venv build still needs the network for pip, which is what
# the skip below is about.


def _online(host="pypi.org", port=443, timeout=3):
    try:
        socket.create_connection((host, port), timeout).close()
        return True
    except OSError:
        return False


needs_network = pytest.mark.skipif(not _online(), reason="pip needs the network")


@pytest.fixture
def local_tarball(tmp_path):
    """A GitHub-shaped tarball: everything under one top-level directory."""
    base = tmp_path / "tarballs"
    (base / "tags").mkdir(parents=True)
    (base / "heads").mkdir(parents=True)
    ver = next(l.split('"')[1] for l in open(ABS_SH) if l.startswith("readonly ABS_VERSION="))
    dst = base / "tags" / f"v{ver}.tar.gz"
    with tarfile.open(dst, "w:gz") as tf:
        for name in ("absd", "VERSION", "abs.sh", "assets"):
            p = REPO / name
            if p.exists():
                tf.add(p, arcname=f"AgentBabysitter-{ver}/{name}")
    return base


@needs_network
def test_installing_unpacks_the_tarball_and_builds_a_working_venv(lone, abs_home, local_tarball):
    out = run(lone, abs_home, "src", "install", ABS_TARBALL_BASE=f"file://{local_tarball}")
    assert out.returncode == 0, out.stderr
    src = abs_home / "src"
    assert (src / "absd" / "__init__.py").exists(), "the package did not land"
    assert (src / ".venv" / "bin" / "python").exists(), "no venv"
    # The claim the command makes when it says "installed": absd imports.
    proof = subprocess.run(
        [str(src / ".venv" / "bin" / "python"), "-c", "import absd, aiohttp"],
        capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(src)),
    )
    assert proof.returncode == 0, proof.stderr


@needs_network
def test_a_second_install_at_the_same_version_is_a_no_op(lone, abs_home, local_tarball):
    """It downloads tens of megabytes and builds a virtualenv. Doing that again
    because someone re-ran the installer is rude."""
    env = {"ABS_TARBALL_BASE": f"file://{local_tarball}"}
    run(lone, abs_home, "src", "install", **env)
    marker = abs_home / "src" / "touched-by-the-test"
    marker.write_text("")
    out = run(lone, abs_home, "src", "install", **env)
    assert out.returncode == 0
    assert "already at" in out.stderr
    assert marker.exists(), "it rebuilt when it should have left well alone"


@needs_network
def test_force_rebuilds(lone, abs_home, local_tarball):
    env = {"ABS_TARBALL_BASE": f"file://{local_tarball}"}
    run(lone, abs_home, "src", "install", **env)
    marker = abs_home / "src" / "touched-by-the-test"
    marker.write_text("")
    out = run(lone, abs_home, "src", "install", "--force", **env)
    assert out.returncode == 0, out.stderr
    assert not marker.exists(), "--force must replace the tree, not patch it"


def test_a_failed_download_leaves_the_existing_source_alone(lone, abs_home, tmp_path):
    """The staging directory exists for exactly this. A half-replaced source is
    worse than an old one, because the old one works."""
    src = abs_home / "src"
    (src / "absd").mkdir(parents=True)
    (src / ".venv" / "bin").mkdir(parents=True)
    py = src / ".venv" / "bin" / "python"
    py.write_text("#!/bin/sh\nexit 0\n")
    py.chmod(0o755)
    (src / "VERSION").write_text("0.0.1\n")
    keep = src / "still-here"
    keep.write_text("")

    empty = tmp_path / "nothing-here"
    empty.mkdir()
    out = run(lone, abs_home, "src", "install", ABS_TARBALL_BASE=f"file://{empty}")
    assert out.returncode != 0
    # Two wordings, because a pre-release refuses the fall back to main and says
    # so specifically. What this test is actually about is the line below.
    assert ("Could not download" in out.stderr
            or "will not fall back to main" in out.stderr), out.stderr
    assert keep.exists(), "the working source was destroyed by a failed update"


# ---- choosing a Python -------------------------------------------------------
#
# The operator's Mac had Python 3.14. It was the only interpreter tried, `venv`
# failed on it, and the whole install gave up with three usable interpreters
# sitting beside it. These pin both halves of the fix: the order, and the
# fall-through.


REAL_PYTHON = sys.executable   # absolute, so a shadowing stub cannot recurse


def _fake_python(path: Path, version: str, *, venv_fails=False, pip_fails=False):
    """A stub interpreter that answers -V and the version probe, and can be told
    to fail the way a real one does.

    `venv_fails` is the operator's Mac: `-m venv` returns non-zero.

    `pip_fails` is what a brand-new Python actually does — the venv builds and
    then there is no wheel to install. It cannot be faked on this stub, because
    abs runs pip with the VENV's python, not with the interpreter that made it.
    So the stub builds a venv whose `bin/python` is itself a failing stub, which
    reaches the same branch by the same route.

    Delegation is by ABSOLUTE path: these stubs go first on PATH precisely to
    shadow the real interpreters, so `env python3` here would find the stub again
    and spin.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    major, minor = version.split(".")
    if venv_fails:
        venv_branch = "exit 1"
    elif pip_fails:
        venv_branch = (
            'for a in "$@"; do target="$a"; done; '
            'mkdir -p "$target/bin" && '
            "printf '#!/bin/sh\\nexit 1\\n' > \"$target/bin/python\" && "
            'chmod 755 "$target/bin/python"'
        )
    else:
        venv_branch = f'exec {REAL_PYTHON} "$@"'
    path.write_text(f"""#!/bin/sh
case "$*" in
  *"version_info"*) echo "{int(major) * 100 + int(minor)}" ;;
  -V|--version)     echo "Python {version}" ;;
  *"-m venv"*)      {venv_branch} ;;
  *)                exec {REAL_PYTHON} "$@" ;;
esac
""")
    path.chmod(0o755)


# Every name `_src_pythons` looks for. Shadowing all of them is how a test says
# "this machine has nothing but what I put here" without emptying PATH, which
# would also take away bash, curl and tar.
PY_NAMES = ["python3.15", "python3.14", "python3.13", "python3.12",
            "python3.11", "python3", "python"]


def _pythons_seen(lone, abs_home, bindir):
    out = run(lone, abs_home, "src", "path", PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")
    assert out.returncode == 0
    body = "".join(l for l in open(ABS_SH) if l.strip() != 'main "$@"')
    script = abs_home.parent / "pys.sh"
    script.write_text(f"{body}\n_src_pythons\n")
    env = dict(os.environ, ABS_HOME=str(abs_home),
               PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")
    env.pop("ABS_SRC_ROOT", None)
    return subprocess.run(["bash", str(script)], capture_output=True, text=True,
                          env=env).stdout.split()


def test_a_brand_new_python_is_not_tried_first(lone, abs_home, tmp_path):
    """3.14 last, not first. The newest release is the WORST first choice: the
    wheels it needs may not exist yet, so it fails at pip rather than at venv and
    looks like an unrelated bug."""
    bindir = tmp_path / "pybin"
    _fake_python(bindir / "python3.14", "3.14")
    _fake_python(bindir / "python3.12", "3.12")
    seen = _pythons_seen(lone, abs_home, bindir)
    assert seen, seen
    assert seen.index("python3.12") < seen.index("python3.14"), seen


def test_an_interpreter_too_old_for_absd_is_not_offered(lone, abs_home, tmp_path):
    """3.11 is a floor, not a preference — absd uses 3.11 syntax and an older one
    dies at import with a SyntaxError, which is a terrible way to find out."""
    bindir = tmp_path / "pybin"
    _fake_python(bindir / "python3.11", "3.11")
    _fake_python(bindir / "python3", "3.9")
    seen = _pythons_seen(lone, abs_home, bindir)
    assert "python3.11" in seen
    assert "python3" not in seen, seen


def _only_these_pythons(tmp_path, broken_first=None, good=None, **kw):
    """A bin dir that shadows every interpreter name abs looks for.

    `broken_first` is the highest-preference name and is made to fail; `good` is
    the one that should be reached by falling through. Any name not named is
    removed from the machine's view entirely.
    """
    bindir = tmp_path / "pybin"
    bindir.mkdir(exist_ok=True)
    for name in PY_NAMES:
        p = bindir / name
        if name == broken_first:
            _fake_python(p, "3.13", **kw)
        elif name == good:
            _fake_python(p, "3.12")
        else:
            # Present but disqualified: reports a version absd cannot run on, so
            # `_src_pythons` skips it rather than it being missing by luck.
            _fake_python(p, "3.9")
    return bindir


@needs_network
def test_a_python_whose_venv_fails_falls_through_to_the_next(lone, abs_home, local_tarball, tmp_path):
    """The Mac failure exactly. It must not end the install."""
    bindir = _only_these_pythons(tmp_path, broken_first="python3.13",
                                 good="python3.12", venv_fails=True)
    out = run(lone, abs_home, "src", "install",
              ABS_TARBALL_BASE=f"file://{local_tarball}",
              PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")
    assert out.returncode == 0, out.stderr
    assert (abs_home / "src" / ".venv" / "bin" / "python").exists()
    assert "trying the next interpreter" in out.stderr


@needs_network
def test_a_python_with_no_wheel_for_it_falls_through_too(lone, abs_home, local_tarball, tmp_path):
    """The failure a brand-new Python actually produces: the venv builds fine and
    then pip cannot find a wheel for it."""
    bindir = _only_these_pythons(tmp_path, broken_first="python3.13",
                                 good="python3.12", pip_fails=True)
    out = run(lone, abs_home, "src", "install",
              ABS_TARBALL_BASE=f"file://{local_tarball}",
              PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")
    assert out.returncode == 0, out.stderr
    assert (abs_home / "src" / "absd" / "__init__.py").exists()
    assert "trying the next interpreter" in out.stderr


def test_when_nothing_works_it_shows_the_real_error(lone, abs_home, local_tarball, tmp_path):
    """The first version swallowed every error and printed a Debian package hint
    — on a Mac. An error naming the wrong operating system is worse than none,
    because it sends you looking somewhere that cannot be the answer."""
    bindir = _only_these_pythons(tmp_path, broken_first="python3.13",
                                 good=None, venv_fails=True)
    out = run(lone, abs_home, "src", "install",
              ABS_TARBALL_BASE=f"file://{local_tarball}",
              PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")
    assert out.returncode != 0
    assert "The last attempt said" in out.stderr
    assert "Nothing was changed" in out.stderr
    assert not (abs_home / "src").exists(), "a failed build left a source tree behind"
    # The hint has to match the machine it is printed on.
    import platform
    if platform.system() == "Darwin":
        assert "brew install" in out.stderr
    else:
        assert "apt install" in out.stderr


# ---- what the v3 commands say now --------------------------------------------


@pytest.mark.parametrize("args", [
    ("sandbox", "list"),
    ("project", "list"),
    ("sessions",),
    ("daemon", "install"),
    ("restricted", "list"),
])
def test_every_v3_command_names_the_one_command_that_fixes_it(lone, abs_home, args, tmp_path):
    # XDG_CONFIG_HOME is redirected because `daemon install` writes a systemd
    # user unit. It dies on the missing source long before it gets there — but a
    # test that would clobber the developer's real unit if that guard ever
    # regressed is not a test worth having.
    out = run(lone, abs_home, *args, XDG_CONFIG_HOME=str(tmp_path / "xdg"))
    assert out.returncode != 0
    assert "abs src install" in out.stderr, out.stderr
    assert "Unexpected failure" not in out.stderr, "the ERR trap fired on top"
    assert not (tmp_path / "xdg").exists(), "it got as far as writing a unit"


def test_daemon_status_stays_a_diagnostic_rather_than_an_error(lone, abs_home):
    """`status` answers a question; it must not refuse to answer it just because
    the source is missing. That is what `abs doctor` and `abs src status` are
    for, and both name the fix."""
    out = run(lone, abs_home, "daemon", "status")
    assert "Unexpected failure" not in out.stderr
