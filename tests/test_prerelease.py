"""A beta must be a known quantity, or it is not worth the tester's cycle.

`abs src install` fetches the v3 source from the tag matching `ABS_VERSION`, and
falls back to `main` when that tag does not exist — deliberately, because a version
can ship before anyone tags it and "no tag yet" must not mean "no daemon".

That trade is right for a release and wrong for a pre-release. Installing
`v3.6.0-beta.1`'s `abs.sh` beside main's `3.5.3` daemon produces a build nobody
wrote, and the person testing the beta spends the afternoon on a mixture. So the
fallback is switched off when the version carries a suffix, and the failure names
the missing tag rather than being quiet about a substitution.
"""

from __future__ import annotations

import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ABS_SH = os.path.join(REPO, "abs.sh")


def _lib(tmp_path, snippet, version=None):
    """abs.sh's functions with main() stripped, optionally at another version."""
    body = "".join(l for l in open(ABS_SH) if l.strip() != 'main "$@"')
    if version is not None:
        body = body.replace(
            next(l for l in body.splitlines() if l.startswith("readonly ABS_VERSION=")),
            f'readonly ABS_VERSION="{version}"',
            1,
        )
    f = tmp_path / "lib.sh"
    f.write_text(body + "\n" + snippet + "\n")
    return f


def run(script, **env_extra):
    env = dict(os.environ)
    for k in ("TELEGRAM_STATE_DIR", "ABS_SESSION_PROFILE"):
        env.pop(k, None)
    env.update(env_extra)
    return subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)


@pytest.mark.parametrize("version,expected", [
    ("3.6.0-beta.1", "yes"),
    ("3.6.0-rc.2", "yes"),
    ("3.5.3", "no"),
    ("3.6.0", "no"),
])
def test_a_suffix_is_the_whole_signal(tmp_path, version, expected):
    s = _lib(tmp_path, 'abs_is_prerelease && echo yes || echo no', version=version)
    assert run(s).stdout.strip() == expected


def test_a_prerelease_does_not_quietly_install_mains_source(tmp_path):
    """The point. With no tag reachable, a beta must come back empty-handed rather
    than with a different version's daemon."""
    base = tmp_path / "empty"
    (base / "tags").mkdir(parents=True)
    (base / "heads").mkdir(parents=True)
    # main IS reachable — that is what makes this a real test rather than a
    # network failure dressed up as a policy.
    (base / "heads" / "main.tar.gz").write_bytes(b"not really a tarball")

    s = _lib(tmp_path, 'printf "[%s]" "$(_src_fetch "${TMPDIR:-/tmp}/absbeta.$$.tgz" || echo FAILED)"',
             version="3.6.0-beta.1")
    out = run(s, ABS_TARBALL_BASE=f"file://{base}")
    assert out.stdout == "[FAILED]", out.stdout


def test_a_release_still_falls_back_to_main(tmp_path):
    """The behaviour that is being narrowed, not removed: a tagless release still
    gets a daemon."""
    base = tmp_path / "empty"
    (base / "tags").mkdir(parents=True)
    (base / "heads").mkdir(parents=True)
    (base / "heads" / "main.tar.gz").write_bytes(b"not really a tarball")

    s = _lib(tmp_path, 'printf "[%s]" "$(_src_fetch "${TMPDIR:-/tmp}/absbeta.$$.tgz" || echo FAILED)"',
             version="3.6.0")
    out = run(s, ABS_TARBALL_BASE=f"file://{base}")
    assert out.stdout == "[main]", out.stdout


def test_the_tag_is_preferred_over_main_either_way(tmp_path):
    base = tmp_path / "tagged"
    (base / "tags").mkdir(parents=True)
    (base / "heads").mkdir(parents=True)
    (base / "tags" / "v3.6.0-beta.1.tar.gz").write_bytes(b"tagged")
    (base / "heads" / "main.tar.gz").write_bytes(b"main")

    s = _lib(tmp_path, 'printf "[%s]" "$(_src_fetch "${TMPDIR:-/tmp}/absbeta.$$.tgz" || echo FAILED)"',
             version="3.6.0-beta.1")
    out = run(s, ABS_TARBALL_BASE=f"file://{base}")
    assert out.stdout == "[v3.6.0-beta.1]", out.stdout


# ---- the update check must leave a beta tester alone --------------------------


def test_a_stable_release_does_not_nag_someone_on_a_newer_beta(tmp_path):
    """`version_gt` truncates each component at its first non-digit, so
    3.6.0-beta.1 compares as 3.6.0. A beta tester must not be told that 3.5.3 is
    an upgrade — and must still be told when a genuinely newer release lands."""
    s = _lib(tmp_path, r'''
      version_gt "3.5.3" "$ABS_VERSION" && echo "nagged" || echo "quiet"
      version_gt "3.7.0" "$ABS_VERSION" && echo "offered" || echo "missed"
    ''', version="3.6.0-beta.1")
    assert run(s).stdout.split() == ["quiet", "offered"]


# ---- and the version is the same everywhere ----------------------------------


def test_every_file_that_carries_the_version_agrees():
    """Three copies, one truth. A beta installed from a tag reads its version out
    of `abs.sh`; `abs src status` compares it against the source tree's `VERSION`
    and warns on a mismatch; absd reports its own."""
    sh = next(l.split('"')[1] for l in open(ABS_SH)
              if l.startswith("readonly ABS_VERSION="))
    with open(os.path.join(REPO, "VERSION")) as f:
        version_file = f.read().strip()
    py = next(l.split('"')[1] for l in open(os.path.join(REPO, "absd", "__init__.py"))
              if l.startswith("__version__ = "))
    assert sh == version_file == py, f"abs.sh={sh} VERSION={version_file} absd={py}"
