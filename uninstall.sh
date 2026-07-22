#!/usr/bin/env bash
#
# uninstall.sh — remove Agent Babysitter (`abs`).
#
#   ./uninstall.sh                                   # from a checkout
#   curl -fsSL <raw-url>/uninstall.sh | bash         # standalone
#
# Removes the `abs` command and, unless told otherwise, its local state.
# The Telegram bot token + allowlist are kept by default (so a reinstall
# doesn't force a re-pair) — pass --all to wipe those too.
#
# Flags / env:
#   --keep-state   remove only the `abs` command, leave ~/.abs untouched
#   --all          also remove the Telegram channel state (token, allowlist)
#   ABS_YES=1      assume "yes" to every prompt (implies a full, non-interactive run)
#   PREFIX=~/bin   where the command was installed (default ~/.local/bin)
#
# What it never touches: your Claude Code install, your git checkout of this
# repo (a git install is a symlink — only the link is removed), and the shell
# rc PATH line (it just tells you where it is).

set -euo pipefail

readonly PREFIX="${PREFIX:-$HOME/.local/bin}"
readonly TARGET="$PREFIX/abs"
readonly ABS_HOME="${ABS_HOME:-$HOME/.abs}"
readonly TG_DIR="$HOME/.claude/channels/telegram"

c_reset=$'\033[0m'; c_bold=$'\033[1m'; c_dim=$'\033[2m'
c_red=$'\033[31m'; c_green=$'\033[32m'; c_yellow=$'\033[33m'
[ -t 2 ] || { c_reset=""; c_bold=""; c_dim=""; c_red=""; c_green=""; c_yellow=""; }

info() { printf '%s\n' "$*" >&2; }
ok()   { printf '%s✓%s %s\n' "$c_green" "$c_reset" "$*" >&2; }
warn() { printf '%s!%s %s\n' "$c_yellow" "$c_reset" "$*" >&2; }
die()  { printf '%s✗%s %s\n' "$c_red" "$c_reset" "$*" >&2; exit 1; }

# --- args --------------------------------------------------------------------
keep_state=0
wipe_all=0
for a in "$@"; do
  case "$a" in
    --keep-state) keep_state=1 ;;
    --all)        wipe_all=1 ;;
    -h|--help)
      info "Usage: uninstall.sh [--keep-state] [--all]"
      info "  --keep-state  remove only the abs command, keep ~/.abs"
      info "  --all         also remove Telegram channel state (token, allowlist)"
      exit 0 ;;
    *) die "Unknown option: $a" ;;
  esac
done
[ "${ABS_YES:-0}" = "1" ] && wipe_all=1

# Piped into bash, stdin is the script — so prompt on /dev/tty, the human.
# No tty (CI, nohup, curl|bash with no terminal) means no consent to be had:
# fall back to the safe default (No) unless ABS_YES already forced the answer.
ask_yes() {
  [ "${ABS_YES:-0}" = "1" ] && return 0
  local reply=""
  { exec 3<>/dev/tty; } 2>/dev/null || return 1
  printf '  %s ' "$1" >&2
  if ! read -r reply <&3; then { exec 3<&-; } 2>/dev/null; return 1; fi
  { exec 3<&-; } 2>/dev/null
  case "$reply" in [Yy]|[Yy][Ee][Ss]) return 0 ;; *) return 1 ;; esac
}

# A file at $TARGET is only ours if it carries our version constant. grep -s
# follows a symlink to the real abs.sh, or reads a static copy directly. This
# stops us deleting some unrelated file that happens to sit at ~/.local/bin/abs.
abs_owned() {
  grep -qs '^readonly ABS_VERSION=' "$1" 2>/dev/null
}

info "${c_bold}Uninstall Agent Babysitter${c_reset}"
info ""

# --- 1. the command ----------------------------------------------------------
if [ -L "$TARGET" ]; then
  # Symlink = git install. Remove the link; never the checkout it points at.
  src="$(readlink "$TARGET" 2>/dev/null || true)"
  rm -f "$TARGET"
  ok "Removed command: $TARGET ${c_dim}(symlink → ${src:-?}, checkout left in place)${c_reset}"
elif [ -e "$TARGET" ]; then
  if abs_owned "$TARGET"; then
    rm -f "$TARGET"
    ok "Removed command: $TARGET"
  else
    warn "$TARGET exists but isn't Agent Babysitter — leaving it alone."
  fi
else
  info "${c_dim}No command at $TARGET (already gone).${c_reset}"
fi

# --- 2. abs state / profiles / logs ------------------------------------------
if [ "$keep_state" = 1 ]; then
  info "${c_dim}Keeping state at $ABS_HOME (--keep-state).${c_reset}"
elif [ -d "$ABS_HOME" ]; then
  if ask_yes "Remove abs state, profiles, local logs, and voice engines at ${c_bold}$ABS_HOME${c_reset}? [y/N]"; then
    rm -rf "$ABS_HOME"
    ok "Removed state: $ABS_HOME"
  else
    info "${c_dim}Kept $ABS_HOME.${c_reset}"
  fi
else
  info "${c_dim}No state at $ABS_HOME.${c_reset}"
fi

# --- 3. Telegram channel state (token + allowlist) ---------------------------
# Kept by default: wiping it forces a full re-pair on reinstall. Only removed
# on --all / ABS_YES, or an explicit yes at the prompt.
if [ -d "$TG_DIR" ]; then
  if [ "$wipe_all" = 1 ]; then
    rm -rf "$TG_DIR"
    ok "Removed Telegram channel state: $TG_DIR ${c_dim}(token + allowlist)${c_reset}"
  elif ask_yes "Also remove Telegram bot token + allowlist at ${c_bold}$TG_DIR${c_reset}? [y/N]"; then
    rm -rf "$TG_DIR"
    ok "Removed Telegram channel state: $TG_DIR"
  else
    info "${c_dim}Kept $TG_DIR — a reinstall will reuse the same bot, no re-pair.${c_reset}"
  fi
else
  info "${c_dim}No Telegram channel state at $TG_DIR.${c_reset}"
fi

# --- notes it won't do for you -----------------------------------------------
info ""
info "${c_bold}Done.${c_reset} A couple of things this script deliberately does not touch:"
info "  • ${c_bold}PATH line${c_reset} — if the installer added ${c_dim}export PATH=\"\$HOME/.local/bin:\$PATH\"${c_reset}"
info "    to your shell rc (~/.zshrc or ~/.bashrc), remove it by hand only if"
info "    nothing else of yours lives in $PREFIX."
info "  • ${c_bold}The Telegram bot itself${c_reset} still exists in BotFather. ${c_dim}/deletebot${c_reset} it there"
info "    if you want it gone for good."
info "  • ${c_bold}Claude Code${c_reset} and the ${c_bold}Telegram plugin${c_reset} are separate installs — left as-is."
info ""
info "Reinstall any time: ${c_bold}curl -fsSL https://agentbabysitter.com/install.sh | bash${c_reset}"
