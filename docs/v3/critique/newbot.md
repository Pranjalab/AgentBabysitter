# Critique — `abs start new-bot`: provisioning a new bot end to end

Stage 2 of the start matrix: from a terminal, `abs start new-bot` creates a brand-new
Telegram bot + ABS profile, pairs it, and launches a session on it — in one flow,
without a daemon restart. It is deliberately terminal-only and interactive, and it
reuses the whole `abs setup` machinery rather than reinventing it.

## The flow

`abs start new-bot` (interactive TTY):

1. **Guard first** — `assert_no_live_session` on the resolved (default) profile. As
   with every launch, don't provision while a session is live: Telegram permits one
   poller per bot and an interactive flow here would collide with it.
2. **Resolve a relay target** — before touching the new bot, pick an EXISTING trusted
   profile whose bot can relay the pairing PIN to the operator's phone (see below).
   Its token + chat are read now, while globals still point at it.
3. **Token entry (terminal-only)** — `read_new_token`: type the BotFather token at the
   terminal (`read -s`, no echo), validate its shape, verify with `getMe` → `@username`.
4. **Derive the profile name** from `@username` (sanitized to `use_profile`'s jail);
   prompt for a short name on empty or collision.
5. **Persist** — `use_profile <name>`, then `save_token` writes the token into the new
   profile's `.env` (0600). Reuses `write_access` / `write_state` exactly as setup does.
6. **Pair the new bot** — `do_pairing` on the NEW bot, with the PIN ALSO relayed to the
   operator's phone via the trusted bot.
7. **Launch** — pick a cwd (this folder / registered projects / workspace children),
   `cd`, then `cmd_run` (hooks, `session.pid`, exec claude) — the ordinary launch path.

Nothing above is a fork of setup: `print_token_instructions`, `read_new_token`,
`save_token`, `gen_pin`, `do_pairing`, `write_access`, `write_state`, `use_profile`,
and the registry targets picker are all shared. `prompt_token` (setup's entry) is now
just `instructions + read_new_token + save_token` — one behavior, two callers.

## Why token entry is terminal-only

A bot token is a bearer credential: whoever holds it owns the bot. Accepting a token
from a Telegram message would mean the *phone* (or anyone who compromised it) could
inject a bot the daemon then polls — the compromised-phone attack. So the token is
typed at the terminal, the one channel whose trust is established out-of-band. There
is no code path that reads a token from Telegram; `read_new_token` reads `/dev/tty`.

## The PIN relay — a convenience, not a trust anchor

The pairing PIN (6 chars, `gen_pin`) is generated on the host and normally read off the
terminal. New-bot ALSO sends it to the operator's phone via an already-trusted bot, so
they can complete pairing on the new bot without alt-tabbing to the terminal. The
argument that this is safe:

- **It travels only to an already-trusted chat.** The relay target
  (`relay_target()` in `absd/newbot.py`) is the `default` profile if usable, else the
  first profile with BOTH a token and a paired `chat_id`. The PIN goes to *that
  profile's* paired chat_id, via *that profile's* token — a chat already paired AND
  allowlisted on an existing bot. Intercepting it requires already controlling that
  trusted channel, in which case the operator is already compromised.
- **It is short-lived and single-use.** The PIN is only valid for `PAIR_TIMEOUT`
  (5 min) and satisfies exactly one pairing; after that it is inert.
- **It confers nothing on its own.** The PIN proves "the phone holding it is the phone
  that should own the new bot." Without the token (terminal-only), a leaked PIN pairs
  nothing — there is no bot to pair to.

Mechanically, `do_pairing` takes optional `relay_token/relay_cid/relay_bot`; after
generating the PIN it swaps `BOT_TOKEN` to the trusted token, `tg_send`s the PIN to the
trusted chat, and restores `BOT_TOKEN` before polling the NEW bot. Setup's call passes
no relay args, so setup is unchanged.

## Daemon profile rescan (the end-to-end piece)

The daemon discovered profiles at boot ONLY, so a bot provisioned while it runs would be
deaf until a restart. Now the supervisor runs a periodic rescan (`profile_rescan_s`,
default 60s; 0 disables):

- `_rescan_once` re-runs `discover()`, diffs against the live poller map, spins up a
  staggered poller for each NEW profile with a token, and cleanly stops+drops the
  poller (cancel task, close client) for each profile whose dir VANISHED.
- It acts ONLY on the set difference — existing pollers, and any live sessions, are
  never touched. A tokenless (half-written) profile is skipped; a later cycle picks it
  up once the token lands.
- Emits `profile_added` / `profile_removed` events for the observability timeline.

A rescan-added poller shares the daemon's engine, event log, and session-count callable,
so it is indistinguishable from a boot poller (including the daemon-wide `max_sessions`
cap).

## Honest gaps

- **The relay assumes a trusted default profile exists.** On a first-ever bot (no
  profiles yet), `relay_target` returns `None` and the PIN is terminal-only — the flow
  says so and continues. That is correct, not a bug, but it means the "relay to phone"
  convenience only kicks in from the second bot onward.
- **Rescan latency is up to `profile_rescan_s`.** A newly-paired bot is deaf to Telegram
  for up to ~60s until the next rescan. The new-bot flow launches a session immediately,
  so the operator is not waiting on the daemon; but a message sent to the new bot in that
  window is only pooled once the poller comes up. Lowering the cadence trades faster
  pickup for more `discover()` churn.
- **Relay send failures degrade to terminal-only.** If the trusted `tg_send` fails
  (revoked token, network), the flow warns and the operator uses the terminal PIN — the
  PIN is always shown at the terminal regardless.
- **No automated test pairs against real Telegram.** The pairing/relay happy path is a
  manual test (`docs/v3/manual-tests/newbot.md`); the automated suite covers the pure
  helpers, the daemon rescan, and the abs.sh guards only.
- **Relay selection is "first usable", not operator-chosen.** With multiple non-default
  trusted profiles and no default, the alphabetically-first relays. In practice the
  default profile is almost always present, so this is a corner case.
