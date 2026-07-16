<div align="center">

# Agent Babysitter

### Leave your desk. Claude Code keeps working, and tells you how it went.

Remote-control a live Claude Code session from Telegram.<br>
Same session, same context — you just moved to the couch.

[![License: MIT](https://img.shields.io/badge/license-MIT-3da639.svg)](LICENSE)
[![Shell: Bash](https://img.shields.io/badge/shell-bash-4eaa25.svg?logo=gnubash&logoColor=white)](abs.sh)
[![For: Claude Code](https://img.shields.io/badge/for-Claude%20Code-d97757.svg)](https://claude.com/claude-code)

**[Quick start](#quick-start)** · [Features](#features) · [How it works](#what-this-actually-is) · [Voice](#voice-notes) · [Security](#security-model) · [Troubleshooting](#troubleshooting)

</div>

---

You already trust Claude Code to do the work. What you can't do is leave the room while it does.

That's the whole idea here. **Agent Babysitter watches the session so you don't have to** — it waits out the long task, messages your phone the moment it's done, and takes your reply straight back into the same live session. You go for the walk. It keeps an eye on things.

Two ordinary, daily reasons you're still at the desk:

**You can't walk away.** You kick off a ten-minute task and then sit there — not because the work needs you, but because you won't know when it lands, and if it goes the wrong way at minute two you can't say "no, do it the other way" from the kitchen. So no walk, no gym. You wait, watching a spinner do something you already understand.

**You keep checking your limits.** Open the browser, or the app, again, just to see how much usage is left before committing to something big. Ten seconds, and it breaks your focus every time.

Agent Babysitter fixes both. Start Claude with `abs` instead of `claude`. When a task finishes you get a short report on your phone; reply in plain English and Claude picks the instruction straight up. Send a voice note instead of typing, or ask for the answer read back as one. Tap `/usage` and your limits arrive in the chat — no browser, no app.

The terminal keeps working exactly as normal. Telegram and the terminal are the **same session**, not two conversations — so you can walk out mid-task, steer from your phone, come back, and keep typing where you left off. One bot per project, so a session each for the things you've got running at once.

<img src="https://raw.githubusercontent.com/Pranjalab/AgentBabysitter/main/assets/bridge.png" alt="Diagram: a terminal running abs on the left, a Telegram conversation on the right, joined by a single line labelled one session, not a copy.">

<sup>*Diagram, not a screenshot — it's the shape of the thing. Real screenshots are below.*</sup>

## Quick start

```sh
git clone https://github.com/Pranjalab/AgentBabysitter
cd AgentBabysitter
./install.sh
abs
```

First run wants a bot token from [@BotFather](https://t.me/BotFather) (`/newbot`, about a minute), then prints a PIN for you to send to your bot. That's the pairing. Claude starts, and you're done — details in [Install](#install).

After that it's just `abs`, from whatever project you want Claude to work in. Setup is once per bot, not once per project.

```sh
abs                         # start a session, babysitter active
abs --model haiku           # any claude flag passes through
abs usage                   # limits, in the terminal and on your phone
abs status                  # what's paired, what's live
```

## Features

| What | How it helps |
| --- | --- |
| **Reports when done** | A short summary on your phone when a task finishes — what happened, what needs deciding. |
| **Reply to steer** | Answer in plain English. It lands in the live session as if you'd typed it. |
| **`/usage` from the phone** | Subscription limits and reset times, without opening a browser. |
| **Voice notes, both ways** | Send one and it's transcribed; ask for a voice answer and it speaks back. Runs locally — no audio leaves your machine. |
| **Profiles** | Several sessions at once, one bot each. `abs --profile work` |
| **Quiet mode** | Mute the reports, keep the inbound. For when you want to send but not receive. |
| **A real off switch** | `abs off` drops *all* inbound Telegram, and can only be undone from the terminal. |
| **Away mode** | `ABS_AWAY=1 abs` stops file edits blocking on approval while you're out. |

### What it doesn't do

Worth knowing up front, because the gap between promise and behaviour is what makes a tool feel broken:

- **Slash commands don't work from Telegram** (except `/usage`). Claude Code's `/model`, `/stop`, `/compact` are terminal commands — over this bridge they arrive as plain text and nothing runs them. See [The command menu](#the-command-menu).
- **Model and effort are set at launch, not from the phone** — `abs --model haiku`. There's no mid-session switch, in any language, plain or otherwise.
- **You can't interrupt a running task from Telegram.** Messages are read between turns.
- **It's not a sandbox.** Anyone who can message the bot can instruct a session that runs commands on your machine. The allowlist is the whole security model — see [Security model](#security-model).

## What this actually is

This is **glue, not a new system**. Anthropic ships an official Telegram plugin (`telegram@claude-plugins-official`) and Claude Code has a `--channels` flag that pushes Telegram messages into a live session. The plugin owns all the inbound polling. Agent Babysitter does the parts it doesn't:

- Prompts for your bot token and validates it before saving.
- Pairs your Telegram account to the machine with a PIN, without you typing anything into Claude.
- Injects the "report when done, ask for feedback" behavior per-session, so your projects' `CLAUDE.md` stays untouched.
- Manages **profiles**, so you can run several sessions at once on different bots.
- Reports your **subscription limits** to the phone.
- Speaks and listens, if you want it to.
- Gives you a real on/off switch.

The whole thing is one bash script with no dependencies of its own. Voice (`speak.py`, `transcribe.py`) is an optional add-on — skip it and Agent Babysitter still works.

## Install

**Prerequisites:** `claude`, `bun`, `jq`, `curl`. The plugin's server runs on Bun — install with `curl -fsSL https://bun.sh/install | bash` if you don't have it. The installer checks and tells you what's missing rather than installing things behind your back.

```sh
git clone https://github.com/Pranjalab/AgentBabysitter
cd AgentBabysitter
./install.sh
```

That links `~/.local/bin/abs` to the checkout, so `git pull` updates the command too.

**1. Create a bot.** In Telegram, message [@BotFather](https://t.me/BotFather), send `/newbot`, pick a name and a username ending in `bot`. It gives you a token like `123456789:AAHfiqksKZ8...`.

**2. Run it** from whatever project you want Claude to work in:

```sh
cd ~/my-project
abs
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
| `abs` | Start a session with Agent Babysitter active (first run does setup) |
| `abs status` | Pairing, inbound state, mute state, whether the poller is live |
| `abs profiles` | List every bot and which ones are in use |
| `abs usage` | Subscription limits — to terminal and Telegram |
| `abs menu` | Re-register the `/` command menu |
| `abs say "text"` | Speak it and send as a voice note (needs `.venv-tts`) |
| `abs quiet on` | Mute reports — **inbound still works** |
| `abs quiet off` | Resume reports |
| `abs off` | Hard off — drop *all* inbound Telegram |
| `abs on` | Re-enable inbound |
| `abs setup` | Re-pair (reuses a working saved token — `reset` first to change it) |
| `abs reset` | Delete this profile's token, allowlist, and state |
| `abs help` | Command list |

Extra arguments pass straight through to `claude`:

```sh
abs --model opus
abs --resume
```

You can also just say it in chat — "rc quiet", "rc off", "rc status" — from Telegram or the terminal. Claude runs the same commands.

## Profiles — more than one session at once

**Telegram allows exactly one `getUpdates` poller per bot token.** That's a hard platform limit, not a design choice here: two sessions on one bot fight over every message and one loses with a `409 Conflict`. So concurrent sessions need **a bot each**.

A profile is one bot's pairing — its token, allowlist, and chat, kept together:

```sh
abs                        # picks the profile, or asks if you have several
abs --profile work         # use (or create) the 'work' bot
ABS_PROFILE=work abs  # same, from the environment
abs profiles               # who's who, and what's live
```

```
Agent Babysitter profiles
  default  @yourbot        live (pid 245324)
  work     @yourwork_bot   idle
```

Running `abs` with no arguments and more than one profile gives you a picker, with in-use profiles marked. Choose `n` to add a new bot — that's the "same token, or a new one?" decision, made per session rather than baked into config.

`abs` refuses to start a session on a profile that's already being polled, and tells you which one. Reusing a single bot across two *simultaneous* sessions isn't supported because Telegram won't allow it; sequential reuse is fine.

## From the phone

### The command menu

The `/` menu has exactly one entry:

| Command | What it does |
| --- | --- |
| `/usage` | Subscription limits and reset times |

That's not an oversight, and this section used to promise nine more.

**Slash commands don't work from Telegram.** The plugin handles `/start`, `/help` and `/status` itself, and answers them without Claude ever seeing them. It has no handler for anything else, so `/model`, `/stop`, `/compact`, `/effort` and friends fall through to `bot.on('message:text')` and arrive at Claude as ordinary text that nothing executes. They aren't commands over this bridge; they're just words.

`/usage` is the exception, and only because the injected prompt tells Claude to run `abs usage --send` when it sees it. That's an instruction to a model, not a wired handler.

Earlier versions advertised the full list because `register_commands` mirrored Telegram's *default* scope, believing it tracked "whatever Claude Code registers." Claude Code registers nothing — those entries had been written by an earlier version of this same script, so the mirror was reading its own output back and trusting it. Tapping one looked precisely like a broken bridge. **A menu that lies is worse than a menu with one honest entry**, so now it registers that one entry explicitly, at chat scope (which outranks the `all_private_chats` scope the plugin rewrites on every start).

**Plain English is what works** — but for *instructions*, not controls. "Fix the failing test and rerun it", "explain what you just changed", "stop after this file and report" all land as real work, because they're things Claude does with tools it has.

What plain English can't do is conjure a tool that doesn't exist. "Switch to opus" fails exactly like `/model` fails, and for the same reason: there's no mid-session model switch to call. Asking politely doesn't help. Neither does asking twice. Relaunch instead:

```sh
abs --model sonnet              # or opus, haiku
abs --permission-mode plan      # or auto, manual, acceptEdits
```

Extra arguments pass straight through to `claude`, so anything the CLI accepts works here.

### Why there's no button bar

There was one — a reply keyboard pinned above the input, with `[/model opus]`, `[/stop]` and friends. It worked, and it's gone anyway: it ate a third of a phone screen to duplicate what the `/` menu already does in one tap. If you're tempted to add it back, two findings from that build are worth keeping:

- **Inline buttons cannot work here** (the tidier kind, attached under a message). They deliver taps as `callback_query` updates, and only the plugin polls this bot. Its `reply` tool has no `reply_markup` parameter, and its callback handler ignores any data that isn't its own permission prompt — so they'd be silently tap-dead, with no error to explain why. Reply-keyboard taps arrive as ordinary text, which the plugin already forwards. That was the seam.
- **Reply-keyboard labels *are* the payload.** A `KeyboardButton` sends its label verbatim; there's no separate data field. A prettier `[🧠 Opus]` sends the literal text `🧠 Opus`, which doesn't start with `/`, so Claude Code never parses it as a command. The labels have to be the commands, which is exactly why the bar looked like a wall of slash-commands.

Telegram stores a reply keyboard client-side per chat, so deleting the code doesn't clear it. `abs menu` sends a `remove_keyboard` to sweep up any bar left over from an older version.

### Two kinds of off, and why

**`quiet`** stops the reports but keeps listening. This is almost always what you want. Because inbound still works, **you can unmute from your phone.**

**`off`** drops everything inbound. It's a genuine kill switch — the plugin re-reads its config on every message, so it takes effect instantly with no restart. The catch: once inbound is dead, Telegram can't turn it back on. **`off` can only be undone at the terminal.** Don't run it and then walk away.

## Usage limits

`abs usage` reports how much of your subscription you've burned and when it resets — the thing you actually want to know before starting a long task from your phone.

```sh
abs usage            # print, and send to Telegram
abs usage --print    # terminal only
abs usage --send     # Telegram only
```

```
🟡 Claude usage — 87% on your tightest limit

5-hour session
  ●●●○○○○○○○ 27%  · resets in 3h 55m (Jul 16, 6:20am)

Week (all models)
  ●●●●●●●●●○ 87%  · resets in 15h 5m (Jul 16, 5:30pm)
```

<div align="center">
<img src="https://raw.githubusercontent.com/Pranjalab/AgentBabysitter/main/assets/usage-telegram.jpg" alt="The /usage report as it arrives in Telegram: a green headline, one bar per limit, and the time until each resets." width="620">
</div>

**Where the numbers come from, and why that matters.** There is no `claude usage` subcommand and no public REST endpoint for this. The only non-interactive source is `claude -p "/usage"` — the same client-side slash command the TUI runs — so `abs` drives that and parses the text. That means **it is parsing a human-readable format that Anthropic can change without warning.** It's written to degrade rather than lie: an unparseable reset stamp falls back to printing the raw stamp, and output that doesn't match at all exits with an error and dumps what it saw. If it ever breaks, that's the first place to look.

Which per-model lines appear is Anthropic's call, not this script's — it reports whichever it finds.

**One number is inherited rather than read, and you should know which.** A limit you haven't touched arrives with no reset window at all — `Current week (Fable): 0% used`, and nothing more. Since the weekly limits share a window (when a per-model line *does* carry a stamp, it's the same stamp as all-models), a stampless weekly row borrows the all-models one instead of showing a bar with no reset. If Anthropic ever gives a model its own weekly window, that's the first line to distrust. Drop the all-models line and the borrowed value disappears with it rather than going stale.

The bar characters are `●`/`○` because `░` is a *hatched* cell — correct, but at phone size a row of them reads as broken glyphs instead of an empty track. `ABS_BAR_FULL` and `ABS_BAR_EMPTY` override them.

### The subprocess has to be isolated, or it kills your channel

`abs usage` runs `claude --strict-mcp-config -p "/usage"`. **That flag is load-bearing.**

Without it, the subprocess loads every globally-enabled plugin — including the Telegram one. The plugin's server enforces Telegram's one-poller-per-token rule by `SIGTERM`ing whatever pid is in `bot.pid` when it boots, then removes the file when it exits a second later. Net effect: **asking for usage from Telegram kills the channel that would deliver the answer**, and nothing restarts it.

Not theoretical — measured, by running both against a live poller:

| run | flag | poller |
| --- | --- | --- |
| `abs usage` | `--strict-mcp-config` | survived, same pid |
| `claude -p …` | none | killed, `bot.pid` gone |

`--strict-mcp-config` makes the subprocess load no MCP servers at all, so it never touches `bot.pid`. **If you add anything else that shells out to `claude`, it needs the same flag.**

## Voice notes

Send a voice note and Claude transcribes it. Ask for a reply in voice and it speaks back. Both run locally — no audio leaves the machine for transcription or synthesis.

```sh
abs say "text"                                         # speak it AND send it (the usual way)
.venv/bin/python transcribe.py <file.oga>              # speech → text (faster-whisper)
.venv-tts/bin/python speak.py "text" out.ogg           # text → speech (chatterbox), file only
```

**Inbound.** A voice note arrives with `attachment_file_id` on the `<channel>` tag. Claude fetches it with the plugin's `download_attachment` tool and runs `transcribe.py` on it, then treats the transcript as if you'd typed it. The injected prompt tells it to do this, so "send a voice note" needs no ceremony at your end.

**Outbound.** `abs say` is the one to reach for: it synthesizes *and* delivers the voice bubble. `speak.py` only writes a file.

That split exists for a reason worth knowing. The plugin's `reply` tool attaches any non-image as a **document** — so a generated `.ogg` shows up as a file to download rather than a bubble with a waveform. Only the Bot API's `sendVoice` gives you the real thing, and that needs the token, so it lives in `abs.sh` next to it rather than in a Python script that would need its own copy.

```sh
abs say "the text to speak"
abs say --keep out.ogg "text"     # also keep the file
abs say - < story.txt             # read stdin
```

`speak.py --exag` is an emotion dial: `0.3` flat, `0.5` natural, `0.8+` animated. Lower `--cfg` slows the delivery, which pairs well with a high `--exag`.

**Long text is chunked, and that's load-bearing.** One `generate()` call stops at chatterbox's token cap — roughly 40 seconds of speech — and returns the short clip with *no error and no flag*. A story hands back its opening and silently loses its ending, and nothing downstream can distinguish that from a genuinely short line. It's the worst shape of bug: the failure looks like success. `speak.py` splits at sentence boundaries under `--max-chars` (default 220), generates each, and stitches with `--gap` between paragraphs, so every seam lands where a reader would breathe.

Voice is optional — everything above works without it. Setting it up needs [`uv`](https://docs.astral.sh/uv/) and `ffmpeg` (`speak.py` shells out to it to produce Opus; without it you get a `FileNotFoundError` at the very last step, after the model has already run).

**Two venvs, deliberately.** `chatterbox-tts` depends on a `numba` pin that only builds on Python <3.10, so TTS lives in its own 3.11 environment. Whisper runs in the main venv on 3.13. They don't share.

```sh
uv venv .venv     --python 3.13 && VIRTUAL_ENV=.venv     uv pip install faster-whisper
uv venv .venv-tts --python 3.11 && VIRTUAL_ENV=.venv-tts uv pip install chatterbox-tts "setuptools<81"
```

That `setuptools<81` is not optional and the failure it prevents is nasty: chatterbox's watermarker needs `pkg_resources`, `perth` swallows the resulting `ImportError`, and you get `PerthImplicitWatermarker = None` — a `TypeError: 'NoneType' object is not callable` four layers from the real cause. uv doesn't install setuptools into venvs by default, and setuptools ≥81 dropped `pkg_resources` outright.

**Transcription runs on CPU, synthesis on GPU.** Whisper on 8 threads clears a voice note in about half the time it took to record, and leaves the GPU alone — usually it's busy serving Ollama. Chatterbox wants CUDA and about 3GB of VRAM, which fits alongside a 7B model on a 16GB card. `speak.py --cpu` forces it off the GPU if you'd rather not contend at all.

**Claude cannot hear its own output.** Worth knowing, because it shapes what you can trust: if it tells you a generated clip sounds a certain way, it's guessing. The honest check is to run the output back through `transcribe.py` and confirm the words survived — that catches garbled synthesis and truncation, but not tone. For tone, you're the only ear.

That check isn't hypothetical. It's what caught the truncation bug above: a story came back 30.5s instead of 37s, and the transcript ended mid-sentence with the closing line gone. Nothing else would have noticed.

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
- **Your bot token is a credential.** Anyone with it can impersonate your bot. It sits in plaintext in `~/.claude/channels/telegram/.env` — protected by file permissions, not encryption. If it leaks, revoke via `/revoke` in BotFather and run `abs setup`.
- **Anyone with your unlocked phone can instruct Claude.** The allowlist authenticates a Telegram *account*, not a person.
- **The prompt rules are instructions, not enforcement.** They guide Claude well but are not a sandbox. The real boundary is Claude Code's permission system.
- **`reset` is what clears the allowlist.** Re-running `setup` *adds* to it (so existing groups and users survive). To revoke everyone, use `reset`.

## Away mode and the blocking problem

The most likely way this disappoints you: Claude hits a permission prompt mid-task while you're out, and blocks. You get silence, not a report.

The prompt tells Claude to message you when it's blocked, which covers most of it. If you want fewer stops:

```sh
ABS_AWAY=1 abs
```

That runs with `--permission-mode acceptEdits` — file edits no longer prompt. Bash and other tools still ask. It's a real trade: you're giving up the review step on edits in exchange for not being blocked. Use it when you trust the task, not by default.

## Staying alive while you're out

RC only works while the session is running. Close the terminal and it's gone — there's no queue. Telegram holds updates for 24 hours, but nothing is polling to collect them, so messages sent while it's down are effectively lost. Use `tmux`:

```sh
tmux new -s claude
abs
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
| `abs.sh` | The whole thing — setup, pairing, profiles, menu, usage, session launch | yes |
| `install.sh` | Links `abs` into `~/.local/bin` | yes, once |
| `transcribe.py` | Voice note → text (faster-whisper, CPU) | optional |
| `speak.py` | Text → Telegram voice note (chatterbox, GPU) | optional |

Nothing in the repo holds state or secrets — every one of these is safe to fork and commit. State lives in `$HOME`:

| Path | What | Owner |
| --- | --- | --- |
| `~/.claude/channels/telegram/.env` | Bot token (`600`) | plugin reads it |
| `~/.claude/channels/telegram/access.json` | Allowlist + policy (`600`) | plugin reads it per message |
| `~/.claude/channels/telegram/bot.pid` | Which process currently holds the poller | plugin |
| `~/.claude/abs/profiles/<name>/rc.json` | Chat ID, mute state, which state dir (`600`) | this script |

Non-default profiles get their own plugin directory (`~/.claude/channels/telegram-<name>/`). The `default` profile deliberately keeps the plugin's own path, so upgrading from a pre-profiles install moves nothing on disk.

RC state is kept out of the plugin's directory so a plugin update or uninstall can't take it with it. `ABS_HOME` overrides where profiles live; `TELEGRAM_STATE_DIR` still overrides the plugin directory if you were using it before profiles existed.

## Uninstall

```sh
abs reset                                             # remove token, allowlist, state
rm ~/.local/bin/abs
claude plugin uninstall telegram@claude-plugins-official
```

Then `/deletebot` in BotFather if you're done with the bot entirely.

## Troubleshooting

**Bot doesn't reply to the PIN.** The session must not be running during pairing — Telegram allows one poller per bot. Quit any RC session and retry. Also confirm you're messaging the right bot (`t.me/<username>` from setup).

**Pairing was interrupted.** Just run `abs` again. The token is saved once it validates, so setup checks it and goes straight back to the PIN rather than asking you to paste it twice. If the token was revoked in the meantime, it falls back to asking.

**Messages don't reach Claude.** Run `abs status`. If inbound shows `OFF`, run `abs on`. If it shows `pairing`, something rewrote `access.json` — re-run `setup`. If `poller` shows `not running`, nothing is listening: start a session.

**Telegram went dead right after running something.** Check whether that something shells out to `claude` without `--strict-mcp-config` — see [the isolation note](#the-subprocess-has-to-be-isolated-or-it-kills-your-channel). Restart the session to get the poller back.

**"terminated by other getUpdates".** Two things are polling the same bot. `abs profiles` shows which are live.

**Plugin won't start.** It needs Bun on `PATH` — check `bun --version`. If Claude Code was launched from a GUI that doesn't load your shell profile, Bun may be missing from its environment.

**Reports never arrive but replies work.** Check `abs status` for `reports: muted`, then `abs quiet off`.

## License

MIT — see [LICENSE](LICENSE).
