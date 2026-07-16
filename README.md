<div align="center">

# Agent Babysitter

### Leave your desk. Claude Code keeps working, and tells you how it went.

Agent Babysitter watches your Claude Code session and messages your phone over
Telegram — so you can walk away, and steer it back with a reply.

[![License: MIT](https://img.shields.io/badge/license-MIT-3da639.svg)](LICENSE)
[![Shell: Bash](https://img.shields.io/badge/shell-bash-4eaa25.svg?logo=gnubash&logoColor=white)](abs.sh)
[![For: Claude Code](https://img.shields.io/badge/for-Claude%20Code-d97757.svg)](https://claude.com/claude-code)

**[Quick start](#quick-start)** · [Features](#features) · [Commands](#commands) · [Security](SECURITY.md) · [License](#license)

</div>

<img src="https://raw.githubusercontent.com/Pranjalab/AgentBabysitter/main/assets/bridge.png" alt="A terminal running abs on the left, a Telegram conversation on the right, joined by a single line labelled one session, not a copy.">

Start Claude with `abs` instead of `claude`. Your terminal works exactly as
normal — but now a task finishing sends a short report to your phone, your reply
goes straight back into the *same* live session, and you can check your usage or
send a screenshot without touching the keyboard.

## Why I built it

Three ordinary frustrations, all from using Claude Code every day:

1. **I was chained to my desk.** The moment I walked away, I'd start worrying —
   is it waiting on me to approve something? Has it finished? Did it go the wrong
   way ten minutes ago while I was making coffee? So I just… sat there.

2. **I kept checking my usage.** Before a big task I'd open the browser or the app
   *again* to see how much of my limit was left. Ten seconds, every time, and it
   broke my focus.

3. **Getting a screenshot into Claude Code was a pain.** Pasting an image into the
   terminal is awkward. I often had one on my phone — a broken UI, an error I'd
   photographed — and no clean way to hand it over.

Agent Babysitter fixes all three: it reports when work is done and takes your
reply back, shows your usage in the chat with one tap, and lets you send a photo
that Claude reads straight away.

## Quick start

```sh
git clone https://github.com/Pranjalab/AgentBabysitter
cd AgentBabysitter
./install.sh
abs
```

First run walks you through everything:

1. **Make a bot** — message [@BotFather](https://t.me/BotFather) on Telegram,
   send `/newbot`, and paste the token it gives you (it stays hidden, and never
   leaves your machine).
2. **Pair your phone** — `abs` prints a short PIN; send it to your new bot. That
   proves the phone is yours, and from then on the bot answers *only* you.

That's it. Claude Code starts, and you get a message the next time a task
finishes. After setup it's just `abs`, from whatever project you're working in —
setup is once per bot, not once per project.

**Prerequisites:** `claude`, `bun`, `jq`, `curl`. The installer checks for them
and tells you what's missing rather than installing anything behind your back.

## Features

**Reports when a task finishes.** A short summary lands on your phone — what
happened, and anything that needs you to decide.

**Reply to steer it.** Answer in plain English and it arrives in the live session
as if you'd typed it at the desk. Approve a step, change direction, ask a
question — all from the chat.

**Send a screenshot or photo.** Attach an image in Telegram and Claude reads it
directly — a failing screen, a stack trace you photographed, a design to match.
No more fighting to paste it into the terminal.

**Voice notes, both ways.** Send a voice note and it's transcribed; ask for the
answer spoken and it replies with a real voice message. Both run locally — no
audio leaves your machine.

<div align="center">
<img src="https://raw.githubusercontent.com/Pranjalab/AgentBabysitter/main/assets/voice-and-report.jpg" alt="A Telegram chat showing voice notes in both directions and a written task-done report from Claude." width="440">
</div>

**Check your usage from the phone.** Tap `/usage` and your subscription limits and
reset times come to the chat — no browser, no app.

<div align="center">
<img src="https://raw.githubusercontent.com/Pranjalab/AgentBabysitter/main/assets/usage-telegram.jpg" alt="The /usage report in Telegram: a headline percentage, a bar per limit, and the time until each resets." width="560">
</div>

**Run several projects at once.** Each project gets its own bot (a *profile*), so
you can babysit more than one session side by side — `abs --profile work`.

**A real on/off switch.** `abs quiet` mutes the reports but keeps listening;
`abs off` drops all inbound Telegram entirely.

## Commands

| Command | What it does |
| --- | --- |
| `abs` | Start a session (first run does setup) |
| `abs status` | What's paired, inbound state, whether it's live |
| `abs usage` | Your subscription limits, in the terminal and on Telegram |
| `abs profiles` | List your bots and which are in use |
| `abs quiet on` / `off` | Mute / unmute the reports (inbound still works) |
| `abs off` / `on` | Drop / re-enable all inbound Telegram |
| `abs say "text"` | Speak it and send as a voice note |
| `abs reset` | Remove this profile's token, allowlist, and state |
| `abs help` | The full list |

Anything else is passed straight to `claude`, so `abs --model opus` or
`abs --resume` work as you'd expect. You can also just say it in chat — "mute the
reports", "what's my usage" — and it runs the same commands.

For voice setup, profiles, running it on a server, and troubleshooting, see the
**[full guide](docs/GUIDE.md)**.

### What it won't do

Worth knowing up front, so nothing feels broken:

- **Telegram slash commands don't work** (except `/usage`). `/model`, `/stop` and
  friends are Claude Code terminal commands; over Telegram they arrive as plain
  text and nothing runs them.
- **You can't switch model mid-session.** Set it at launch: `abs --model opus`.
- **You can't interrupt a running task from the phone.** Messages are read
  between turns, not during one.

## How it works

Agent Babysitter is **glue, not a new system** — one bash script, no daemon, no
webhook. Anthropic ships an official Telegram plugin and Claude Code has a
`--channels` flag that feeds Telegram messages into a live session. Agent
Babysitter handles the parts around that: validating your bot token, pairing your
phone with a PIN, injecting the "report when done" behavior per session (your
project's `CLAUDE.md` is never touched), managing profiles, and reporting your
usage. Voice (`speak.py`, `transcribe.py`) is an optional local add-on.

## Security

**Only you can message your bot.** Pairing writes your Telegram ID to an
allowlist; anyone else who finds the bot is ignored before Claude ever sees the
message. The bot token is kept out of `ps` and stored owner-only.

It is not a sandbox, though — within your paired account, Claude has whatever
power Claude Code gives it. Read **[SECURITY.md](SECURITY.md)** for the full
model, including what it deliberately does *not* protect against.

## Acknowledgements

Agent Babysitter stands on two things it doesn't reinvent:

- **[Claude Code](https://claude.com/claude-code)** — the agent doing the actual
  work. Agent Babysitter just gives it a way to reach you.
- **Anthropic's official Telegram plugin** (`telegram@claude-plugins-official`) —
  it owns the inbound message polling and the `download_attachment` and `reply`
  tools this builds on.

## License

MIT — see [LICENSE](LICENSE). Do what you like with it.
