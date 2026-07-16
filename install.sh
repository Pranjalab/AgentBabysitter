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
# Checked but not installed. Installing bun or a package manager's jq on
# someone's behalf is a bigger decision than they agreed to by running this.

missing=()
for c in claude curl jq bun; do
  command -v "$c" >/dev/null 2>&1 || missing+=("$c")
done
if [ ${#missing[@]} -gt 0 ]; then
  info "${c_bold}Agent Babysitter needs these first:${c_reset}"
  for m in "${missing[@]}"; do
    case "$m" in
      bun)    info "  bun    → curl -fsSL https://bun.sh/install | bash   ${c_dim}(the Telegram plugin's server runs on Bun)${c_reset}" ;;
      claude) info "  claude → https://claude.com/claude-code" ;;
      jq)     info "  jq     → sudo apt install jq   (or: brew install jq)" ;;
      curl)   info "  curl   → sudo apt install curl" ;;
    esac
  done
  die "Install those, then run this again."
fi

# --- fetch -------------------------------------------------------------------
#
# Prefer the checkout we're sitting in: someone who cloned the repo means to
# install *that* copy, not whatever main happens to be right now.

src=""
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
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

# `abs` is a short name, and both branches below overwrite whatever holds it —
# `ln -sfn` and `install` are equally happy to clobber. Agent Babysitter v1 (the
# Python one) put its own entry point here, and on a stranger's machine anything
# could. Taking someone's command out from under them without a word is not this
# installer's call to make.
if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
  current="$(readlink -f "$TARGET" 2>/dev/null || printf '%s' "$TARGET")"
  case "$current" in
    *"/abs.sh")
      : ;;  # already ours — re-running the installer is meant to relink
    *)
      if [ "${ABS_FORCE:-}" = "1" ]; then
        warn "Overwriting $TARGET because ABS_FORCE=1."
      else
        warn "$TARGET already exists, and it isn't this script:"
        info "      $current"
        info ""
        info "  That may be Agent Babysitter v1 (the Python package: ${c_bold}pip uninstall agent-babysitter${c_reset}),"
        info "  or something else entirely. Nothing here will overwrite it for you."
        info ""
        info "  Pick one:"
        info "    • move it aside:     ${c_bold}mv $TARGET $TARGET.old${c_reset}"
        info "    • install elsewhere: ${c_bold}PREFIX=~/bin ./install.sh${c_reset}"
        info "    • overwrite anyway:  ${c_bold}ABS_FORCE=1 ./install.sh${c_reset}"
        die "Refusing to replace a command this installer didn't create."
      fi ;;
  esac
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
ok "Agent Babysitter installed."
info ""
info "  ${c_bold}abs${c_reset}            start a session (walks you through bot setup on first run)"
info "  ${c_bold}abs help${c_reset}       everything else"
info ""
info "${c_dim}First run asks for a Telegram bot token from @BotFather, then pairs your${c_reset}"
info "${c_dim}account with a PIN. Nothing leaves your machine except Telegram API calls.${c_reset}"
