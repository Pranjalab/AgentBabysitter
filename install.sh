#!/usr/bin/env bash
#
# install.sh — install Claude RC as `crc`.
#
#   git clone … && cd claude-rc && ./install.sh      # from a checkout
#   curl -fsSL <raw-url>/install.sh | bash           # standalone
#
# Installs to ~/.local/bin/crc. Set PREFIX to change that.

set -euo pipefail

readonly REPO="${CLAUDERC_REPO:-https://raw.githubusercontent.com/pranjalab/claude-rc/main}"
readonly PREFIX="${PREFIX:-$HOME/.local/bin}"
readonly TARGET="$PREFIX/crc"

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
  info "${c_bold}Claude RC needs these first:${c_reset}"
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
if [ -n "$here" ] && [ -f "$here/clauderc.sh" ]; then
  src="$here/clauderc.sh"
  info "${c_dim}Installing from this checkout.${c_reset}"
else
  src="$(mktemp -t clauderc.XXXXXX.sh)"
  trap 'rm -f "$src"' EXIT
  info "${c_dim}Downloading clauderc.sh…${c_reset}"
  curl -fsSL "$REPO/clauderc.sh" -o "$src" || die "Could not download $REPO/clauderc.sh"
  # A truncated download that still starts with a shebang would install cleanly
  # and then fail at the worst moment. Parse it before trusting it.
  bash -n "$src" 2>/dev/null || die "Downloaded file isn't valid bash — aborting rather than installing it."
fi

# --- install -----------------------------------------------------------------

mkdir -p "$PREFIX"

if [ -n "$here" ] && [ -f "$here/clauderc.sh" ]; then
  # Symlink, so `git pull` updates the installed command too. clauderc.sh
  # resolves its own path with readlink -f precisely so this works.
  ln -sfn "$src" "$TARGET"
  ok "Linked $TARGET → $src"
else
  install -m 755 "$src" "$TARGET"
  ok "Installed $TARGET"
fi

# --- PATH --------------------------------------------------------------------

if ! command -v crc >/dev/null 2>&1; then
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
        printf '\n# Claude RC\n%s\n' "$line" >> "$rc"
        ok "Added it to $rc — open a new shell, or: source $rc"
      else
        info "  Add this to your shell profile:"
        info "    $line"
      fi
      ;;
  esac
fi

info ""
ok "Claude RC installed."
info ""
info "  ${c_bold}crc${c_reset}            start a session (walks you through bot setup on first run)"
info "  ${c_bold}crc help${c_reset}       everything else"
info ""
info "${c_dim}First run asks for a Telegram bot token from @BotFather, then pairs your${c_reset}"
info "${c_dim}account with a PIN. Nothing leaves your machine except Telegram API calls.${c_reset}"
