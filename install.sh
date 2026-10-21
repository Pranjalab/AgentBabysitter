#!/usr/bin/env bash
#
# install.sh — install Agent Babysitter as `abs`.
#
#   curl -fsSL https://agentbabysitter.com/install.sh | bash   # standalone
#   git clone … && cd AgentBabysitter && ./install.sh          # from a checkout
#
# Installs to ~/.local/bin/abs. Set PREFIX to change that.
#
# It runs in one pass: look at the machine, show everything that is missing in a
# single list, ask once, install all of it, verify. Reported 21 Oct from a new
# Mac — "Python was not installed and Claude Code was not installed so it needed
# to do all this" — because the old script met the gaps one at a time, three
# stops and three re-runs, each with advice for somebody else's operating system.
#
# Flags / environment:
#   -y, --yes, ABS_YES=1      install the missing dependencies without asking
#       --no-deps             only report what is missing; install nothing
#       ABS_REF=v3.7.0        install a pinned release instead of main
#       PREFIX=~/bin          install the `abs` command somewhere else
#       ABS_FORCE=1           overwrite an `abs` this installer did not create

set -euo pipefail

# ABS_REF must go on the RIGHT of the pipe, immediately before bash:
#
#   curl -fsSL https://agentbabysitter.com/install.sh | ABS_REF=v2.5.1 bash
#
# Put it in front of curl instead and it lands in curl's environment, not bash's
# — the two sides of a pipeline are separate processes — so the pin is silently
# ignored and you reinstall the very version you were escaping. Found 19 Aug,
# when a pinned beta install came back reporting the current release.
#
readonly ABS_REF="${ABS_REF:-main}"
readonly REPO="${ABS_REPO:-https://raw.githubusercontent.com/Pranjalab/AgentBabysitter/$ABS_REF}"
readonly PREFIX="${PREFIX:-$HOME/.local/bin}"
readonly TARGET="$PREFIX/abs"
readonly ABS_HOME="${ABS_HOME:-$HOME/.abs}"

# Everything below this line lives in one function, and the only thing after it
# is the call. That is not style.
#
# Piped from curl, THIS SCRIPT IS STDIN. bash reads a non-seekable script as it
# goes, so any child process that reads stdin eats the part bash has not parsed
# yet. Reported 21 Oct, from the Mac that had just been fixed: `brew install`
# drank the rest of this file mid-install — lines of it were echoed into the
# operator's terminal — and bash hit end-of-file right after the dependency
# summary. Every dependency was installed, "All dependencies present" was
# printed, and `abs` itself never was. It looked like a success.
#
# Inside a function, bash must read to the closing brace before anything runs,
# so by the time the first child starts there is nothing left in the pipe to
# steal. The body is not re-indented: the diff that introduced this is worth
# reading, and bash does not care either way.
#
# BASH_SOURCE is captured out here while it is unambiguous — piped, it is an
# empty array, and the checkout detection below depends on telling those apart.
ABS_SELF=""
if [ -f "${BASH_SOURCE[0]:-}" ]; then ABS_SELF="${BASH_SOURCE[0]}"; fi

main() {

assume_yes="${ABS_YES:-0}"
skip_deps=0
# Piped in there are no arguments at all; `… | bash -s -- --yes` is the form.
for arg in ${1+"$@"}; do
  case "$arg" in
    -y|--yes)  assume_yes=1 ;;
    --no-deps) skip_deps=1 ;;
    -h|--help)
      printf '%s\n' \
        "install.sh — install Agent Babysitter as 'abs'" "" \
        "  -y, --yes      install missing dependencies without asking" \
        "      --no-deps  report what is missing; install nothing" "" \
        "  ABS_REF=v3.7.0   install a pinned release" \
        "  PREFIX=~/bin     install the abs command elsewhere" \
        "  ABS_FORCE=1      overwrite an abs this installer did not create"
      exit 0 ;;
  esac
done

# ---- output -----------------------------------------------------------------

c_reset=$'\033[0m'; c_bold=$'\033[1m'; c_dim=$'\033[2m'
c_red=$'\033[31m'; c_green=$'\033[32m'; c_yellow=$'\033[33m'; c_cyan=$'\033[36m'
[ -t 2 ] || { c_reset=""; c_bold=""; c_dim=""; c_red=""; c_green=""; c_yellow=""; c_cyan=""; }

info() { printf '%s\n' "$*" >&2; }
ok()   { printf '  %s✓%s %s\n' "$c_green" "$c_reset" "$*" >&2; }
warn() { printf '  %s!%s %s\n' "$c_yellow" "$c_reset" "$*" >&2; }
die()  { printf '\n  %s✗%s %s\n\n' "$c_red" "$c_reset" "$*" >&2; exit 1; }

# A section heading with a rule under it, so a long install reads as steps
# rather than as one wall of lines. The number is counted, not written down:
# half of these sections are skipped on a machine that is already set up, and
# a run that goes "1 · … 4 · … 6 · …" reads like something went wrong.
stepno=0
step() {
  stepno=$((stepno + 1))
  info ""
  info "  ${c_bold}${stepno} · ${1}${c_reset}"
  info "  ${c_dim}────────────────────────────────────────────────────────────${c_reset}"
}
step_plain() {
  info ""
  info "  ${c_bold}${1}${c_reset}"
  info "  ${c_dim}────────────────────────────────────────────────────────────${c_reset}"
}

# mark · name · note — the checklist rows.
row() { printf '    %s  %-18s %s%s%s\n' "$1" "$2" "$c_dim" "$3" "$c_reset" >&2; }

banner() {
  info ""
  info "   ${c_bold}Agent Babysitter${c_reset}"
  info "   ${c_dim}Claude Code, on Telegram.${c_reset}"
}

# Piped in, stdin is this script — reading from it would swallow the rest of the
# file. /dev/tty is the human, when there is one. `[ -e /dev/tty ]` is not the
# test: the node exists under nohup/CI/cron and still fails to open for want of a
# controlling terminal, so try the open, and try it before printing.
#
# `default` is "y" when Enter should mean yes. A prompt showing a capital Y while
# treating Enter as no is a lie, so any caller writing "[Y/n]" must pass it.
# Note what this does NOT do: consult assume_yes. `--yes` means "install the
# dependencies you found without asking me" — it must not also answer yes to the
# optional extras below it, or an unattended install starts a multi-gigabyte
# voice build nobody asked for. The one place --yes applies says so explicitly.
ask_yes() {
  local reply="" default="${2:-n}"
  # Braces matter: `exec 3<>/dev/tty 2>/dev/null` applies redirections left to
  # right, so a failed open still prints before 2>/dev/null exists.
  { exec 3<>/dev/tty; } 2>/dev/null || return 1
  printf '  %s%s%s ' "$c_bold" "$1" "$c_reset" >&2
  # Close fd 3 inside a group too: a bare `exec 3<&- 2>/dev/null` would make the
  # 2>/dev/null permanent, and every info/ok/warn after it writes to >&2 — the
  # whole install would then run to completion in total silence.
  if ! read -r reply <&3; then { exec 3<&-; } 2>/dev/null; return 1; fi
  { exec 3<&-; } 2>/dev/null
  case "$reply" in
    [yY]|[yY][eE][sS]) return 0 ;;
    "") case "$default" in [yY]) return 0 ;; *) return 1 ;; esac ;;
    *) return 1 ;;
  esac
}

# ~/.zshrc, not /Users/somebody/.zshrc. These end up in the Next list at the
# bottom, where they are lines to be typed rather than facts to be read.
short_path() {
  case "$1" in
    "$HOME"/*) printf '~%s' "${1#"$HOME"}" ;;
    *)         printf '%s' "$1" ;;
  esac
}

have_tty() { { exec 3<>/dev/tty; } 2>/dev/null && { exec 3<&-; } 2>/dev/null; }

# The PATH this shell really has, kept before anything below widens it. The
# advice at the end of the run is about the shell the operator goes back to, not
# about ours: if we add ~/.local/bin ourselves in order to FIND a claude that is
# already installed, and then read that same PATH to decide whether `abs` is
# reachable, the one line they need to be told never gets printed.
ORIG_PATH="$PATH"

# ---- this machine -----------------------------------------------------------

case "$(uname -s 2>/dev/null)" in
  Darwin) OS="macos" ;;
  Linux)  OS="linux" ;;
  *)      OS="other" ;;
esac

# Homebrew lands in a different prefix on Apple Silicon and Intel, and its
# installer only edits your shell rc for the *next* login — so a brew that is
# installed and working is invisible to any shell that has not logged in since,
# including the one that just installed it.
#
# Reported 21 Oct, one run after Homebrew and python@3.13 both installed
# perfectly: "No package manager found on this machine, and abs needs: python3".
# Both of those were lies, and both came from the same place. The scan probed
# python before anything had put /opt/homebrew/bin on PATH, so a working
# python3.13 read as missing; and PKG had been decided by `command -v brew`
# alone, which never looks in the prefix. So this runs FIRST, before PKG is
# decided and before a single dependency is probed.
#
# The prefixes are a variable only so this is testable on a machine that is not
# a Mac — two Mac-only bugs in a row have been found by the operator rather than
# by me, which is not a good trade.
BREW_PREFIXES="${ABS_BREW_PREFIXES:-/opt/homebrew/bin/brew /usr/local/bin/brew}"
brew_onpath() {
  local b
  command -v brew >/dev/null 2>&1 && return 0
  for b in $BREW_PREFIXES; do
    [ -x "$b" ] || continue
    eval "$("$b" shellenv)" 2>/dev/null || true
    command -v brew >/dev/null 2>&1 && return 0
  done
  return 1
}

# PKG is the package manager we will actually drive; empty means we have no way
# to install system packages here and must say so rather than invent a command.
PKG=""
detect_pkg() {
  case "$OS" in
    macos) if brew_onpath >/dev/null 2>&1; then PKG="brew"; else PKG=""; fi ;;
    linux)
      if   command -v apt-get >/dev/null 2>&1; then PKG="apt"
      elif command -v dnf     >/dev/null 2>&1; then PKG="dnf"
      elif command -v pacman  >/dev/null 2>&1; then PKG="pacman"
      elif command -v apk     >/dev/null 2>&1; then PKG="apk"
      elif command -v zypper  >/dev/null 2>&1; then PKG="zypper"
      else PKG=""; fi ;;
    *) PKG="" ;;
  esac
}
detect_pkg

# Homebrew's problem, two directories along. Bun installs into ~/.bun and Claude
# Code into ~/.local/bin, and both edit your rc for the NEXT login — so in the
# shell that just installed them they are invisible, and this script would
# cheerfully download both again. Reported 21 Oct: a second run in the same tab
# re-offered everything that had just succeeded. Probe where they actually live.
for _d in "${BUN_INSTALL:-$HOME/.bun}/bin" "$HOME/.local/bin"; do
  [ -d "$_d" ] || continue
  case ":$PATH:" in *":$_d:"*) ;; *) PATH="$_d:$PATH" ;; esac
done
export PATH

sudo_run() {
  if [ "$(id -u)" = "0" ]; then "$@"
  elif command -v sudo >/dev/null 2>&1; then sudo "$@"
  else return 127; fi
}

# The package name for a generic dependency on this platform.
pkg_name() {
  case "$1" in
    jq) printf 'jq' ;;
    python)
      case "$PKG" in
        pacman) printf 'python' ;;
        # Name the formula, not the alias. `brew install python3` relies on an
        # alias whose existence is not something this script can check, and a
        # wrong package name fails three steps into a run the operator has
        # already said yes to. python@3.13 is a real formula and not keg-only,
        # so it lands on PATH; the probe accepts any 3.11+ anyway.
        brew)   printf 'python@3.13' ;;
        *)      printf 'python3' ;;
      esac ;;
  esac
}

# What the plan will show, and what pkg_install actually runs. One command for
# all of the packages, not one per package.
pkg_cmdline() {
  local s=""
  [ "$(id -u)" = "0" ] || [ "$PKG" = "brew" ] || s="sudo "
  case "$PKG" in
    brew)   printf 'brew install %s' "$*" ;;
    apt)    printf '%sapt-get install -y %s' "$s" "$*" ;;
    dnf)    printf '%sdnf install -y %s' "$s" "$*" ;;
    pacman) printf '%spacman -S --needed --noconfirm %s' "$s" "$*" ;;
    apk)    printf '%sapk add %s' "$s" "$*" ;;
    zypper) printf '%szypper install -y %s' "$s" "$*" ;;
    *)      printf 'install %s with your package manager' "$*" ;;
  esac
}

pkg_install() {
  case "$PKG" in
    brew)   brew install "$@" ;;
    apt)    sudo_run apt-get update -qq || true; sudo_run apt-get install -y "$@" ;;
    dnf)    sudo_run dnf install -y "$@" ;;
    pacman) sudo_run pacman -S --needed --noconfirm "$@" ;;
    apk)    sudo_run apk add "$@" ;;
    zypper) sudo_run zypper install -y "$@" ;;
    *)      return 1 ;;
  esac
}

# ---- what is already here ---------------------------------------------------

# Python 3.11 or newer under any of the names it goes by. The daemon is asyncio
# and uses 3.11 syntax, so an older interpreter fails at *import* — a much worse
# place to find out than here.
python_version() {
  local c v
  for c in python3.14 python3.13 python3.12 python3.11 python3 python; do
    command -v "$c" >/dev/null 2>&1 || continue
    v="$("$c" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
    case "$v" in
      3.1[1-9]*|3.[2-9][0-9]*|[4-9].*) printf '%s' "$v"; return 0 ;;
    esac
  done
  return 1
}

version_of() {
  case "$1" in
    curl)   curl --version 2>/dev/null | head -1 | awk '{print $2}' ;;
    jq)     jq --version   2>/dev/null | sed 's/^jq-//' ;;
    bun)    bun --version  2>/dev/null | head -1 ;;
    claude) claude --version 2>/dev/null | head -1 | awk '{print $1}' ;;
    python) python_version ;;
    brew)   brew --version 2>/dev/null | head -1 | awk '{print $2}' ;;
  esac
}

have() {
  case "$1" in
    python) python_version >/dev/null 2>&1 ;;
    *)      command -v "$1" >/dev/null 2>&1 ;;
  esac
}

note_for() {
  case "$1" in
    curl)   printf 'downloads everything below' ;;
    jq)     printf 'abs keeps its state as JSON' ;;
    python) printf '3.11+ — daemon, sandboxes, abs prompt' ;;
    bun)    printf "runs the Telegram plugin's server" ;;
    claude) printf 'the thing abs babysits' ;;
    brew)   printf 'macOS has no package manager of its own' ;;
  esac
}

# curl is the one thing we cannot bootstrap our way out of.
have curl || die "This installer needs curl.
  macOS:  it ships with curl — if this fires, your PATH is unusual
  Linux:  sudo apt install curl   (or dnf/pacman/apk)"

banner
step "What this machine already has"
info ""

need=""            # generic names, in install order
for dep in curl jq python bun claude; do
  if have "$dep"; then
    row "${c_green}✓${c_reset}" "$dep" "$(version_of "$dep")"
  else
    row "${c_yellow}·${c_reset}" "$dep" "$(note_for "$dep")"
    need="$need $dep"
  fi
done

# Homebrew is not a dependency of abs — it is how we get the other two on a Mac.
need_brew=0
case "$OS:$need" in
  macos:*jq*|macos:*python*)
    if ! brew_onpath; then
      need_brew=1
      row "${c_yellow}·${c_reset}" "homebrew" "$(note_for brew)"
    fi ;;
esac

if [ -z "$need" ]; then
  info ""
  ok "Everything abs needs is already here."
fi

# ---- the plan ---------------------------------------------------------------

if [ -n "$need" ]; then
  # A Mac that is about to get Homebrew HAS a package manager as far as
  # everything below is concerned. This has to happen before pkg_name runs, or
  # the package names are chosen for a platform with no manager at all — which
  # is how a bare Mac ended up being offered `brew install python3` instead of
  # the formula this script means.
  [ "$need_brew" = "1" ] && PKG="brew"

  # Split into the ones a package manager owns and the ones that install
  # themselves into $HOME with no sudo.
  pkgs=""
  selfs=""
  for dep in $need; do
    case "$dep" in
      jq|python) pkgs="$pkgs $(pkg_name "$dep")" ;;
      bun|claude) selfs="$selfs $dep" ;;
    esac
  done

  # No package manager and something that needs one: say exactly that.
  if [ -n "$pkgs" ] && [ "$need_brew" = "0" ] && [ -z "$PKG" ]; then
    info ""
    warn "No package manager found on this machine, and abs needs:${pkgs}"
    info "    Install those with whatever this system uses, then run this again."
    die "Stopping — guessing a package manager would waste your time twice."
  fi

  step "What needs installing"
  info ""
  info "    ${c_dim}Nothing below is run until you say so.${c_reset}"
  info ""

  n=0
  plan_line() { n=$((n + 1)); printf '    %s%d%s  %-14s %s%s%s\n' \
    "$c_bold" "$n" "$c_reset" "$1" "$c_cyan" "$2" "$c_reset" >&2; }

  [ "$need_brew" = "1" ] && plan_line "Homebrew" \
    '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  if [ -n "$pkgs" ]; then
    # shellcheck disable=SC2086
    plan_line "$(echo $pkgs | sed 's/ / + /g')" "$(pkg_cmdline $pkgs)"
  fi
  case "$selfs" in *bun*)    plan_line "Bun"         'curl -fsSL https://bun.sh/install | bash' ;; esac
  case "$selfs" in *claude*) plan_line "Claude Code" 'curl -fsSL https://claude.ai/install.sh | bash' ;; esac

  info ""
  if [ "$need_brew" = "1" ] || { [ -n "$pkgs" ] && [ "$PKG" != "brew" ] && [ "$(id -u)" != "0" ]; }; then
    info "    ${c_dim}Those steps use sudo and will ask for your account password.${c_reset}"
    info ""
  fi

  if [ "$skip_deps" = "1" ]; then
    die "--no-deps: nothing installed. Run the lines above, then run this again."
  fi

  if ! have_tty && [ "$assume_yes" != "1" ]; then
    info "    ${c_dim}No terminal to ask on. Run those lines, then run this again —${c_reset}"
    info "    ${c_dim}or re-run with --yes to install them unattended.${c_reset}"
    die "Stopping rather than installing software you did not agree to."
  fi

  if [ "$assume_yes" = "1" ]; then
    info "    ${c_dim}--yes: installing without asking.${c_reset}"
  elif ! ask_yes "Install all of this now? [Y/n]" y; then
    info ""
    info "    No problem. Run the lines above, then:"
    info "      ${c_bold}curl -fsSL https://agentbabysitter.com/install.sh | bash${c_reset}"
    info ""
    exit 1
  fi

  step "Installing"
  info ""

  if [ "$need_brew" = "1" ]; then
    info "    ${c_dim}Homebrew — the slow one. It will ask you to press RETURN and then for${c_reset}"
    info "    ${c_dim}your password, and it installs Apple's command line tools if needed.${c_reset}"
    # To a file, not through $( ): a failed download makes the substitution empty,
    # `bash -c ""` then SUCCEEDS, and the next line blames PATH for a brew that was
    # never downloaded. Fetch it, check it is really a script, then run it.
    brew_sh="$(mktemp -t brewinstall.XXXXXX.sh)"
    curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh -o "$brew_sh" \
      || { rm -f "$brew_sh"; die "Could not download Homebrew's installer. Get it from https://brew.sh"; }
    head -1 "$brew_sh" | grep -q '^#!' \
      || { rm -f "$brew_sh"; die "What came back from Homebrew is not a script — refusing to run it."; }

    # Hand it the terminal rather than setting NONINTERACTIVE.
    #
    # Reported 21 Oct, on the first real Mac to run this: NONINTERACTIVE makes
    # Homebrew's installer use `sudo -n` — sudo that is forbidden to prompt — so
    # on a Mac whose password is not already cached it aborts immediately with
    # "Insufficient permissions to install Homebrew to /opt/homebrew", having
    # never asked. We are piped from curl, so our own stdin is the script; the
    # terminal is still there on /dev/tty. Given that, the installer is properly
    # interactive and sudo asks for the password the way it does everywhere else.
    brew_rc=0
    if { exec 9<>/dev/tty; } 2>/dev/null; then
      /bin/bash "$brew_sh" <&9 >&2 || brew_rc=$?
      { exec 9<&-; } 2>/dev/null
    else
      rm -f "$brew_sh"
      die "Homebrew needs a terminal to ask for your password on, and there is none here.
  Install it yourself, then run this again:
    /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    fi
    rm -f "$brew_sh"
    [ "$brew_rc" = "0" ] \
      || die "Homebrew's installer did not finish (exit $brew_rc). If it asked for a key
  and you pressed something other than RETURN, that is all this was — run the
  installer again. Otherwise install Homebrew from https://brew.sh first."
    brew_onpath || die "Homebrew installed but brew is not on PATH. Open a new terminal and run this again."
    ok "Homebrew $(version_of brew)"
    PKG="brew"
  fi

  if [ -n "$pkgs" ]; then
    # shellcheck disable=SC2086
    info "    ${c_dim}$(pkg_cmdline $pkgs)${c_reset}"
    # shellcheck disable=SC2086
    pkg_install $pkgs >&2 || die "That package install failed. Fix it, then run this again."
    # brew keeps some pythons out of the prefix bin; make sure the one we just
    # installed is actually reachable before calling this a success.
    have python || { brew_onpath >/dev/null 2>&1 || true; }
    for dep in jq python; do
      case " $need " in
        *" $dep "*) have "$dep" && ok "$dep $(version_of "$dep")" \
                      || warn "$dep still is not on PATH — open a new terminal and run this again." ;;
      esac
    done
  fi

  case "$selfs" in *bun*)
    info "    ${c_dim}Bun — into ~/.bun, no sudo.${c_reset}"
    curl -fsSL https://bun.sh/install | bash >/dev/null 2>&1 \
      || die "Bun's installer failed. Install it yourself: https://bun.sh"
    # Its installer edits your rc for the next login; this shell needs it now.
    export BUN_INSTALL="${BUN_INSTALL:-$HOME/.bun}"
    export PATH="$BUN_INSTALL/bin:$PATH"
    have bun || die "Bun installed but is not on PATH — open a new terminal and run this again."
    bun_fresh=1
    ok "Bun $(version_of bun)" ;;
  esac

  case "$selfs" in *claude*)
    info "    ${c_dim}Claude Code — into ~/.local/bin, no sudo.${c_reset}"
    curl -fsSL https://claude.ai/install.sh | bash >/dev/null 2>&1 \
      || die "Claude Code's installer failed. Install it yourself: https://claude.com/claude-code"
    export PATH="$HOME/.local/bin:$PATH"
    have claude || die "Claude Code installed but is not on PATH — open a new terminal and run this again."
    claude_fresh=1
    ok "Claude Code $(version_of claude)" ;;
  esac

  # Everything claimed, re-checked — and a re-run of this script then has nothing
  # left to do, which is the point.
  info ""
  still=""
  for dep in curl jq python bun claude; do
    have "$dep" || still="$still $dep"
  done
  if [ -n "$still" ]; then
    warn "Installed, but not yet on this shell's PATH:${still}"
    info "      abs goes in anyway. Open a new terminal before your first ${c_bold}abs${c_reset}."
  else
    ok "All dependencies present."
  fi
fi

bun_fresh="${bun_fresh:-0}"
claude_fresh="${claude_fresh:-0}"

# ---- the abs command --------------------------------------------------------

step "Installing abs"
info ""

# Piped in there is no script file at all: BASH_SOURCE is an empty array, which
# under `set -u` aborts the expansion — and the wreckage collapses to `cd ""`,
# which succeeds and quietly leaves us in the caller's directory. A stray abs.sh
# there would then get installed instead of the real one. Only trust BASH_SOURCE
# when it actually names a file.
src=""
here=""
if [ -n "$ABS_SELF" ]; then
  here="$(cd "$(dirname "$ABS_SELF")" 2>/dev/null && pwd || true)"
fi

if [ -n "$here" ] && [ -f "$here/abs.sh" ]; then
  src="$here/abs.sh"
  info "    ${c_dim}From this checkout.${c_reset}"
else
  # No checkout, so this is the single-script install: abs.sh alone is a
  # complete v2, and `abs src install` below unpacks the rest into ~/.abs/src.
  here=""
  src="$(mktemp -t abs.XXXXXX.sh)"
  trap 'rm -f "$src"' EXIT
  info "    ${c_dim}Downloading abs.sh…${c_reset}"
  curl -fsSL "$REPO/abs.sh" -o "$src" || die "Could not download $REPO/abs.sh"
  # A truncated download that still starts with a shebang would install cleanly
  # and fail at the worst moment. Parse it before trusting it.
  bash -n "$src" 2>/dev/null || die "Downloaded file is not valid bash — refusing to install it."
fi

# `abs` is a short name, and both branches below overwrite whatever holds it. A
# previous Agent Babysitter carries our ABS_VERSION constant, so we recognize it
# and update in place — that is how everyone gets new versions. Agent Babysitter
# v1 (the unrelated Python namesake) and anything else a stranger put here does
# not carry it, so we refuse to clobber it without a word.
abs_owned() {
  grep -q '^readonly ABS_VERSION=' "$1" 2>/dev/null && return 0
  # Only fall back to the name for a DANGLING symlink (a deleted checkout): a
  # live target already got the grep, so trusting the name there would let any
  # unrelated `abs -> …/abs.sh` be silently overwritten.
  if [ -L "$1" ] && [ ! -e "$1" ]; then
    case "$(readlink "$1" 2>/dev/null)" in *"/abs.sh") return 0 ;; esac
  fi
  return 1
}

if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
  if abs_owned "$TARGET"; then
    old_ver="$(grep -m1 '^readonly ABS_VERSION=' "$TARGET" 2>/dev/null | sed -E 's/.*"([^"]+)".*/\1/')"
    [ -n "$old_ver" ] \
      && info "    ${c_dim}Found Agent Babysitter $old_ver — updating in place.${c_reset}" \
      || info "    ${c_dim}Found an existing Agent Babysitter — updating in place.${c_reset}"
  elif [ "${ABS_FORCE:-}" = "1" ]; then
    warn "Overwriting $TARGET because ABS_FORCE=1."
  else
    current="$(readlink -f "$TARGET" 2>/dev/null || printf '%s' "$TARGET")"
    warn "$TARGET already exists, and it is not Agent Babysitter:"
    info "      $current"
    info ""
    info "    That is most likely the old v1 Python package, or something else"
    info "    entirely. Nothing here will overwrite it for you. Pick one:"
    info "      • remove it:         ${c_bold}pip uninstall agent-babysitter${c_reset}"
    info "      • install elsewhere: ${c_bold}PREFIX=~/bin ./install.sh${c_reset}"
    info "      • overwrite anyway:  ${c_bold}ABS_FORCE=1 ./install.sh${c_reset}"
    die "Refusing to replace a command this installer did not create."
  fi
fi

# Whether an abs was there BEFORE we wrote one, which is the only way to tell
# whether the operator's shell could already know the name. See the Next list.
abs_existed=0
if [ -e "$TARGET" ]; then abs_existed=1; fi

mkdir -p "$PREFIX"
if [ -n "$here" ]; then
  # Symlink, so `git pull` updates the installed command too. abs.sh resolves
  # its own path with readlink -f precisely so this works.
  ln -sfn "$src" "$TARGET"
  ok "Linked $TARGET → $src"
else
  install -m 755 "$src" "$TARGET"
  ok "Installed $TARGET"
fi

ver="$(grep -m1 '^readonly ABS_VERSION=' "$src" 2>/dev/null | sed -E 's/.*"([^"]+)".*/\1/')"

# PATH. Only worth saying anything when `abs` is not already reachable.
#
# path_note is what the Next list at the bottom reads: 0 nothing to do, 1 a
# profile was written and this shell needs it, 2 nobody could write it and the
# operator has to. Reported 21 Oct: `zsh: command not found: abs`, with no
# instruction anywhere at the end of the run — the old code only appended to a
# profile that ALREADY EXISTED, and a brand new Mac has no ~/.zshrc, so the one
# machine that most needed the PATH line was the one guaranteed not to get it.
path_rc=""
path_note=0
# ORIG_PATH in both tests, deliberately: the question is whether `abs` will be
# reachable in the shell they go back to, not in this one, which we widened
# ourselves a few hundred lines up in order to find Bun and Claude Code.
abs_reachable=0
case ":$ORIG_PATH:" in *":$PREFIX:"*) abs_reachable=1 ;; esac
if [ "$abs_reachable" = "0" ] \
   && (PATH="$ORIG_PATH"; command -v abs >/dev/null 2>&1); then
  abs_reachable=1
fi
if [ "$abs_reachable" = "0" ]; then
  case "${SHELL:-}" in
    */zsh)  path_rc="$HOME/.zshrc" ;;
    */bash) path_rc="$HOME/.bashrc" ;;
  esac
  line="export PATH=\"$PREFIX:\$PATH\""
  if [ -n "$path_rc" ]; then
    if grep -qF "$PREFIX" "$path_rc" 2>/dev/null; then
      ok "$(short_path "$PREFIX") is already in $(short_path "$path_rc")"
    elif printf '\n# Agent Babysitter\n%s\n' "$line" >> "$path_rc" 2>/dev/null; then
      ok "$(short_path "$PREFIX") added to $(short_path "$path_rc")"
    else
      warn "Could not write to $path_rc."
      path_rc=""
    fi
    if [ -n "$path_rc" ]; then path_note=1; fi
  fi
  if [ -z "$path_rc" ]; then
    case "${SHELL:-}" in
      */fish) warn "$PREFIX is not on your PATH. Run: ${c_bold}fish_add_path $PREFIX${c_reset}" ;;
      *)      warn "$PREFIX is not on your PATH. Add this to your shell's profile:"
              info "      $line" ;;
    esac
    path_note=2
  fi
  export PATH="$PREFIX:$PATH"
fi

# ---- the v3 source ----------------------------------------------------------
#
# abs.sh alone is a complete v2. The daemon, sandboxes and the start menu need
# the absd package and a venv, which `abs src install` unpacks into ~/.abs/src.
# Not fatal: losing a working v2 over an optional layer would be the wrong trade.

if [ -z "$here" ] || [ ! -d "$here/absd" ]; then
  if ! grep -q '^cmd_src()' "$src" 2>/dev/null; then
    info ""
    info "    ${c_dim}This release predates the bundled v3 source — v2 features only.${c_reset}"
  else
    step "The v3 source"
    info ""
    info "    ${c_dim}The daemon, sandboxes and the start menu.${c_reset}"
    info ""
    "$TARGET" src install >&2 || {
      warn "The v3 source did not install — abs still works as v2."
      info "      Try again any time: ${c_bold}abs src install${c_reset}"
    }
  fi
fi

# Where the v3 tree ended up, for the two optional offers below.
src_root=""
if   [ -n "$here" ] && [ -d "$here/absd" ]; then src_root="$here"
elif [ -d "$ABS_HOME/src/absd" ];           then src_root="$ABS_HOME/src"
fi

# ---- optional: the always-on daemon (systemd, so Linux only) ----------------

if [ "$OS" = "linux" ] && [ -n "$src_root" ] && [ -x "$src_root/.venv/bin/python" ] \
   && command -v systemctl >/dev/null 2>&1; then
  step "Optional — the always-on daemon"
  info ""
  info "    ${c_dim}Polls your idle bots, so you can start a session from Telegram${c_reset}"
  info "    ${c_dim}(ABS START) with nothing running on the machine.${c_reset}"
  info ""
  if ask_yes "Install the absd systemd unit? [y/N]"; then
    "$TARGET" daemon install >&2 || warn "Daemon install did not finish — run: abs daemon install"
    info ""
    info "      ${c_bold}systemctl --user enable --now absd${c_reset}   ${c_dim}# start now + on login${c_reset}"
    info "      ${c_bold}abs daemon status${c_reset}                    ${c_dim}# check it${c_reset}"
  else
    info "      ${c_dim}Skipped. Any time: ${c_reset}${c_bold}abs daemon install${c_reset}"
  fi

  # A nicer attach UI than tmux, and never required — tmux is always the
  # fallback. Pinned to one checksum-verified asset, never a `curl | sh`.
  if [ "$(uname -m)" = "x86_64" ] && ! command -v herdr >/dev/null 2>&1 \
     && [ ! -x "$HOME/.local/bin/herdr" ]; then
    info ""
    info "    ${c_dim}herdr gives a nicer attach UI than tmux. Pinned 0.7.5, checksummed.${c_reset}"
    info ""
    if ask_yes "Install herdr too? [y/N]"; then
      url="https://github.com/ogulcancelik/herdr/releases/download/v0.7.5/herdr-linux-x86_64"
      sha="3dc83288073e4c2d3c679a30e7be97bcca9141c6fd17dbbb9219142e95c59253"
      dst="$HOME/.local/bin/herdr"; tmp="$dst.download"
      mkdir -p "$(dirname "$dst")"
      if ! curl -fL -o "$tmp" "$url" 2>/dev/null; then
        rm -f "$tmp"; warn "herdr download failed — tmux will be used."
      elif command -v sha256sum >/dev/null 2>&1 \
           && ! printf '%s  %s\n' "$sha" "$tmp" | sha256sum -c - >/dev/null 2>&1; then
        rm -f "$tmp"; warn "herdr checksum mismatch — refusing to install it."
      elif command -v shasum >/dev/null 2>&1 \
           && [ "$(shasum -a 256 "$tmp" | awk '{print $1}')" != "$sha" ]; then
        rm -f "$tmp"; warn "herdr checksum mismatch — refusing to install it."
      else
        chmod +x "$tmp" && mv -f "$tmp" "$dst" && ok "herdr 0.7.5 → $dst" \
          || { rm -f "$tmp"; warn "herdr not installed — tmux will be used."; }
      fi
    fi
  fi
fi

# ---- optional: voice --------------------------------------------------------
#
# Local, but big (Whisper in, Kokoro out — a few GB), so never forced. Offered
# once, and the build itself is handed to the abs we just installed so there is a
# single source of truth for it.

voice_already() {
  local r
  for r in ${src_root:+"$src_root"}; do
    [ -x "$r/.venv/bin/python" ] || continue
    { [ -x "$r/.venv-kokoro/bin/python" ] || [ -x "$r/.venv-tts/bin/python" ]; } && return 0
  done
  return 1
}

if [ -n "$src_root" ] && voice_already; then
  : # already built — asking again is the "redo everything" this script avoids
elif [ -n "$src_root" ]; then
  step "Optional — voice"
  info ""
  info "    ${c_dim}Send Claude a voice note; hear its replies as voice notes.${c_reset}"
  info "    ${c_dim}Entirely on your machine. One-time download of a few GB.${c_reset}"
  info "    ${c_dim}Without it, replies are text — abs offers again on first launch.${c_reset}"
  # ffmpeg packages the synthesized audio as the Opus a Telegram voice note has
  # to be, and `abs voice setup` refuses without it rather than installing a
  # package itself. A brand-new Mac has no ffmpeg, so this offer was a dead end
  # with a default of yes: say yes, watch it stop. The package manager is known
  # by now — name it in the offer, and install it as part of the yes.
  need_ffmpeg=0
  if ! command -v ffmpeg >/dev/null 2>&1; then
    need_ffmpeg=1
    info ""
    if [ -n "$PKG" ]; then
      info "    ${c_dim}It needs ffmpeg too, which is not here yet: $(pkg_cmdline ffmpeg)${c_reset}"
    else
      info "    ${c_dim}It needs ffmpeg too, which is not here yet — install that first.${c_reset}"
    fi
  fi
  info ""
  if ask_yes "Set up voice now? [Y/n]" y; then
    info ""
    if [ "$need_ffmpeg" = "1" ] && [ -n "$PKG" ]; then
      info "    ${c_dim}$(pkg_cmdline ffmpeg)${c_reset}"
      pkg_install ffmpeg >&2 </dev/null \
        || warn "ffmpeg did not install — voice setup will stop short of audio."
    fi
    "$TARGET" voice setup >&2 \
      || warn "Voice setup did not finish — any time: ${c_bold}abs voice setup${c_reset}"
  else
    info "      ${c_dim}Skipped. Any time: ${c_reset}${c_bold}abs voice setup${c_reset}"
  fi
fi

# ---- done -------------------------------------------------------------------

step_plain "Ready"
info ""
if [ -n "$ver" ]; then ok "Agent Babysitter ${c_bold}${ver}${c_reset}  ${c_dim}(from ${ABS_REF})${c_reset}"
else                   ok "Agent Babysitter installed  ${c_dim}(from ${ABS_REF})${c_reset}"; fi

# Naming the ref even when it is `main` is the cheap half of the pinning fix: a
# pin that goes missing cannot be detected — nothing here knows what you meant —
# but someone who typed a tag and reads "from main" knows in a second, instead of
# testing the wrong build for an hour.
if [ "$ABS_REF" != "main" ]; then
  info ""
  warn "This is a PINNED install of $ABS_REF, not the current release."
  info "      It will not update itself past this version. Back to current:"
  info "      ${c_bold}curl -fsSL https://agentbabysitter.com/install.sh | bash${c_reset}"
fi

info ""
info "  ${c_bold}Next${c_reset}"
info ""
nn=0
next() { nn=$((nn + 1)); printf '    %s%d%s  %-34s %s%s%s\n' \
  "$c_bold" "$nn" "$c_reset" "$1" "$c_dim" "$2" "$c_reset" >&2; }

# This has to be the first thing in the list and it has to be a command, not
# advice: the run that prompted it ended with `abs` installed, correct, and
# unreachable, and "open a new terminal" buried 60 lines up is not an answer.
if [ "$path_note" = "1" ]; then
  next "source $(short_path "$path_rc")" "puts abs on THIS shell's PATH"
  info "       ${c_dim}opening a new terminal does the same${c_reset}"
elif [ "$path_note" = "2" ]; then
  next "put $(short_path "$PREFIX") on your PATH" "the line is printed above"
elif [ "$abs_existed" = "0" ]; then
  # PREFIX was already on PATH, so there was nothing to write and the list used
  # to say nothing at all — and that is the case the operator hit:
  #
  #     zsh: command not found: abs
  #
  # on a machine whose PATH was already correct. zsh builds its table of every
  # command on PATH when the shell starts, so a command created after that is
  # unknown until you tell it to look again. bash only caches what it has
  # actually run, so `hash -r` there is belt and braces rather than a fix.
  case "${SHELL:-}" in
    */zsh) next "rehash" "zsh listed your PATH before abs was on it" ;;
    *)     next "hash -r" "so this shell notices a command newer than itself" ;;
  esac
elif [ "$bun_fresh" = "1" ] || [ "$claude_fresh" = "1" ]; then
  next "open a new terminal" "so the new PATH is picked up"
fi

# Login is needed before a session can start, and a fresh machine has not done
# it. Saying so here turns a confusing first launch into a one-line instruction.
if [ -s "$HOME/.claude/.credentials.json" ] \
   || { [ -f "$HOME/.claude.json" ] && grep -q '"oauthAccount"' "$HOME/.claude.json" 2>/dev/null; }; then
  :
else
  next "claude" "log in to Claude Code, then /exit"
fi

next "abs" "first run walks you through the bot"
info ""
info "    ${c_dim}abs help${c_reset}   everything else"
info "    ${c_dim}abs doctor${c_reset} if anything looks wrong"
info ""
info "  ${c_dim}First run asks for a bot token from @BotFather, then pairs your account${c_reset}"
info "  ${c_dim}with a PIN. Nothing leaves your machine except Telegram API calls.${c_reset}"
info ""
}

# The last line, and nothing after it: see the note above main.
main ${1+"$@"}
