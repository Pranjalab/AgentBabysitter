#!/usr/bin/env bash
#
# usage.sh — report Claude Code subscription limits, optionally to Telegram.
#
# The numbers come from `claude -p "/usage"`, which is the same client-side
# slash command the TUI runs. There is no `claude usage` subcommand and no
# public REST endpoint for this; driving the slash command in print mode is
# the only non-interactive source, so that is what we parse.
#
#   ./usage.sh              print to terminal, and send to Telegram
#   ./usage.sh --print      terminal only, no send
#   ./usage.sh --send       send only, no terminal output
#   ./usage.sh --install    add /usage to the bot's command menu
#
set -euo pipefail
umask 077

# --- config ------------------------------------------------------------------

# Mirrors claude-rc.sh. Kept in sync by hand; both read the plugin's .env.
readonly TG_DIR="${TELEGRAM_STATE_DIR:-$HOME/.claude/channels/telegram}"
readonly TG_ENV="$TG_DIR/.env"
readonly RC_DIR="${CLAUDE_RC_DIR:-$HOME/.claude/telegram-rc}"
readonly RC_STATE="$RC_DIR/rc.json"

# Percent-used thresholds at which the headline flips.
readonly WARN_AT=75
readonly CRIT_AT=90

BOT_TOKEN=""

c_reset=$'\033[0m'; c_bold=$'\033[1m'
c_green=$'\033[32m'; c_yellow=$'\033[33m'; c_red=$'\033[31m'; c_dim=$'\033[2m'
[ -t 2 ] || { c_reset=""; c_bold=""; c_green=""; c_yellow=""; c_red=""; c_dim=""; }

die() { printf '%s✗%s %s\n' "$c_red" "$c_reset" "$*" >&2; exit 1; }

# --- telegram ----------------------------------------------------------------
#
# Same -K - discipline as claude-rc.sh: Telegram puts the token in the URL path,
# and a normal `curl https://.../bot<TOKEN>/...` would expose it to every user on
# the box via `ps auxww`. Reading the URL from stdin keeps it out of argv.

load_token() {
  [ -f "$TG_ENV" ] || return 1
  BOT_TOKEN="$(grep -m1 '^TELEGRAM_BOT_TOKEN=' "$TG_ENV" 2>/dev/null | cut -d= -f2- | tr -d '"'\''[:space:]')"
  [ -n "$BOT_TOKEN" ]
}

tg_api() {
  local method="$1" body="$2"
  printf 'url = "https://api.telegram.org/bot%s/%s"\nheader = "Content-Type: application/json"\n' \
    "$BOT_TOKEN" "$method" \
    | curl -sS --max-time 20 -K - --data-binary "$body" 2>/dev/null || true
}

tg_send() {
  local chat="$1" text="$2" body
  body="$(jq -n --arg c "$chat" --arg t "$text" '{chat_id:$c,text:$t}')"
  tg_api sendMessage "$body" >/dev/null
}

# --- parsing -----------------------------------------------------------------

# `claude -p "/usage"` emits lines shaped like:
#   Current session: 43% used · resets Jul 16, 1:19am (UTC)
#   Current week (all models): 84% used · resets Jul 16, 5:29pm (UTC)
#   Current week (Fable): 86% used · resets Jul 16, 5:29pm (UTC)
# We take the percent and the reset stamp from whichever of those exist. The
# Fable line only appears once that model has been used this week.

fetch_usage() {
  local out
  out="$(cd /tmp && timeout 90 claude -p "/usage" 2>&1)" || return 1
  [ -n "$out" ] || return 1
  grep -q '% used' <<<"$out" || return 1
  printf '%s\n' "$out"
}

# field <raw> <label-regex> -> "PCT<TAB>RESET"; empty if the line is absent.
field() {
  local raw="$1" label="$2" line pct reset
  line="$(grep -m1 -E "^${label}:" <<<"$raw" || true)"
  [ -n "$line" ] || return 0
  pct="$(sed -E 's/.*: *([0-9]+)% used.*/\1/' <<<"$line")"
  reset="$(sed -E 's/.*resets ([^(]*).*/\1/' <<<"$line" | sed -E 's/ +$//')"
  printf '%s\t%s' "$pct" "$reset"
}

# "Jul 16, 5:29pm" -> "in 16h 26m". Falls back to the raw stamp if date(1)
# can't parse it, so a format change degrades instead of breaking.
until_reset() {
  local stamp="$1" target now delta h m
  target="$(date -d "$(tr -d ',' <<<"$stamp")" +%s 2>/dev/null || true)"
  [ -n "$target" ] || { printf '%s' "$stamp"; return; }
  now="$(date +%s)"
  # A reset that parses as past means we rolled the year; add one.
  [ "$target" -lt "$now" ] && target=$(date -d "$(tr -d ',' <<<"$stamp") +1 year" +%s 2>/dev/null || echo "$target")
  delta=$(( target - now ))
  [ "$delta" -lt 0 ] && { printf 'now'; return; }
  h=$(( delta / 3600 )); m=$(( (delta % 3600) / 60 ))
  if [ "$h" -gt 0 ]; then printf 'in %dh %dm' "$h" "$m"; else printf 'in %dm' "$m"; fi
}

bar() {
  local pct="$1" width=10 filled i out=""
  filled=$(( pct * width / 100 ))
  for ((i=0; i<width; i++)); do
    if [ "$i" -lt "$filled" ]; then out+="█"; else out+="░"; fi
  done
  printf '%s' "$out"
}

severity() {  # highest percent across all limits decides the headline
  local max="$1"
  if   [ "$max" -ge "$CRIT_AT" ]; then printf 'crit'
  elif [ "$max" -ge "$WARN_AT" ]; then printf 'warn'
  else printf 'ok'; fi
}

# --- report ------------------------------------------------------------------

build_report() {  # <raw> -> plain text, Telegram-safe (no markdown)
  local raw="$1" out="" max=0
  local rows=() label pct reset f

  while IFS= read -r spec; do
    label="${spec%%|*}"; f="$(field "$raw" "${spec#*|}")"
    [ -n "$f" ] || continue
    pct="${f%%$'\t'*}"; reset="${f#*$'\t'}"
    rows+=("$label|$pct|$reset")
    [ "$pct" -gt "$max" ] && max="$pct"
  done <<'SPECS'
5-hour session|Current session
Week (all models)|Current week \(all models\)
Week (Fable)|Current week \(Fable\)
SPECS

  [ "${#rows[@]}" -gt 0 ] || return 1

  case "$(severity "$max")" in
    crit) out="🔴 Claude usage — ${max}% on your tightest limit"$'\n\n' ;;
    warn) out="🟡 Claude usage — ${max}% on your tightest limit"$'\n\n' ;;
    *)    out="🟢 Claude usage — ${max}% on your tightest limit"$'\n\n' ;;
  esac

  local r
  for r in "${rows[@]}"; do
    label="${r%%|*}"; r="${r#*|}"; pct="${r%%|*}"; reset="${r#*|}"
    out+="$(printf '%s\n  %s %s%%  · resets %s (%s)' \
      "$label" "$(bar "$pct")" "$pct" "$(until_reset "$reset")" "$reset")"$'\n\n'
  done

  printf '%s' "${out%$'\n\n'}"
}

# --- commands ----------------------------------------------------------------

# Append /usage to the bot's existing command menu. setMyCommands REPLACES the
# whole list, so we read the current one and add to it rather than clobbering
# the plugin's own /new, /model, /resume, etc.
cmd_install() {
  load_token || die "No bot token at $TG_ENV. Run ./claude-rc.sh setup first."
  local cur new
  cur="$(tg_api getMyCommands '{}')"
  jq -e '.ok' >/dev/null 2>&1 <<<"$cur" || die "Could not read current bot commands."

  if jq -e '[.result[].command] | index("usage")' >/dev/null 2>&1 <<<"$cur"; then
    printf '%s✓%s /usage is already in the command menu.\n' "$c_green" "$c_reset" >&2
    return 0
  fi

  new="$(jq -c '{commands: (.result + [{command:"usage", description:"Show Claude usage limits and resets"}])}' <<<"$cur")"
  jq -e '.ok' >/dev/null 2>&1 <<<"$(tg_api setMyCommands "$new")" \
    || die "setMyCommands failed."
  printf '%s✓%s Added /usage to the bot command menu (%s commands total).\n' \
    "$c_green" "$c_reset" "$(jq '.commands|length' <<<"$new")" >&2
  printf '%s  Tap it in Telegram with a live RC session running.%s\n' "$c_dim" "$c_reset" >&2
}

main() {
  local mode="both"
  case "${1:-}" in
    --print|-p)   mode="print" ;;
    --send|-s)    mode="send" ;;
    --install)    cmd_install; return ;;
    -h|--help)    sed -n '3,13p' "$0" | sed 's/^# \?//'; return ;;
    "")           ;;
    *)            die "Unknown option: $1  (try --help)" ;;
  esac

  command -v jq   >/dev/null || die "jq is required."
  command -v curl >/dev/null || die "curl is required."

  local raw report
  raw="$(fetch_usage)" || die "Could not read usage from 'claude -p /usage'."
  report="$(build_report "$raw")" || die "Usage output did not match the expected format. Raw:"$'\n'"$raw"

  [ "$mode" = "send" ] || printf '%s\n' "$report"

  if [ "$mode" != "print" ]; then
    load_token || die "No bot token at $TG_ENV."
    local chat; chat="$(jq -r '.chat_id // empty' "$RC_STATE" 2>/dev/null || true)"
    [ -n "$chat" ] || die "No chat_id in $RC_STATE. Run ./claude-rc.sh setup first."
    tg_send "$chat" "$report"
    [ "$mode" = "send" ] || printf '%s→ sent to Telegram%s\n' "$c_dim" "$c_reset" >&2
  fi
}

main "$@"
