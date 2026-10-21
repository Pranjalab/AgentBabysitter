"""Installing on a machine that has nothing yet.

Reported 21 Oct, from a new Mac:

    I am installing the ABS on my new Mac and Python was not installed and
    Claude Code was not installed so it needed to do all this. Can you think of
    a proper method so that ABS can install properly with all the dependencies?

The installer met those one at a time: offer Bun, offer Claude Code, then stop
dead at jq with "sudo apt install jq (or: brew install jq)". On a fresh Mac that
is wrong twice — apt does not exist, and neither does brew until you install it.
Python was never checked at all, so it surfaced later still, when `abs src
install` wanted 3.11+ for the daemon. Three stops, three re-runs, and advice
written for somebody else's operating system.

It is one pass now: look at the machine, print everything missing in a single
list with the command for THIS platform, ask once, install all of it, re-verify.

These tests stand the installer on platforms this machine is not, with a fake
`uname` and a PATH holding only the basics. Two of them go further and let it
actually install, against a `brew` that produces what it claims to.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INSTALL = REPO / "install.sh"
ABS_SH = REPO / "abs.sh"

# The basics the installer needs before it can get as far as the scan.
TOOLS = ["bash", "sh", "cat", "sed", "grep", "awk", "mktemp", "mv", "cp", "chmod",
         "ln", "tr", "head", "tail", "cut", "curl", "rm", "dirname", "basename",
         "id", "install", "readlink", "expr", "mkdir", "ls", "env"]

UNAME = '#!/bin/sh\ncase "$1" in -s) echo %s ;; -m) echo %s ;; *) echo %s ;; esac\n'
STUB = '#!/bin/sh\ncase "$1" in *version*) echo "9.9.9" ;; esac\nexit 0\n'

# A brew that writes the binaries a real one would, so the install path can run
# to the end without a package manager or the network.
FAKE_BREW = """#!/bin/sh
d="$(dirname "$0")"
case "$1" in
  --version) echo "Homebrew 4.3.0"; exit 0 ;;
  shellenv)  echo "export PATH=\\"$d:\\$PATH\\""; exit 0 ;;
  install)   shift
             for p in "$@"; do
               case "$p" in
                 jq)        printf '#!/bin/sh\\ncase "$1" in --version) echo "jq-1.7.1" ;; esac\\nexit 0\\n' > "$d/jq"; chmod +x "$d/jq" ;;
                 python@*|python3)
                            printf '#!/bin/sh\\necho 3.13\\nexit 0\\n' > "$d/python3"; chmod +x "$d/python3" ;;
               esac
             done
             exit 0 ;;
esac
exit 0
"""

# The same brew, but it reads stdin — which is what the real one did on the
# reported Mac, and the entire bug in three characters.
FAKE_BREW_THIRSTY = FAKE_BREW.replace(
    "  install)   shift\n",
    "  install)   shift\n             cat > /dev/null\n")

# Stands in for Homebrew's own installer, reproducing the two ways it refuses.
# NONINTERACTIVE makes the real one use `sudo -n` — sudo forbidden to prompt —
# so on a Mac whose password is not cached it aborts with the exact line the
# operator saw. Without it, it still needs a terminal to ask for RETURN.
FAKE_BREW_INSTALLER = """#!/bin/bash
if [ -n "${NONINTERACTIVE-}" ]; then
  echo "==> Running in non-interactive mode because \\$NONINTERACTIVE is set."
  echo "==> Checking for sudo access..."
  echo "Insufficient permissions to install Homebrew to \\"/opt/homebrew\\"."
  exit 1
fi
if [ ! -t 0 ]; then
  echo "==> No terminal to ask for RETURN on."
  exit 1
fi
echo "==> FAKE HOMEBREW: a terminal, and sudo free to prompt."
cp "$FAKE_BREW_PATH" "$FAKE_BIN/brew"
chmod +x "$FAKE_BIN/brew"
exit 0
"""

# A curl that serves only that installer. Anything else is a miss, loudly.
FAKE_CURL = """#!/bin/sh
out=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -*) shift ;;
    *)  url="$1"; shift ;;
  esac
done
case "$url" in
  *Homebrew/install*) cp "$FAKE_BREW_INSTALLER_PATH" "$out"; chmod +x "$out"; exit 0 ;;
  *abs.sh)            cp "$FAKE_ABS_SH" "$out"; exit 0 ;;
esac
echo "fake curl asked for an unexpected url: $url" >&2
exit 1
"""


def machine(tmp_path, *, osname: str, arch: str = "arm64", have=(), brew=False) -> Path:
    """A PATH with only the basics, a fake `uname`, and whatever `have` says is
    already installed. `brew` swaps in the one that really installs things."""
    d = tmp_path / ("bin-" + osname + "-" + "-".join(have or ("none",)) + ("-fb" if brew else ""))
    d.mkdir(parents=True, exist_ok=True)
    (d / "uname").write_text(UNAME % (osname, arch, osname))
    (d / "uname").chmod(0o755)
    for t in TOOLS:
        src = _which(t)
        if src and not (d / t).exists():
            (d / t).symlink_to(src)
    for t in have:
        p = d / t
        p.write_text(FAKE_BREW if (brew and t == "brew") else STUB)
        p.chmod(0o755)
    (d / "home").mkdir(exist_ok=True)
    return d


def _which(t):
    for root in ("/usr/bin", "/bin", "/usr/local/bin", "/usr/sbin"):
        c = Path(root) / t
        if c.exists():
            return str(c)
    return None


def run(d: Path, *args, env=None) -> str:
    """Run the installer against that machine. No tty, so the consent prompt
    cannot be answered — which is itself one of the paths worth testing."""
    e = {"PATH": str(d), "HOME": str(d / "home"), "SHELL": "/bin/zsh", "TERM": "dumb",
         "PREFIX": str(d / "home" / ".local" / "bin"), "ABS_HOME": str(d / "home" / ".abs")}
    e.update(env or {})
    r = subprocess.run(["bash", str(INSTALL), *args], capture_output=True, text=True,
                       timeout=120, env=e)
    return re.sub(r"\x1b\[[0-9;]*m", "", r.stdout + r.stderr)


# ---- the reported machine -------------------------------------------------------------


def test_everything_missing_is_reported_in_one_pass(tmp_path):
    """One stop, not three. This is the whole point of the change."""
    out = run(machine(tmp_path, osname="Darwin"))
    scan = out.split("What needs installing")[0]
    for dep in ("jq", "python", "bun", "claude"):
        assert dep in scan, f"{dep} missing from the scan:\n{out}"
    # and it must not have begun installing anything before saying all of it
    assert out.count("What needs installing") == 1, out
    assert "Installing\n" not in out, "it started installing without consent:\n" + out


def test_a_bare_mac_is_told_about_homebrew_first(tmp_path):
    """`brew install jq` is not advice you can follow on a Mac with no brew."""
    out = run(machine(tmp_path, osname="Darwin"))
    assert "Homebrew" in out, out
    assert "brew.sh" in out or "Homebrew/install" in out, out
    assert out.index("Homebrew") < out.index("brew install jq"), out


def test_python_is_checked_before_install_not_after(tmp_path):
    """It used to surface only at `abs src install`, long after this script had
    finished and said it was done. Now it is in the scan AND in the plan, with a
    command — on a Mac, the brew one."""
    out = run(machine(tmp_path, osname="Darwin", have=("brew",)))
    scan, _, plan = out.partition("What needs installing")
    assert re.search(r"^\s+\S\s+python\s", scan, re.M), "not in the scan:\n" + out
    assert "brew install jq python@3." in plan, "no command to run:\n" + out
    assert "3.11" in scan, "the version it needs is never stated:\n" + out


def test_a_mac_that_has_brew_is_not_told_to_install_it(tmp_path):
    out = run(machine(tmp_path, osname="Darwin", have=("brew",)))
    assert "brew install jq" in out, out
    assert "macOS has no package manager" not in out, out


def test_nothing_is_installed_before_the_plan_is_shown(tmp_path):
    out = run(machine(tmp_path, osname="Darwin", have=("brew",)))
    assert "Nothing below is run until you say so" in out, out


def test_no_tty_and_no_yes_installs_nothing(tmp_path):
    """Piped to bash in CI there is nobody to consent, and a surprise install is
    the one thing a script fetched over the network must not do."""
    out = run(machine(tmp_path, osname="Darwin", have=("brew",)))
    assert "Stopping rather than installing software you did not agree to" in out, out
    assert "fake brew installing" not in out


def test_no_deps_reports_and_stops(tmp_path):
    out = run(machine(tmp_path, osname="Darwin", have=("brew",)), "--no-deps")
    assert "brew install jq" in out, out
    assert "nothing installed" in out.lower(), out


# ---- and then it actually installs ----------------------------------------------------


def test_yes_installs_the_missing_packages_and_verifies_them(tmp_path):
    """The branch the dry runs never reach: consent, install, re-probe."""
    d = machine(tmp_path, osname="Darwin", have=("brew", "bun", "claude"), brew=True)
    out = run(d, "--yes")
    assert "jq 1.7.1" in out, "the installed jq was never confirmed:\n" + out
    assert "python 3.13" in out, "the installed python was never confirmed:\n" + out
    assert "All dependencies present" in out, out
    assert (d / "home" / ".local" / "bin" / "abs").exists(), "abs never got installed:\n" + out


def test_yes_does_not_also_say_yes_to_the_optional_extras(tmp_path):
    """`--yes` means "install the dependencies you found". If it also answered the
    voice offer, an unattended install would start a multi-gigabyte model build."""
    d = machine(tmp_path, osname="Darwin", have=("brew", "bun", "claude"), brew=True)
    out = run(d, "--yes")
    assert "Setting up voice" not in out and "voice setup" not in out.split("Optional")[0], out
    # the offer may be printed; what must not happen is it being taken
    assert "Downloading" not in out or "Kokoro" not in out, out


def test_homebrew_is_given_the_terminal_so_sudo_can_ask(tmp_path):
    """Reported 21 Oct from the first real Mac to run this:

        ==> Checking for sudo access...
        Insufficient permissions to install Homebrew to "/opt/homebrew".

    The installer set NONINTERACTIVE, which makes Homebrew's own installer use
    `sudo -n` — sudo that is forbidden to prompt. On a Mac whose password is not
    already cached that aborts immediately, having never asked for it. We are
    piped from curl so our stdin is the script, but the terminal is still on
    /dev/tty; handing it over is what makes sudo work as it does everywhere else.

    The stand-in installer here refuses exactly as the real one did, so this test
    fails if the terminal is ever taken away again.
    """
    if not _which("script"):
        pytest.skip("needs util-linux `script` to give the installer a real tty")
    d = machine(tmp_path, osname="Darwin", have=("jq", "bun", "claude"))
    (d / "brewtemplate").write_text(FAKE_BREW)
    (d / "brewinstaller").write_text(FAKE_BREW_INSTALLER)
    # `curl` in here is a symlink to the real one; writing would follow it
    # straight to /usr/bin. Drop it first, then put ours in its place.
    (d / "curl").unlink()
    (d / "curl").write_text(FAKE_CURL)
    (d / "curl").chmod(0o755)

    env = {"PATH": str(d), "HOME": str(d / "home"), "SHELL": "/bin/zsh", "TERM": "dumb",
           "PREFIX": str(d / "home" / ".local" / "bin"),
           "ABS_HOME": str(d / "home" / ".abs"),
           "FAKE_BIN": str(d), "FAKE_BREW_PATH": str(d / "brewtemplate"),
           "FAKE_BREW_INSTALLER_PATH": str(d / "brewinstaller"),
           "FAKE_ABS_SH": str(ABS_SH)}
    envs = " ".join("%s=%s" % (k, v) for k, v in env.items())
    # The installer must be PIPED, exactly as `curl … | bash` pipes it: its own
    # stdin is then the script, not the terminal, and only /dev/tty is left. Run
    # it with a tty on stdin instead and the bug cannot reproduce — the first
    # version of this test made that mistake and passed against the broken code.
    r = subprocess.run(
        ["script", "-qec", "cat %s | env -i %s bash" % (INSTALL, envs), "/dev/null"],
        input="y\n\n", capture_output=True, text=True, timeout=180)
    out = re.sub(r"\x1b\[[0-9;]*m", "", r.stdout + r.stderr)

    assert "Insufficient permissions" not in out, \
        "Homebrew was run without a terminal again — this is the reported bug:\n" + out
    assert "FAKE HOMEBREW" in out, "the installer never reached Homebrew:\n" + out
    assert "Homebrew 4.3.0" in out, "it did not confirm the brew it installed:\n" + out
    # and the package it then asks brew for must be a formula, not an alias
    assert "brew install python@3." in out, \
        "a bare Mac must be offered the python formula, not the `python3` alias:\n" + out
    assert (d / "home" / ".local" / "bin" / "abs").exists(), "abs never got installed:\n" + out


def test_the_piped_form_installs_a_real_copy(tmp_path):
    """`curl … | bash` is the install everyone actually uses, and it is the one
    where BASH_SOURCE is an empty array — which under `set -u` once collapsed to
    `cd ""`, leaving the installer in the CALLER's directory where a stray abs.sh
    would be picked up instead of the real one. ABS_REPO over file:// keeps the
    network out of it."""
    d = machine(tmp_path, osname="Darwin", have=("brew", "bun", "claude", "jq", "tar"))
    (d / "python3").write_text("#!/bin/sh\necho 3.12\nexit 0\n")
    (d / "python3").chmod(0o755)
    (d / "jq").write_text('#!/bin/sh\ncase "$1" in --version) echo "1.7.1" ;; esac\nexit 0\n')
    (d / "jq").chmod(0o755)
    prefix = d / "home" / ".local" / "bin"
    r = subprocess.run(["bash"], input=INSTALL.read_text(), capture_output=True, text=True,
                       timeout=180,
                       env={"PATH": str(d), "HOME": str(d / "home"), "SHELL": "/bin/zsh",
                            "TERM": "dumb", "PREFIX": str(prefix),
                            "ABS_HOME": str(d / "home" / ".abs"),
                            "ABS_REPO": "file://" + str(REPO)})
    out = re.sub(r"\x1b\[[0-9;]*m", "", r.stdout + r.stderr)
    abs_cmd = prefix / "abs"
    assert abs_cmd.is_file() and not abs_cmd.is_symlink(), \
        "the piped install must leave a real copy, not a link:\n" + out
    assert "readonly ABS_VERSION=" in abs_cmd.read_text(), "that is not abs.sh:\n" + out
    assert subprocess.run(["bash", "-n", str(abs_cmd)]).returncode == 0, "it does not parse"
    # and the v3 source failing over a fake python must not take the install down
    assert "Ready" in out, out
    assert "brew install python@3.12" in out, \
        "the runtime hint gave a Mac the wrong platform's command:\n" + out



def test_a_package_manager_that_reads_stdin_does_not_eat_the_script(tmp_path):
    """Reported 21 Oct, from the Mac that had just been fixed. Every dependency
    installed, "All dependencies present", and then the shell prompt — no abs,
    no PATH advice, nothing. Earlier in the same run, lines of this very file had
    been echoed into the terminal in the middle of the brew install.

    Piped from curl the script IS stdin, and bash reads a non-seekable script as
    it goes, so a child that reads stdin eats the part bash has not parsed yet.
    `brew install` did exactly that: bash reached end-of-file right after the
    dependency summary and the whole "Installing abs" step never ran. It looked
    like a success, which is the worst kind of failure.

    The cure is that the body lives in a function and the call is the last line,
    so bash has read all of it before the first child starts. This brew drinks
    stdin the way the real one did, so this test fails if that ever unwinds.
    """
    d = machine(tmp_path, osname="Darwin", have=("brew", "bun", "claude"))
    (d / "brew").write_text(FAKE_BREW_THIRSTY)
    (d / "brew").chmod(0o755)
    prefix = d / "home" / ".local" / "bin"
    r = subprocess.run(
        ["bash", "-s", "--", "--yes"], input=INSTALL.read_text(),
        capture_output=True, text=True, timeout=180,
        env={"PATH": str(d), "HOME": str(d / "home"), "SHELL": "/bin/zsh",
             "TERM": "dumb", "PREFIX": str(prefix),
             "ABS_HOME": str(d / "home" / ".abs"),
             "ABS_REPO": "file://" + str(REPO)})
    out = re.sub(r"\x1b\[[0-9;]*m", "", r.stdout + r.stderr)
    assert "All dependencies present" in out, out
    assert (prefix / "abs").is_file(), \
        "the script was eaten before abs could be installed:\n" + out
    assert "Ready" in out, "the run stopped early:\n" + out
    # and no part of this file may ever be dumped into the operator's terminal
    assert "bun_fresh=" not in out, \
        "the rest of the script went to the terminal as output:\n" + out


def test_the_voice_offer_names_ffmpeg_when_it_is_missing(tmp_path):
    """`abs voice setup` refuses without ffmpeg and will not install a package
    itself, while this offer defaults to yes — so on a brand-new Mac, pressing
    RETURN at the last question was a guaranteed dead end. Name it in the offer,
    and install it as part of the yes."""
    d = machine(tmp_path, osname="Darwin", have=("brew", "bun", "claude", "jq", "tar"))
    fake_py = d / "python3"
    fake_py.write_text("#!/bin/sh\necho 3.12\nexit 0\n")
    fake_py.chmod(0o755)
    # the voice step is only offered once the v3 tree exists somewhere
    (d / "home" / ".abs" / "src" / "absd").mkdir(parents=True)
    r = subprocess.run(
        ["bash"], input=INSTALL.read_text(), capture_output=True, text=True, timeout=180,
        env={"PATH": str(d), "HOME": str(d / "home"), "SHELL": "/bin/zsh", "TERM": "dumb",
             "PREFIX": str(d / "home" / ".local" / "bin"),
             "ABS_HOME": str(d / "home" / ".abs"),
             "ABS_REPO": "file://" + str(REPO)})
    out = re.sub(r"\x1b\[[0-9;]*m", "", r.stdout + r.stderr)
    assert "Optional — voice" in out, "the voice step was never reached:\n" + out
    voice = out.split("Optional — voice")[-1]
    assert "ffmpeg" in voice, "the offer never mentions what it needs:\n" + voice
    assert "brew install ffmpeg" in voice, \
        "a Mac must be given the brew command, not somebody else's:\n" + voice


def _mac_with_brew_off_the_path(tmp_path):
    """A Mac exactly as the operator's was: Homebrew installed and working, in a
    prefix that is not on this shell's PATH because the shell has not logged in
    since. python@3.13 lives in that prefix too, so it is invisible until
    `brew shellenv` has been eval'd."""
    d = machine(tmp_path, osname="Darwin", have=("jq", "bun", "claude"))
    prefix = tmp_path / "opt-homebrew-bin"
    prefix.mkdir(exist_ok=True)
    (prefix / "brew").write_text(FAKE_BREW)
    (prefix / "brew").chmod(0o755)
    (prefix / "python3.13").write_text("#!/bin/sh\necho 3.13\nexit 0\n")
    (prefix / "python3.13").chmod(0o755)
    return d, prefix


def test_a_mac_with_homebrew_off_the_path_is_not_told_it_has_none(tmp_path):
    """Reported 21 Oct, the run after Homebrew and python@3.13 both installed
    perfectly:

        !  No package manager found on this machine, and abs needs: python3
        ✗  Stopping — guessing a package manager would waste your time twice.

    Both statements were false. Homebrew's installer only sets up PATH for the
    next login shell, so in the tab that had just installed it /opt/homebrew/bin
    was missing — and two separate things trusted PATH too early. The dependency
    scan probed python before anything had looked in the prefix, so a working
    3.13 read as missing; and PKG was decided by `command -v brew` alone, which
    never looks in the prefix at all. Homebrew goes on PATH first now, before PKG
    is decided and before a single dependency is probed."""
    d, prefix = _mac_with_brew_off_the_path(tmp_path)
    out = run(d, env={"ABS_BREW_PREFIXES": str(prefix / "brew")})
    assert "No package manager found" not in out, \
        "it has Homebrew, in the prefix Homebrew uses:\n" + out
    assert "needs: python3" not in out, \
        "python@3.13 is installed and was called missing:\n" + out
    assert "Everything abs needs is already here" in out, \
        "nothing was missing on this machine:\n" + out


def test_a_mac_with_no_homebrew_anywhere_still_says_so(tmp_path):
    """The control. If putting the prefix on PATH ever becomes a way of pretending
    a package manager exists, this is the test that notices."""
    d, _ = _mac_with_brew_off_the_path(tmp_path)
    out = run(d, env={"ABS_BREW_PREFIXES": str(tmp_path / "nowhere" / "brew")})
    assert "homebrew" in out, "a Mac with no brew must be offered it:\n" + out
    assert "Homebrew/install" in out, out


def test_bun_and_claude_in_their_own_prefixes_are_not_installed_twice(tmp_path):
    """Both install into $HOME and edit your rc for the NEXT login, so in the tab
    that just installed them they are invisible. Reported 21 Oct: a second run in
    the same tab re-offered everything that had already succeeded. Probing where
    they actually live is what makes a re-run cheap — the whole point of the
    one-pass rewrite."""
    d = machine(tmp_path, osname="Darwin", have=("brew", "jq"))
    fake_py = d / "python3"
    fake_py.write_text("#!/bin/sh\necho 3.12\nexit 0\n")
    fake_py.chmod(0o755)
    for sub, tool in (("home/.bun/bin", "bun"), ("home/.local/bin", "claude")):
        where = d / sub
        where.mkdir(parents=True, exist_ok=True)
        (where / tool).write_text(STUB)
        (where / tool).chmod(0o755)

    out = run(d)
    assert "Everything abs needs is already here" in out, \
        "bun and claude are installed; neither was found:\n" + out
    assert "bun.sh/install" not in out, "it offered to install Bun again:\n" + out
    assert "claude.ai/install" not in out, \
        "it offered to install Claude Code again:\n" + out

    # ...and finding them there must NOT make the PATH advice think the operator's
    # own shell can see ~/.local/bin, which is exactly what it cannot do.
    zshrc = d / "home" / ".zshrc"
    assert zshrc.exists(), "no profile was written:\n" + out
    assert str(d / "home" / ".local" / "bin") in zshrc.read_text(), \
        "the profile has no PATH line:\n" + zshrc.read_text()
    assert "source ~/.zshrc" in out, \
        "the Next list never says how to pick it up:\n" + out


def _mac_with_everything(tmp_path, **kw):
    d = machine(tmp_path, osname="Darwin",
                have=("brew", "bun", "claude", "jq", "tar"), **kw)
    fake_py = d / "python3"
    fake_py.write_text("#!/bin/sh\necho 3.12\nexit 0\n")
    fake_py.chmod(0o755)
    return d


def test_a_prefix_already_on_path_still_says_how_to_reach_abs(tmp_path):
    """Reported 21 Oct: `zsh: command not found: abs` on a machine whose PATH was
    already correct. Nothing needed writing, so the run said nothing — but zsh
    lists every command on PATH when the shell starts, and abs was created after
    that, so the name is unknown until it looks again. The one case the Next list
    was silent about was the one case that needed a word."""
    d = _mac_with_everything(tmp_path)
    out = run(d, env={"PREFIX": str(d)})          # PREFIX *is* the PATH dir
    assert "is not on your PATH" not in out, "it is on PATH:\n" + out
    assert "rehash" in out, "nothing told them how to reach abs:\n" + out


def test_an_abs_that_was_already_there_is_not_made_a_fuss_of(tmp_path):
    """The control. A re-run on a working machine must not keep handing out
    shell commands for a problem that cannot exist — the shell has known the
    name since before this run started."""
    d = _mac_with_everything(tmp_path)
    (d / "abs").write_text('#!/usr/bin/env bash\nreadonly ABS_VERSION="0.0.1"\n')
    (d / "abs").chmod(0o755)
    out = run(d, env={"PREFIX": str(d)})
    assert "rehash" not in out, "abs was already on PATH and already known:\n" + out
    assert "hash -r" not in out, out


def test_a_non_zsh_shell_is_given_its_own_word_for_it(tmp_path):
    d = _mac_with_everything(tmp_path)
    out = run(d, env={"PREFIX": str(d), "SHELL": "/bin/bash"})
    assert "hash -r" in out, out
    assert "rehash" not in out, "that is the zsh builtin, not bash's:\n" + out

def test_the_steps_are_numbered_without_gaps(tmp_path):
    """Half these sections are skipped on a machine that is already set up, and a
    run that reads "1 · … 4 · … 6 · …" looks like something went wrong."""
    d = machine(tmp_path, osname="Darwin", have=("brew", "bun", "claude"), brew=True)
    nums = [int(m) for m in re.findall(r"^  (\d+) · ", run(d, "--yes"), re.M)]
    assert nums, "no steps printed at all"
    assert nums == list(range(1, len(nums) + 1)), f"gaps in the step numbers: {nums}"


# ---- the platforms it is not -----------------------------------------------------------


def test_a_linux_box_is_not_told_to_use_brew(tmp_path):
    out = run(machine(tmp_path, osname="Linux", arch="x86_64", have=("apt-get",)))
    assert "brew" not in out.lower(), "wrong operating system's advice:\n" + out
    assert "apt-get install -y jq" in out, out


def test_an_unknown_platform_admits_it_rather_than_guessing(tmp_path):
    out = run(machine(tmp_path, osname="FreeBSD"))
    assert "No package manager found" in out, out
    for wrong in ("sudo apt", "brew install"):
        assert wrong not in out, f"invented {wrong!r} for an unknown platform:\n{out}"


@pytest.mark.parametrize("mgr,expect", [
    ("apt-get", "sudo apt-get install -y jq"),
    ("dnf",     "sudo dnf install -y jq"),
    ("pacman",  "sudo pacman -S --needed --noconfirm jq"),
    ("apk",     "sudo apk add jq"),
    ("zypper",  "sudo zypper install -y jq"),
])
def test_each_linux_package_manager_gets_its_own_command(tmp_path, mgr, expect):
    out = run(machine(tmp_path, osname="Linux", arch="x86_64", have=(mgr,)))
    assert expect in out, f"{mgr} → \n{out}"


# ---- and the same at runtime -----------------------------------------------------------


@pytest.mark.parametrize("osname,have,expect,forbid", [
    ("Darwin", (),          "Homebrew",            "sudo apt"),
    ("Darwin", ("brew",),   "brew install jq",     "sudo apt"),
    ("Linux",  ("apt",),    "sudo apt install jq", "brew"),
    ("Linux",  ("dnf",),    "sudo dnf install jq", "apt"),
    ("Linux",  ("pacman",), "sudo pacman -S jq",   "apt"),
])
def test_pkg_hint_names_the_right_command(tmp_path, osname, have, expect, forbid):
    """`abs` told a Mac user `sudo apt install jq` at runtime, too."""
    fn = re.search(r"^_pkg_hint\(\) \{.*?^\}", ABS_SH.read_text(), re.S | re.M)
    assert fn, "_pkg_hint moved"
    script = tmp_path / ("hint-%s-%s.sh" % (osname, "-".join(have or ("none",))))
    script.write_text(fn.group(0) + "\n_pkg_hint jq\n")
    d = machine(tmp_path, osname=osname, have=have)
    out = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                         env={"PATH": str(d)}).stdout
    assert expect in out, f"{osname} {have} → {out!r}"
    assert forbid not in out, f"{osname} {have} → {out!r}"


def test_the_installer_no_longer_hardcodes_apt_for_jq():
    """The exact string from the report."""
    assert 'jq → ${c_bold}sudo apt install jq' not in INSTALL.read_text()


def test_it_parses_on_the_bash_macos_actually_ships():
    """bash 3.2. A multibyte character directly after an unbraced $var is read as
    part of the variable NAME there, which has broken a Mac install before."""
    r = subprocess.run(["bash", "-n", str(INSTALL)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    if not _which("docker"):
        pytest.skip("no docker to check bash 3.2 with")
    r = subprocess.run(["docker", "run", "--rm", "-v", f"{REPO}:/w:ro",
                        "bash:3.2", "bash", "-n", "/w/install.sh"],
                       capture_output=True, text=True, timeout=120)
    if "Unable to find image" in r.stderr or r.returncode == 125:
        pytest.skip("bash:3.2 image not available")
    assert r.returncode == 0, r.stdout + r.stderr
