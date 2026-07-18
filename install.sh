#!/usr/bin/env bash
#
# install.sh — install Agent Babysitter as `abs`.
#
#   git clone … && cd AgentBabysitter && ./install.sh      # from a checkout
#   curl -fsSL <raw-url>/install.sh | bash           # standalone
#
# Installs to ~/.local/bin/abs. Set PREFIX to change that.

set -euo pipefail

readonly REPO="${ABS_REPO:-https://raw.githubusercontent.com/Pranjalab/AgentBabysitter/main}"
readonly PREFIX="${PREFIX:-$HOME/.local/bin}"
readonly TARGET="$PREFIX/abs"

c_reset=$'\033[0m'; c_bold=$'\033[1m'; c_dim=$'\033[2m'
c_red=$'\033[31m'; c_green=$'\033[32m'; c_yellow=$'\033[33m'
[ -t 2 ] || { c_reset=""; c_bold=""; c_dim=""; c_red=""; c_green=""; c_yellow=""; }

info() { printf '%s\n' "$*" >&2; }
ok()   { printf '%s✓%s %s\n' "$c_green" "$c_reset" "$*" >&2; }
warn() { printf '%s!%s %s\n' "$c_yellow" "$c_reset" "$*" >&2; }
die()  { printf '%s✗%s %s\n' "$c_red" "$c_reset" "$*" >&2; exit 1; }

# --- dependencies ------------------------------------------------------------
#
# curl is the only thing this installer itself needs. The rest is what `abs`
# needs at runtime, and abs.sh re-checks every one of them on each start — so
# nothing here can fail silently later.
#
# Bun gets special treatment because it's the one that stops people. Anthropic's
# Telegram plugin hardcodes `"command": "bun"` in its .mcp.json and runs
# server.ts directly, so there's no node fallback to fall back to. It's also the
# only dep that installs cleanly into $HOME with no sudo. Offer it; don't run
# someone else's installer unannounced just because they piped this to bash.

command -v curl >/dev/null 2>&1 || die "This installer needs curl. (sudo apt install curl)"

bun_fresh=0
claude_fresh=0

# Piped in, stdin is the script — reading from it would swallow the rest of this
# file. /dev/tty is the human, when there is one. No tty (CI, nohup) means no
# consent to be had, so callers get instructions instead of a surprise.
# `[ -e /dev/tty ]` is not the test: the node exists under nohup/CI/cron and
# still fails to open for want of a controlling terminal. Try the open, and do
# it before printing — a prompt nobody can answer is worse than no prompt.
ask_yes() {
  local reply=""
  # Braces matter: `exec 3<>/dev/tty 2>/dev/null` applies redirections left to
  # right, so the failed open still prints before 2>/dev/null exists. Grouping
  # redirects the group's stderr first, which swallows it.
  { exec 3<>/dev/tty; } 2>/dev/null || return 1
  printf '  %s ' "$1" >&2
  # Close fd 3 inside a group. Bare `exec 3<&- 2>/dev/null` would make the
  # 2>/dev/null permanent — exec's redirections outlive the statement — and
  # every info/ok/warn after this point writes to >&2, so the whole install
  # would run to completion in total silence.
  if ! read -r reply <&3; then { exec 3<&-; } 2>/dev/null; return 1; fi
  { exec 3<&-; } 2>/dev/null
  case "$reply" in [yY]|[yY][eE][sS]) return 0 ;; *) return 1 ;; esac
}

if ! command -v bun >/dev/null 2>&1; then
  info "${c_bold}Agent Babysitter needs Bun${c_reset} — the Telegram plugin's server runs on it."
  info "${c_dim}Installs to ~/.bun. No sudo, nothing outside your home directory.${c_reset}"
  if ask_yes "Install Bun now? [y/N]"; then
    info "  ${c_dim}Installing Bun…${c_reset}"
    curl -fsSL https://bun.sh/install | bash >/dev/null 2>&1 \
      || die "Bun's installer failed. Install it yourself: https://bun.sh"
    # Its installer edits your shell rc for the *next* login. This shell needs
    # bun on PATH now, or the abs.sh runtime check fails on first run.
    export BUN_INSTALL="${BUN_INSTALL:-$HOME/.bun}"
    export PATH="$BUN_INSTALL/bin:$PATH"
    command -v bun >/dev/null 2>&1 || die "Bun installed but isn't on PATH — open a new shell and re-run this."
    bun_fresh=1
    ok "Bun installed."
  else
    info ""
    info "  No problem — install it yourself with:"
    info "    ${c_bold}curl -fsSL https://bun.sh/install | bash${c_reset}"
    die "Then re-run this installer."
  fi
fi

# Claude Code is the whole point — abs runs it. It ships an official installer
# that drops into ~/.local/bin with no sudo, so we can offer it just like Bun.
if ! command -v claude >/dev/null 2>&1; then
  info "${c_bold}Agent Babysitter runs Claude Code${c_reset} — and it isn't installed yet."
  info "${c_dim}Anthropic's installer puts it in ~/.local/bin. No sudo.${c_reset}"
  if ask_yes "Install Claude Code now? [y/N]"; then
    info "  ${c_dim}Installing Claude Code…${c_reset}"
    curl -fsSL https://claude.ai/install.sh | bash >/dev/null 2>&1 \
      || die "Claude Code's installer failed. Install it yourself: https://claude.com/claude-code"
    # Its installer targets ~/.local/bin; make it visible to this shell now, or
    # the check just below (and abs's own runtime check) fails on first run.
    export PATH="$HOME/.local/bin:$PATH"
    command -v claude >/dev/null 2>&1 || die "Claude Code installed but isn't on PATH — open a new shell and re-run this."
    claude_fresh=1
    ok "Claude Code installed."
  else
    info ""
    info "  No problem — install it yourself with:"
    info "    ${c_bold}curl -fsSL https://claude.ai/install.sh | bash${c_reset}"
    die "Then re-run this installer."
  fi
fi

# jq is the one dep we can't auto-install: it wants a package manager and a sudo
# password this script has no business asking for. Name it and stop.
if ! command -v jq >/dev/null 2>&1; then
  info "${c_bold}Agent Babysitter needs jq:${c_reset}"
  info "  jq → ${c_bold}sudo apt install jq${c_reset}   (or: ${c_bold}brew install jq${c_reset})"
  die "Install it, then run this again."
fi

# --- fetch -------------------------------------------------------------------
#
# Prefer the checkout we're sitting in: someone who cloned the repo means to
# install *that* copy, not whatever main happens to be right now.

#
# Piped in (`curl … | bash`) there is no script file at all: BASH_SOURCE is an
# empty array, which under `set -u` aborts the expansion — and the wreckage
# collapses to `cd ""`, which succeeds and quietly leaves us in the *caller's*
# directory. A stray abs.sh there would then get installed instead of the real
# one. Only trust BASH_SOURCE when it actually names a file.

src=""
here=""
if [ -f "${BASH_SOURCE[0]:-}" ]; then
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
fi
if [ -n "$here" ] && [ -f "$here/abs.sh" ]; then
  src="$here/abs.sh"
  info "${c_dim}Installing from this checkout.${c_reset}"
else
  src="$(mktemp -t abs.XXXXXX.sh)"
  trap 'rm -f "$src"' EXIT
  info "${c_dim}Downloading abs.sh…${c_reset}"
  curl -fsSL "$REPO/abs.sh" -o "$src" || die "Could not download $REPO/abs.sh"
  # A truncated download that still starts with a shebang would install cleanly
  # and then fail at the worst moment. Parse it before trusting it.
  bash -n "$src" 2>/dev/null || die "Downloaded file isn't valid bash — aborting rather than installing it."
fi

# --- install -----------------------------------------------------------------

# `abs` is a short name, and both branches below overwrite whatever holds it.
# A prior Agent Babysitter — the git symlink OR a curl/pipx static copy — carries
# our ABS_VERSION constant, so we recognize it and update it in place (this is how
# every existing user gets new versions: just re-run the installer). Agent
# Babysitter v1 (the unrelated Python namesake) and anything else a stranger put
# here does NOT carry it, so we still refuse to clobber it without a word.
abs_owned() {
  # grep follows a symlink to the real abs.sh; on a static copy it reads the copy.
  grep -q '^readonly ABS_VERSION=' "$1" 2>/dev/null && return 0
  case "$(readlink -f "$1" 2>/dev/null)" in *"/abs.sh") return 0 ;; esac
  return 1
}

if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
  if abs_owned "$TARGET"; then
    old_ver="$(grep -m1 '^readonly ABS_VERSION=' "$TARGET" 2>/dev/null | sed -E 's/.*"([^"]+)".*/\1/')"
    [ -n "$old_ver" ] \
      && info "${c_dim}Found Agent Babysitter $old_ver — updating it in place.${c_reset}" \
      || info "${c_dim}Found an existing Agent Babysitter — updating it in place.${c_reset}"
  elif [ "${ABS_FORCE:-}" = "1" ]; then
    warn "Overwriting $TARGET because ABS_FORCE=1."
  else
    current="$(readlink -f "$TARGET" 2>/dev/null || printf '%s' "$TARGET")"
    warn "$TARGET already exists, and it isn't Agent Babysitter:"
    info "      $current"
    info ""
    info "  That's most likely the old v1 Python package (${c_bold}pip uninstall agent-babysitter${c_reset}),"
    info "  or something else entirely. Nothing here will overwrite it for you."
    info ""
    info "  Pick one:"
    info "    • remove the old one: ${c_bold}pip uninstall agent-babysitter${c_reset}  ${c_dim}then re-run this${c_reset}"
    info "    • install elsewhere:  ${c_bold}PREFIX=~/bin ./install.sh${c_reset}"
    info "    • overwrite anyway:   ${c_bold}ABS_FORCE=1 ./install.sh${c_reset}"
    die "Refusing to replace a command this installer didn't create."
  fi
fi

mkdir -p "$PREFIX"

if [ -n "$here" ] && [ -f "$here/abs.sh" ]; then
  # Symlink, so `git pull` updates the installed command too. abs.sh
  # resolves its own path with readlink -f precisely so this works.
  ln -sfn "$src" "$TARGET"
  ok "Linked $TARGET → $src"
else
  install -m 755 "$src" "$TARGET"
  ok "Installed $TARGET"
fi

# --- PATH --------------------------------------------------------------------

if ! command -v abs >/dev/null 2>&1; then
  case ":$PATH:" in
    *":$PREFIX:"*) ;;
    *)
      warn "$PREFIX is not on your PATH."
      rc=""
      case "${SHELL##*/}" in
        zsh)  rc="$HOME/.zshrc" ;;
        bash) rc="$HOME/.bashrc" ;;
      esac
      line="export PATH=\"$PREFIX:\$PATH\""
      if [ -n "$rc" ] && [ -f "$rc" ] && ! grep -qF "$PREFIX" "$rc" 2>/dev/null; then
        printf '\n# Agent Babysitter\n%s\n' "$line" >> "$rc"
        ok "Added it to $rc — open a new shell, or: source $rc"
      else
        info "  Add this to your shell profile:"
        info "    $line"
      fi
      ;;
  esac
fi

info ""
ver="$(grep -m1 '^readonly ABS_VERSION=' "$src" 2>/dev/null | sed -E 's/.*"([^"]+)".*/\1/')"
if [ -n "$ver" ]; then
  ok "Agent Babysitter $ver installed."
else
  ok "Agent Babysitter installed."
fi
info ""
info "  ${c_bold}abs${c_reset}            start a session (walks you through bot setup on first run)"
info "  ${c_bold}abs help${c_reset}       everything else"
info ""

# We put bun on PATH for this script's own shell, but the caller's shell won't
# see it until its rc is re-read. Saying nothing here means their very first
# `abs` dies on a missing bun we just installed for them.
if [ "$bun_fresh" = "1" ]; then
  warn "Bun is new here — open a new shell before your first ${c_bold}abs${c_reset}, or run:"
  info "    ${c_bold}export PATH=\"\$HOME/.bun/bin:\$PATH\"${c_reset}"
  info ""
fi
if [ "$claude_fresh" = "1" ]; then
  warn "Claude Code is new here — open a new shell before your first ${c_bold}abs${c_reset}, or run:"
  info "    ${c_bold}export PATH=\"\$HOME/.local/bin:\$PATH\"${c_reset}"
  info ""
fi
info "${c_dim}First run asks for a Telegram bot token from @BotFather, then pairs your${c_reset}"
info "${c_dim}account with a PIN. Nothing leaves your machine except Telegram API calls.${c_reset}"
