# Manual test — `abs start new-bot`

Prereq: the daemon installed + running; an EXISTING paired `default` profile (so the
PIN relay has a trusted bot to send through); a phone with Telegram. This provisions a
REAL new bot and pairs it — you'll create a throwaway bot in BotFather.

> The automated suite never pairs against real Telegram (it covers helpers, the daemon
> rescan, and the guards). This doc is the real end-to-end pass.

## 1. Create the bot in BotFather (have the token ready)

In Telegram, message **@BotFather** → `/newbot` → give it a name and a unique username
ending in `bot`. Copy the token (`123456789:AA...`). Don't send it anywhere yet.

## 2. Run the flow

From any project directory, with **no session running**:

```sh
abs start new-bot
```

Expected, in order:

- It refuses immediately if a session is already live on `default` ("already has a live
  poller") — quit that first.
- It prints the BotFather walkthrough and asks **`Bot token:`** (input hidden). Paste the
  token. It verifies with Telegram: **`Authenticated as @<username>`**.
- It derives a profile name from `@username` (or asks for a short name on a collision).
- **Step 2 of 2** shows a PIN and says it was *also* sent to your phone via `@<default's
  bot>`. Check your phone: a message from your existing bot with
  **`Pairing PIN for @<newbot>: <PIN> …`**.

## 3. Complete pairing on the NEW bot

Open the NEW bot (`t.me/<username>`), tap **Start**, and send it the PIN (read from
either the terminal or your phone). The terminal should show **`Paired with Telegram
user <id>`**, write the allowlist + state, send the new bot a "paired ✅" confirmation,
and register its `/` command menu.

## 4. Pick where it runs, and it launches

It asks **"Where should this bot's sessions run?"** — `1` = this folder, or a registered
project / workspace child. Pick one. Claude Code launches for the NEW profile in that
folder (its own bot, its own allowlist).

## 5. The daemon picks it up (rescan)

End the session (`/exit` or `abs --profile <name> exit`). Within **~60s** (the
`profile_rescan_s` cadence), the daemon starts polling the new bot with no restart.
Confirm:

```sh
abs daemon status            # the new profile appears, state "polling"
journalctl --user -u absd -n 30    # "rescan: profile <name> appeared — poller started"
```

Send the new bot a message while idle — it should be acked + pooled by the daemon, and
delivered as the initial prompt on the next `ABS START`.

## 6. Negative checks

- **No trusted bot:** on a machine with no paired profiles, `abs start new-bot` shows the
  PIN in the terminal only ("No trusted bot to relay through") and still pairs fine.
- **Bad token:** paste a malformed or revoked token → it aborts cleanly ("Telegram
  rejected that token" / "doesn't look like a bot token") without creating a profile.
- **From Telegram:** there is no way to start this from the phone — token entry is
  terminal-only by design.

## Cleanup

```sh
abs --profile <name> reset   # drop the token/allowlist/state
rm -rf ~/.abs/profiles/<name>    # remove the profile dir; rescan drops its poller
```

Then delete the bot in BotFather (`/deletebot`) if it was a throwaway.
