# Claude RC

Remote-control a Claude Code session from Telegram.

Start Claude with `crc` instead of `claude`. When a task finishes, you get a short report on your phone. Reply to it and Claude picks up the instruction. The terminal keeps working exactly as normal — Telegram and the terminal are the *same* session, not two conversations.

Built for the case where you kick off some work, walk away, and want to know how it went without going back to the desk.

```
┌──────────┐   task done → short report    ┌──────────┐
│ Terminal │ ───────────────────────────►  │ Telegram │
│  Claude  │                               │  (phone) │
│   Code   │ ◄─────────────────────────── │          │
└──────────┘   "fix the test and rerun"    └──────────┘
      ▲
      └── you can still type here at the same time
```

## What this actually is

This is **glue, not a new system**. Anthropic ships an official Telegram plugin (`telegram@claude-plugins-official`) and Claude Code has a `--channels` flag that pushes Telegram messages into a live session. The plugin owns all the inbound polling. Claude RC does the parts it doesn't:

- Prompts for your bot token and validates it before saving.
- Pairs your Telegram account to the machine with a PIN, without you typing anything into Claude.
- Injects the "report when done, ask for feedback" behavior per-session, so your projects' `CLAUDE.md` stays untouched.
- Manages **profiles**, so you can run several sessions at once on different bots.
- Puts a proper **command menu** in the chat.
- Reports your **subscription limits** to the phone.
- Gives you a real on/off switch.

The whole thing is one bash script with no dependencies of its own. Voice (`speak.py`, `transcribe.py`) is an optional add-on — skip it and RC still works.

## Install

**Prerequisites:** `claude`, `bun`, `jq`, `curl`. The plugin's server runs on Bun — install with `curl -fsSL https://bun.sh/install | bash` if you don't have it. The installer checks and tells you what's missing rather than installing things behind your back.

```sh
git clone https://github.com/pranjalab/claude-rc
cd claude-rc
./install.sh
```

That links `~/.local/bin/crc` to the checkout, so `git pull` updates the command too.

**1. Create a bot.** In Telegram, message [@BotFather](https://t.me/BotFather), send `/newbot`, pick a name and a username ending in `bot`. It gives you a token like `123456789:AAHfiqksKZ8...`.

**2. Run it** from whatever project you want Claude to work in:

```sh
cd ~/my-project
crc
```

First run walks you through it:

- Paste the token (input is hidden). It's verified against Telegram immediately, so a typo fails now rather than three steps later.
- The script prints a 6-character PIN. Send that PIN to your bot on Telegram.
- Once it arrives, you're paired. The bot confirms in-chat, registers the command menu, and Claude Code starts.

Every run after that just starts the session. Setup is once per bot, not per project.

**3. Try it.** Ask Claude to do something, then walk away. When it finishes you'll get a message. Reply to it and Claude will keep going.

## Commands

| Command | What it does |
| --- | --- |
| `crc` | Start a session with RC active (first run does setup) |
| `crc status` | Pairing, inbound state, mute state, whether the poller is live |
| `crc profiles` | List every bot and which ones are in use |
| `crc usage` | Subscription limits — to terminal and Telegram |
| `crc menu` | Re-register the `/` command menu |
| `crc quiet on` | Mute reports — **inbound still works** |
| `crc quiet off` | Resume reports |
| `crc off` | Hard off — drop *all* inbound Telegram |
| `crc on` | Re-enable inbound |
| `crc setup` | Re-pair (reuses a working saved token — `reset` first to change it) |
| `crc reset` | Delete this profile's token, allowlist, and state |
| `crc help` | Command list |

Extra arguments pass straight through to `claude`:

```sh
crc --model opus
crc --resume
```

You can also just say it in chat — "rc quiet", "rc off", "rc status" — from Telegram or the terminal. Claude runs the same commands.

## Profiles — more than one session at once

**Telegram allows exactly one `getUpdates` poller per bot token.** That's a hard platform limit, not a design choice here: two sessions on one bot fight over every message and one loses with a `409 Conflict`. So concurrent sessions need **a bot each**.

A profile is one bot's pairing — its token, allowlist, and chat, kept together:

```sh
crc                        # picks the profile, or asks if you have several
crc --profile work         # use (or create) the 'work' bot
CLAUDERC_PROFILE=work crc  # same, from the environment
crc profiles               # who's who, and what's live
```

```
Claude RC profiles
  default  @claudepranbot  live (pid 245324)
  work     @pranwork_bot   idle
```

Running `crc` with no arguments and more than one profile gives you a picker, with in-use profiles marked. Choose `n` to add a new bot — that's the "same token, or a new one?" decision, made per session rather than baked into config.

`crc` refuses to start a session on a profile that's already being polled, and tells you which one. Reusing a single bot across two *simultaneous* sessions isn't supported because Telegram won't allow it; sequential reuse is fine.

## From the phone

### The command menu

The `/` button lists what Claude Code itself understands:

| Command | What it does |
| --- | --- |
| `/model` | Switch model (`sonnet` / `opus` / `haiku`) mid-session |
| `/effort` | Set thinking effort (`low` / `medium` / `high`) |
| `/stop` | Interrupt whatever is running right now |
| `/compact` | Compact the context without going to the terminal |
| `/new`, `/resume`, `/sessions`, `/use` | Start, resume, list sessions; set the default workspace |
| `/usage` | Subscription limits and reset times |

`/model` and `/effort` are the two worth remembering. Kicking a long task down to `haiku`, or up to `opus` for something hairy, works from bed.

Setup registers this menu **at chat scope** (`crc menu` re-registers it), and that detail is load-bearing. The plugin re-registers its own three commands (`/start`, `/help`, `/status`) at `all_private_chats` scope every time it starts, and that scope outranks the default one — so without this you'd see three commands in your DM instead of ten. Chat scope outranks `all_private_chats`, so ours wins and survives every restart. The list is *read* from whatever Claude Code registers rather than hardcoded, so it tracks new commands automatically.

`/start`, `/help` and `/status` are reserved — the plugin answers them itself and they never reach Claude.

`/usage` is the odd one out: Claude Code doesn't know it, so it arrives as plain text and the injected prompt tells Claude to run `crc usage --send`. That's an instruction, not a wired handler. If it ever no-ops, say "run crc usage" instead.

Plain English works for anything without a command: "switch to opus", "stop", "how much context is left".

### Why there's no button bar

There was one — a reply keyboard pinned above the input, with `[/model opus]`, `[/stop]` and friends. It worked, and it's gone anyway: it ate a third of a phone screen to duplicate what the `/` menu already does in one tap. If you're tempted to add it back, two findings from that build are worth keeping:

- **Inline buttons cannot work here** (the tidier kind, attached under a message). They deliver taps as `callback_query` updates, and only the plugin polls this bot. Its `reply` tool has no `reply_markup` parameter, and its callback handler ignores any data that isn't its own permission prompt — so they'd be silently tap-dead, with no error to explain why. Reply-keyboard taps arrive as ordinary text, which the plugin already forwards. That was the seam.
- **Reply-keyboard labels *are* the payload.** A `KeyboardButton` sends its label verbatim; there's no separate data field. A prettier `[🧠 Opus]` sends the literal text `🧠 Opus`, which doesn't start with `/`, so Claude Code never parses it as a command. The labels have to be the commands, which is exactly why the bar looked like a wall of slash-commands.

Telegram stores a reply keyboard client-side per chat, so deleting the code doesn't clear it. `crc menu` sends a `remove_keyboard` to sweep up any bar left over from an older version.

### Two kinds of off, and why

**`quiet`** stops the reports but keeps listening. This is almost always what you want. Because inbound still works, **you can unmute from your phone.**

**`off`** drops everything inbound. It's a genuine kill switch — the plugin re-reads its config on every message, so it takes effect instantly with no restart. The catch: once inbound is dead, Telegram can't turn it back on. **`off` can only be undone at the terminal.** Don't run it and then walk away.

## Usage limits

`crc usage` reports how much of your subscription you've burned and when it resets — the thing you actually want to know before starting a long task from your phone.

```sh
crc usage            # print, and send to Telegram
crc usage --print    # terminal only
crc usage --send     # Telegram only
```

```
🟡 Claude usage — 87% on your tightest limit

5-hour session
  ██░░░░░░░░ 27%  · resets in 3h 55m (Jul 16, 6:20am)

Week (all models)
  ████████░░ 87%  · resets in 15h 5m (Jul 16, 5:30pm)
```

**Where the numbers come from, and why that matters.** There is no `claude usage` subcommand and no public REST endpoint for this. The only non-interactive source is `claude -p "/usage"` — the same client-side slash command the TUI runs — so `crc` drives that and parses the text. That means **it is parsing a human-readable format that Anthropic can change without warning.** It's written to degrade rather than lie: an unparseable reset stamp falls back to printing the raw stamp, and output that doesn't match at all exits with an error and dumps what it saw. If it ever breaks, that's the first place to look.

A model's line only appears once you've used it that week, so the report is short early in the week and grows.

### The subprocess has to be isolated, or it kills your channel

`crc usage` runs `claude --strict-mcp-config -p "/usage"`. **That flag is load-bearing.**

Without it, the subprocess loads every globally-enabled plugin — including the Telegram one. The plugin's server enforces Telegram's one-poller-per-token rule by `SIGTERM`ing whatever pid is in `bot.pid` when it boots, then removes the file when it exits a second later. Net effect: **asking for usage from Telegram kills the channel that would deliver the answer**, and nothing restarts it.

Not theoretical — measured, by running both against a live poller:

| run | flag | poller |
| --- | --- | --- |
| `crc usage` | `--strict-mcp-config` | survived, same pid |
| `claude -p …` | none | killed, `bot.pid` gone |

`--strict-mcp-config` makes the subprocess load no MCP servers at all, so it never touches `bot.pid`. **If you add anything else that shells out to `claude`, it needs the same flag.**

## Voice notes

Send a voice note and Claude transcribes it. Ask for a reply in voice and it speaks back. Both run locally — no audio leaves the machine for transcription or synthesis.

```sh
.venv/bin/python transcribe.py <file.oga>              # speech → text (faster-whisper)
.venv-tts/bin/python speak.py "text" out.ogg           # text → speech (chatterbox)
```

`speak.py --exag` is an emotion dial: `0.3` flat, `0.5` natural, `0.8+` animated. Lower `--cfg` slows the delivery, which pairs well with a high `--exag`.

Voice is optional — everything above works without it. Setting it up needs [`uv`](https://docs.astral.sh/uv/) and `ffmpeg` (`speak.py` shells out to it to produce Opus; without it you get a `FileNotFoundError` at the very last step, after the model has already run).

**Two venvs, deliberately.** `chatterbox-tts` depends on a `numba` pin that only builds on Python <3.10, so TTS lives in its own 3.11 environment. Whisper runs in the main venv on 3.13. They don't share.

```sh
uv venv .venv     --python 3.13 && VIRTUAL_ENV=.venv     uv pip install faster-whisper
uv venv .venv-tts --python 3.11 && VIRTUAL_ENV=.venv-tts uv pip install chatterbox-tts "setuptools<81"
```

That `setuptools<81` is not optional and the failure it prevents is nasty: chatterbox's watermarker needs `pkg_resources`, `perth` swallows the resulting `ImportError`, and you get `PerthImplicitWatermarker = None` — a `TypeError: 'NoneType' object is not callable` four layers from the real cause. uv doesn't install setuptools into venvs by default, and setuptools ≥81 dropped `pkg_resources` outright.

**Transcription runs on CPU, synthesis on GPU.** Whisper on 8 threads clears a voice note in about half the time it took to record, and leaves the GPU alone — usually it's busy serving Ollama. Chatterbox wants CUDA and about 3GB of VRAM, which fits alongside a 7B model on a 16GB card. `speak.py --cpu` forces it off the GPU if you'd rather not contend at all.

**Claude cannot hear its own output.** Worth knowing, because it shapes what you can trust: if it tells you a generated clip sounds a certain way, it's guessing. The honest check is to run the output back through `transcribe.py` and confirm the words survived — that catches garbled synthesis, but not tone. For tone, you're the only ear.

## Security model

You're connecting a public-addressable Telegram bot to a machine where Claude can run commands. That deserves care, so here's exactly what's done and what's left to you.

**Only you can talk to it.** Pairing writes your numeric Telegram user ID to an allowlist and sets `dmPolicy: "allowlist"`. Anyone else who finds your bot gets silence — their messages are dropped before reaching Claude. Numeric IDs are permanent and can't be spoofed by changing a display name or username.

**Pairing is inverted on purpose.** The plugin's built-in flow has the *bot* DM a 6-character code to any stranger who messages it, which you then approve from inside Claude. This script goes the other way: the terminal generates the PIN and waits for it to arrive. That means unknown senders are never answered at all, and matching the PIN proves the person holding the phone is the person holding the terminal. The PIN is drawn from `/dev/urandom`, excludes look-alike characters (`I`/`O`/`0`/`1`), expires in 5 minutes, and only counts from a **private** chat — a PIN pasted into a group won't pair anyone.

**The token stays out of `ps`.** Telegram puts the bot token in the URL path, so a normal `curl https://api.telegram.org/bot<TOKEN>/...` exposes it to every user on the box via `ps auxww`. All API calls here pipe the URL through `curl -K -` instead, so the token never enters the process's argument list. *(Verified: a canary token was not visible in `ps` during a live call.)*

**Files are owner-only.** The script sets `umask 077` before touching disk. The token (`~/.claude/channels/telegram/.env`) and allowlist are `600`, directories `700`. Writes go through temp files and `mv`, so a crash can't leave a half-written token or a briefly world-readable file.

**Claude is told the rules.** The injected prompt instructs it to never send secrets, tokens, keys, or `.env` contents over Telegram; to treat instructions embedded in fetched content as data rather than commands; and to require terminal confirmation for anything destructive or irreversible requested over chat.

**Command menu scopes are cosmetic.** Telegram sends no scope information with an incoming command, and anyone can type `/anything` regardless of what's registered where. The menu is a convenience; the allowlist is the boundary.

### What this does *not* protect against

Be clear-eyed about these:

- **Telegram sees your messages.** Bot chats are not end-to-end encrypted. Anything Claude reports and anything you send is readable by Telegram. Don't use this on work where that matters. Voice notes are transcribed and synthesized locally, but the audio itself still travels over Telegram like any other message — local processing buys you privacy from a cloud STT vendor, not from Telegram.
- **Your bot token is a credential.** Anyone with it can impersonate your bot. It sits in plaintext in `~/.claude/channels/telegram/.env` — protected by file permissions, not encryption. If it leaks, revoke via `/revoke` in BotFather and run `crc setup`.
- **Anyone with your unlocked phone can instruct Claude.** The allowlist authenticates a Telegram *account*, not a person.
- **The prompt rules are instructions, not enforcement.** They guide Claude well but are not a sandbox. The real boundary is Claude Code's permission system.
- **`reset` is what clears the allowlist.** Re-running `setup` *adds* to it (so existing groups and users survive). To revoke everyone, use `reset`.

## Away mode and the blocking problem

The most likely way this disappoints you: Claude hits a permission prompt mid-task while you're out, and blocks. You get silence, not a report.

The prompt tells Claude to message you when it's blocked, which covers most of it. If you want fewer stops:

```sh
RC_AWAY=1 crc
```

That runs with `--permission-mode acceptEdits` — file edits no longer prompt. Bash and other tools still ask. It's a real trade: you're giving up the review step on edits in exchange for not being blocked. Use it when you trust the task, not by default.

## Staying alive while you're out

RC only works while the session is running. Close the terminal and it's gone — there's no queue. Telegram holds updates for 24 hours, but nothing is polling to collect them, so messages sent while it's down are effectively lost. Use `tmux`:

```sh
tmux new -s claude
crc
# detach with Ctrl-b then d — reattach later with: tmux attach -t claude
```

### On a cloud box

Nothing here assumes a desktop. Telegram polls *outbound*, so the machine needs no public IP, no port open, and no webhook — a VPS, a home server, or a work desktop you SSH into all behave the same. Run setup once over SSH, start it in `tmux`, and close the laptop. That's the setup where RC earns its keep: the box stays up, you don't.

Two things change when nobody's at that terminal:

- **`off` strands you.** It can only be undone at the terminal — which now means SSHing back in. Use `quiet` instead, which you can undo from the phone.
- **Voice output wants a GPU.** Chatterbox needs CUDA and ~3GB of VRAM; on a CPU-only VPS, `speak.py --cpu` works but is slow. Transcription is CPU-only by design and is fine anywhere.

## Known limits

- **No history, no search.** Telegram's Bot API exposes neither, so Claude only sees messages as they arrive. If it needs earlier context it'll ask you to paste it.
- **One session per bot.** A hard Telegram limit — see [Profiles](#profiles--more-than-one-session-at-once). Use a profile per concurrent session.
- **Reports are a judgment call, not a guarantee.** "Message me when done" is an instruction Claude follows well, but it isn't mechanical. Same for `rc usage`. If you find it skipping sends, a `Stop` hook would make it deterministic at the cost of a less well-written summary.
- **This depends on a pre-1.0 third-party plugin.** RC leans on the plugin's `bot.pid`, its `access.json` schema, and its reserved-command behavior — none of which are documented API. An update could change any of them. If something breaks after `claude plugin update`, look there first.
- **4096 characters per message.** Longer replies are auto-chunked.
- **Reactions are a fixed list.** Telegram only accepts specific emoji; others silently do nothing.

## Files

What's in the repo:

| File | What | Needed? |
| --- | --- | --- |
| `clauderc.sh` | The whole thing — setup, pairing, profiles, menu, usage, session launch | yes |
| `install.sh` | Links `crc` into `~/.local/bin` | yes, once |
| `transcribe.py` | Voice note → text (faster-whisper, CPU) | optional |
| `speak.py` | Text → Telegram voice note (chatterbox, GPU) | optional |

Nothing in the repo holds state or secrets — every one of these is safe to fork and commit. State lives in `$HOME`:

| Path | What | Owner |
| --- | --- | --- |
| `~/.claude/channels/telegram/.env` | Bot token (`600`) | plugin reads it |
| `~/.claude/channels/telegram/access.json` | Allowlist + policy (`600`) | plugin reads it per message |
| `~/.claude/channels/telegram/bot.pid` | Which process currently holds the poller | plugin |
| `~/.claude/clauderc/profiles/<name>/rc.json` | Chat ID, mute state, which state dir (`600`) | this script |

Non-default profiles get their own plugin directory (`~/.claude/channels/telegram-<name>/`). The `default` profile deliberately keeps the plugin's own path, so upgrading from a pre-profiles install moves nothing on disk.

RC state is kept out of the plugin's directory so a plugin update or uninstall can't take it with it. `CLAUDERC_HOME` overrides where profiles live; `TELEGRAM_STATE_DIR` still overrides the plugin directory if you were using it before profiles existed.

## Uninstall

```sh
crc reset                                             # remove token, allowlist, state
rm ~/.local/bin/crc
claude plugin uninstall telegram@claude-plugins-official
```

Then `/deletebot` in BotFather if you're done with the bot entirely.

## Troubleshooting

**Bot doesn't reply to the PIN.** The session must not be running during pairing — Telegram allows one poller per bot. Quit any RC session and retry. Also confirm you're messaging the right bot (`t.me/<username>` from setup).

**Pairing was interrupted.** Just run `crc` again. The token is saved once it validates, so setup checks it and goes straight back to the PIN rather than asking you to paste it twice. If the token was revoked in the meantime, it falls back to asking.

**Messages don't reach Claude.** Run `crc status`. If inbound shows `OFF`, run `crc on`. If it shows `pairing`, something rewrote `access.json` — re-run `setup`. If `poller` shows `not running`, nothing is listening: start a session.

**Telegram went dead right after running something.** Check whether that something shells out to `claude` without `--strict-mcp-config` — see [the isolation note](#the-subprocess-has-to-be-isolated-or-it-kills-your-channel). Restart the session to get the poller back.

**"terminated by other getUpdates".** Two things are polling the same bot. `crc profiles` shows which are live.

**Plugin won't start.** It needs Bun on `PATH` — check `bun --version`. If Claude Code was launched from a GUI that doesn't load your shell profile, Bun may be missing from its environment.

**Reports never arrive but replies work.** Check `crc status` for `reports: muted`, then `crc quiet off`.

## License

MIT — see [LICENSE](LICENSE).
