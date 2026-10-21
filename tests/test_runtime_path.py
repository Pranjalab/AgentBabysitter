"""abs looking for its dependencies where they actually install themselves.

Reported 21 Oct, in the tab the installer had just finished in:

    pranjalbhaskare@Pranjals-MacBook-Air ~ % abs
    Missing required commands: bun
      bun    → curl -fsSL https://bun.sh/install | bash   (the plugin's MCP server runs on Bun)

Bun was installed. It had gone into ~/.bun, and Bun's installer — like Claude
Code's, and like the one in this repo — only adds that to your shell profile for
the NEXT login. So abs read PATH, believed it, and told the operator to install
something they already had.

`need_deps` widens PATH to those two directories before deciding. Appended, not
prepended: the job is to find what PATH has missed, never to outrank what it
already found.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

ABS_SH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "abs.sh")

TOOLS = ["sh", "bash", "cat", "printf", "echo", "uname", "grep", "sed"]

# Resolved from the real environment: the PATH these tests hand the shell holds
# only what the machine under test is meant to have, and bash is not part of
# what is being tested.
BASH = shutil.which("bash") or "/bin/bash"


def _fn(name: str) -> str:
    """One function, verbatim out of abs.sh."""
    src = open(ABS_SH).read()
    head = "\n%s() {\n" % name
    start = src.index(head) + 1
    return src[start:src.index("\n}\n", start) + len("\n}\n")]


def _run(tmp_path, *, bun: bool, claude: bool):
    """need_deps on a machine with curl and jq on PATH, and bun/claude only ever
    in the directories their own installers use."""
    home = tmp_path / "home"
    binsd = tmp_path / "bin"
    for d in (home, binsd):
        d.mkdir(parents=True, exist_ok=True)

    for tool in ("curl", "jq"):
        p = binsd / tool
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(0o755)
    for t in TOOLS:
        for root in ("/usr/bin", "/bin"):
            src = os.path.join(root, t)
            if os.path.exists(src) and not (binsd / t).exists():
                (binsd / t).symlink_to(src)

    if bun:
        d = home / ".bun" / "bin"
        d.mkdir(parents=True)
        (d / "bun").write_text("#!/bin/sh\nexit 0\n")
        (d / "bun").chmod(0o755)
    if claude:
        d = home / ".local" / "bin"
        d.mkdir(parents=True)
        (d / "claude").write_text("#!/bin/sh\nexit 0\n")
        (d / "claude").chmod(0o755)

    script = tmp_path / "drive.sh"
    script.write_text(
        "set -uo pipefail\n"
        "_pkg_hint() { printf 'install %s somehow' \"$1\"; }\n"
        + _fn("_widen_path")
        + _fn("need_deps")
        + "\nneed_deps\nprintf 'ALL PRESENT\\n'\n"
    )
    r = subprocess.run([BASH, str(script)], capture_output=True, text=True, timeout=60,
                       env={"PATH": str(binsd), "HOME": str(home)})
    return r.returncode, r.stdout + r.stderr


def test_bun_in_its_own_prefix_is_found(tmp_path):
    rc, out = _run(tmp_path, bun=True, claude=True)
    assert "Missing required commands" not in out, \
        "both are installed; abs said otherwise:\n" + out
    assert "ALL PRESENT" in out, out
    assert rc == 0, out


def test_bun_that_really_is_absent_is_still_reported(tmp_path):
    """The control. Widening PATH must not become a way of pretending something
    is installed — and the hint now also names the case where it IS installed and
    the shell simply has not caught up."""
    rc, out = _run(tmp_path, bun=False, claude=True)
    assert "Missing required commands: bun" in out, out
    assert "bun.sh/install" in out, out
    assert "open a new terminal" in out, \
        "the one thing that was actually wrong goes unmentioned:\n" + out
    assert rc == 1, out


@pytest.mark.parametrize("bun,claude,expect", [
    (False, False, "claude bun"),
    (True, False, "claude"),
    (False, True, "bun"),
])
def test_only_what_is_genuinely_missing_is_named(tmp_path, bun, claude, expect):
    _, out = _run(tmp_path, bun=bun, claude=claude)
    assert "Missing required commands: %s" % expect in out, out


def test_the_widening_appends_and_never_outranks_path(tmp_path):
    """A bun already on PATH must keep winning: this exists to find what PATH has
    missed, not to quietly swap one binary for another."""
    home = tmp_path / "home"
    binsd = tmp_path / "bin"
    (home / ".bun" / "bin").mkdir(parents=True)
    binsd.mkdir(parents=True)
    (home / ".bun" / "bin" / "bun").write_text("#!/bin/sh\necho HOME-ONE\n")
    (home / ".bun" / "bin" / "bun").chmod(0o755)
    (binsd / "bun").write_text("#!/bin/sh\necho PATH-ONE\n")
    (binsd / "bun").chmod(0o755)

    script = tmp_path / "drive.sh"
    script.write_text("set -uo pipefail\n" + _fn("_widen_path") + "\n_widen_path\nbun\n")
    r = subprocess.run([BASH, str(script)], capture_output=True, text=True, timeout=60,
                       env={"PATH": str(binsd), "HOME": str(home)})
    assert r.stdout.strip() == "PATH-ONE", \
        "the widening overtook something PATH had already found:\n" + r.stdout + r.stderr
