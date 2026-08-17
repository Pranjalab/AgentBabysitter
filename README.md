<div align="center">

<img src="https://raw.githubusercontent.com/Pranjalab/AgentBabysitter/main/assets/banner.jpg" alt="Agent Babysitter — remote-control and monitor Claude Code from Telegram. Leave your desk; Claude Code keeps working and messages your phone when it's done." width="100%">

### Remote-control and monitor Claude Code from Telegram

[![License: MIT](https://img.shields.io/badge/license-MIT-3da639.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/Pranjalab/AgentBabysitter?style=flat&color=d97757)](https://github.com/Pranjalab/AgentBabysitter/stargazers)
[![Shell: Bash](https://img.shields.io/badge/shell-bash-4eaa25.svg?logo=gnubash&logoColor=white)](abs.sh)
[![For: Claude Code](https://img.shields.io/badge/for-Claude%20Code-d97757.svg)](https://claude.com/claude-code)
[![Works over SSH](https://img.shields.io/badge/works-local%20·%20SSH%20·%20tmux-2aabee.svg)](docs/GUIDE.md)

</div>

Start a task, close the laptop, and let Claude keep coding. **Agent Babysitter**
messages your phone when work finishes, takes your reply straight back into the
**same live session**, and lets you send screenshots, use voice, and check your
usage — all from Telegram.

Claude Code already does the work. Agent Babysitter is the piece that lets you
walk away from it — a thin bash script wrapped around Anthropic's official
Telegram plugin. No daemon, no webhook, no second copy of your session.

<div align="center">

<a href="https://raw.githubusercontent.com/Pranjalab/AgentBabysitter/main/assets/demo.mp4"><img src="https://raw.githubusercontent.com/Pranjalab/AgentBabysitter/main/assets/demo.gif" alt="Agent Babysitter demo — one-line install, pair your phone with a PIN, then close the laptop and let Claude keep working" width="80%"></a>

<sub><strong>▶︎ 50-second demo</strong> — one-line install, pair your phone with a PIN, then close the laptop and let Claude keep working. <a href="https://raw.githubusercontent.com/Pranjalab/AgentBabysitter/main/assets/demo.mp4">Watch with audio →</a></sub>

</div>

---

## ✨ Why you'll love it

- 🔔 **Leave your desk** — get pinged when Claude finishes or needs a decision.
- 📱 **Reply from your phone** — your message lands in the same live session.
- 🖼 **Send screenshots** — attach an image and Claude reads it directly.
- 🎤 **Voice both ways** — send a voice note, or ask for the answer spoken back.
- 📊 **Check usage remotely** — your Claude limits, one tap away, no browser.
- 🔒 **Your own private bot** — PIN-paired, so only you can reach it.
- 🟢 **See the state at a glance** — dots in Claude's status bar answer one question per channel: if a reply happened right now, would it go out this way? Plus your 5-hour and weekly usage.
- 🖥 **Runs anywhere** — laptop, SSH, `tmux`, a headless Linux server.
- 🗂 **Multiple projects** — one bot per project, babysat side by side.

## 🚀 Quick start

Install it, then run `abs` instead of `claude`:

```sh
curl -fsSL https://agentbabysitter.com/install.sh | bash
abs
```

It installs a single script to `~/.local/bin/abs` and touches nothing else. Piping
to `bash` is your call to make — [read it first](https://agentbabysitter.com/install.sh)
if you'd rather.

Prefer Python packaging?

```sh
pipx install agent-babysitter     # or: pip install agent-babysitter
```

From source, or to contribute:

```sh
git clone https://github.com/Pranjalab/AgentBabysitter
cd AgentBabysitter
./install.sh
```

**About two minutes, once per bot:**

1. 🤖 **Create a bot** — message [@BotFather](https://t.me/BotFather), send
   `/newbot`, and paste the token back (it stays hidden and never leaves your
   machine).
2. 📲 **Pair your phone** — `abs` prints a short PIN; send it to your bot. That
   proves the phone is yours, and from then on the bot answers *only* you.
3. ✅ **Done** — Claude Code starts. Walk away; you'll get a message when a task
   finishes.

After setup it's just `abs`, from whatever project you're in. Setup is once per
bot, not once per project.

> **Prerequisites:** `claude`, `bun`, `jq`, `curl`. The installer checks for them
> and tells you what's missing rather than installing anything behind your back.

## 🧩 Features

### 🔔 Notifications — know when Claude needs you

A task finishing sends a short summary to your phone: what happened, and anything
that needs a decision. Stop babysitting the spinner; the spinner tells you when
it's done.

### 💬 Remote control — steer the same session from your phone

Reply in plain English and it arrives in the live session as if you'd typed it at
the desk. Approve a step, change direction, ask a question — the terminal and
Telegram are **one session**, not two conversations.

### 🖼 Screenshots — hand Claude an image without the terminal

Pasting an image into a terminal is awkward. Attach it in Telegram instead and
Claude reads it directly — a broken UI, a stack trace you photographed, a design
to match.

### 🎤 Voice — talk to Claude, and have it talk back

Send a voice note and it's transcribed; ask for the answer spoken and it replies
with a real voice message. Both run **locally** — no audio ever leaves your
machine.

Voice is an optional add-on (it pulls in Whisper + Chatterbox, a few GB), so the
installer offers it as a separate step — or turn it on any time with a single
command:

```bash
abs voice setup      # builds the local speech engines; abs voice status to check
```

Pick the **model** for speed vs. expression (`abs config voice standard|turbo` —
turbo generates ~1.8× faster), and **clone a voice** from any short reference clip
so replies speak in the voice you choose (`abs config voice-sample <clip>`, or
`--audio-prompt` per call).

**"Always answer me in voice" — as a setting, not a request.** Asking the
assistant to always reply with a voice note works until the session gets long and
the instruction drifts out of the model's attention. `abs config reply` stores it
instead, and the session hooks enforce it whether the model remembers or not:

Two switches, one per channel:

```sh
abs config reply-text on|off     # send replies as text  (default on)
abs config reply-voice on|off    # send replies as a voice note (default off)
```

Both on and **every finished result goes out as a voice note first, then the same
words as text**. Turn text off and the voice note *is* the reply.
(`abs config reply text|both|voice` still works as the one-line shorthand.)

The note leads because reading the message first makes the audio a duplicate of
something you already know. The cost is the wait: the words only exist once the
reply is written, so the text is held for the few seconds synthesis takes.
`abs config voice-first off` puts the text back in front.

Turning both off is refused — that isn't a delivery mode, it's silence, and it's
the one state where a message you were waiting for never arrives with nothing
saying why. `abs quiet on` already means "mute the reports", says so, and can be
undone from either side.

`voice` still lets a message through as text when it carries a code block, a link,
or an attachment: a voice note can't carry any of those, and a blocked message is
one you simply never get. Same reason it refuses to turn on at all where nothing
can speak — the failure mode is always "text as usual", never silence.

<div align="center">
<img src="https://raw.githubusercontent.com/Pranjalab/AgentBabysitter/main/assets/voice-and-report.jpg" alt="A Telegram chat showing voice notes in both directions and a written task-done report from Claude." width="440">
</div>

### 📊 Usage — check your Claude limits without leaving the chat

Tap `/usage` and your subscription limits and reset times come to Telegram — no
browser, no app, no breaking focus before a big task.

<div align="center">
<img src="https://raw.githubusercontent.com/Pranjalab/AgentBabysitter/main/assets/usage-telegram.jpg" alt="The /usage report in Telegram: a headline percentage, a bar per limit, and the time until each resets." width="560">
</div>

### 🗂 Profiles — babysit several projects at once

Each project gets its own bot, so you can run more than one session in parallel
without them fighting over messages — `abs --profile work`.

### ⌨️ A terminal that behaves like one

Every list `abs` offers — which bot, which session to resume, which project, which
sandbox — is an arrow-key menu: ↑/↓ (or `k`/`j`) move the highlight, Enter takes
it, `q` backs out. **Typing the number still works**, so nothing you already do
stops working, and the row you pick collapses to a single line so your scrollback
keeps the decision without the whole menu.

It degrades rather than breaks: no terminal, `TERM=dumb`, or `ABS_NO_TUI=1` all
fall back to the old numbered prompt — which is what keeps these usable under
`docker exec` without `-t`, over a pipe, and in CI.

### 🛑 Remote controls — a kill ladder that doesn't trust the model

Send any of these from Telegram **as a whole message** and the session hook acts
on it *itself* — so they work even if the model has been prompt-injected or turned
against you (it never runs them):

| Phrase | Does |
| --- | --- |
| `ABS MUTE` / `ABS UNMUTE` | Mute / resume proactive reports (catch-up on resume) |
| `ABS OFF` | Cut inbound **and** outbound Telegram; the session keeps working |
| `ABS STOP` | Halt the current plan at the next step and wait for a new instruction |
| `ABS EXIT` | Close the session (asks to confirm if mid-task); restart with `abs` |
| `ABS BLOCK` | Lock the bot out entirely until a deliberate `abs setup` |

And a **destructive-command guard**: when a turn is driven from Telegram, a
`PreToolUse` hook blocks a small, high-confidence set of dangerous commands
(`rm -rf`, `git push --force`, `DROP`/`TRUNCATE`, reading `.env`/keys, …) and tells
you to run them at the terminal — where you're proven to be at the desk. From the
terminal, nothing is blocked. Opt out with `abs config guard off`. Full model and
the honest limits in **[SECURITY.md](SECURITY.md)**.

### 🔒 Security — a private bot only you can reach

Pairing writes your Telegram ID to an allowlist; anyone else who finds the bot is
ignored before Claude ever sees the message. Full model in [SECURITY.md](SECURITY.md).

## 🤔 Why Agent Babysitter?

Claude Code *can* already talk to Telegram — Anthropic ships an official plugin
for it. But the bare plugin is just a pipe: it forwards messages and nothing
more. Agent Babysitter is the workflow around that pipe.

| | Official Telegram plugin, alone | With Agent Babysitter |
| --- | :---: | :---: |
| Chat with the session from Telegram | ✅ | ✅ |
| Guided bot setup & token validation | Manual | ✅ One command |
| Only you can reach the bot | Basic | ✅ PIN pairing + allowlist |
| "Task finished" reports to your phone | ❌ | ✅ |
| Check Claude usage from Telegram | ❌ | ✅ |
| Voice notes in and out, processed locally | ❌ | ✅ |
| Send screenshots Claude reads | Raw | ✅ Built in |
| Run multiple projects at once | ❌ Single bot | ✅ Profiles |
| Mute / hard-off controls | ❌ | ✅ |

It **complements** Claude Code — it doesn't replace or fork it. Your session,
your `CLAUDE.md`, and your permissions are all untouched.

## 🎯 Perfect for

- 🌙 **Overnight jobs** and long refactors you don't want to watch.
- 🛠 **Bug-fixing sessions** where you step away between turns.
- 🖥 **Remote servers** — run it over SSH on a VPS or home box and close the laptop.
- ☕ **Coffee breaks** and working from another room.
- 🗂 **Multi-project days** — a bot per project, all reporting to one phone.

## 💡 Why I built it

Three ordinary frustrations, all from using Claude Code every day:

1. **I was chained to my desk.** The moment I walked away I'd start worrying — is
   it waiting on me to approve something? Has it finished? Did it go the wrong way
   ten minutes ago while I was making coffee? So I just… sat there.
2. **I kept checking my usage.** Before a big task I'd open the browser *again* to
   see how much of my limit was left. Ten seconds, every time, breaking my focus.
3. **Handing Claude a screenshot was a pain.** Pasting into the terminal is
   awkward, and the image was usually on my phone anyway.

Agent Babysitter fixes all three — and it's stayed a single bash script the whole
way, because the point was to remove friction, not add a platform.

## ⌨️ Commands

```sh
abs                     # start a session (first run does setup)
abs --model opus        # any claude flag is passed straight through
```

| Command | What it does |
| --- | --- |
| 📋 `abs status` | What's paired, inbound state, whether it's live |
| 📊 `abs usage` | Your Claude limits — in the terminal and on Telegram |
| 🗂 `abs profiles` | List your bots and which are in use |
| ⚙️ `abs config model <name>` | Default model for new sessions (`--clear` to unset) |
| ⚙️ `abs config silent on` / `off` | Whether new sessions start muted |
| ⚙️ `abs config statusline on` / `off` | Bottom-bar Text/Voice/Daemon dots + usage (default on) |
| ⚙️ `abs config label <name>` | Name before the colon in the bar — `auto` takes your Claude one |
| ⚙️ `abs config usage-refresh <min>` | How often the usage glance refreshes (default 5) |
| ⚙️ `abs config guard on` / `off` | Block destructive commands on Telegram turns (default on) |
| ⚙️ `abs config voice standard` / `turbo` | Default TTS model — expressive vs. ~1.8× faster |
| ⚙️ `abs config voice-sample <file>` | Clone a voice for spoken replies (both models) |
| ⚙️ `abs config reply-text on` / `off` | Send replies as text (default on) |
| ⚙️ `abs config reply-voice on` / `off` | Send replies as a voice note (default off) |
| ⚙️ `abs config voice-first on` / `off` | In mode `both`: note first, then text (default on) |
| ⚙️ `abs config auto-silent on` / `off` | Pause reports while you're driving the terminal (default on) |
| 🔕 `abs quiet on` / `off` | Mute / unmute reports (inbound still works) |
| 🛑 `abs off` / `on` | Drop / re-enable all inbound + outbound Telegram |
| 🚪 `abs exit` | End the running session (restart with `abs`) |
| 🎤 `abs say [--turbo] "text"` | Speak it and send as a voice note (`--audio-prompt` to clone) |
| ♻️ `abs reset` | Remove this profile's token, allowlist, and state |
| ❓ `abs help` | The full list |

From **Telegram**, the hook-enforced kill ladder — sent as a whole message —
`ABS MUTE` / `ABS UNMUTE` · `ABS OFF` · `ABS STOP` · `ABS EXIT` · `ABS BLOCK`
(see [Remote controls](#-remote-controls--a-kill-ladder-that-doesnt-trust-the-model)).

You can also just say it in chat — "mute the reports", "what's my usage" — and it
runs the same commands. For voice setup, profiles, servers, and troubleshooting,
see the **[full guide](docs/GUIDE.md)**.

## 🛰 Always-on daemon (v3)

Without the daemon, `abs` is a passenger: when Claude Code isn't running, the bot
is deaf and nothing can be started remotely. **`absd`** is a small background
daemon (a systemd user service) that polls every one of your idle bots, so you can
**start a session from your phone** and pick it up at the desk later.

- **`ABS START`** from Telegram → pick a project (or ▶ **Resume** a recent one) →
  pick Normal / Away → the daemon launches Claude Code in a persistent, attachable
  session (herdr if installed, else tmux). It confirms with `abs attach <profile>`.
- **Messages while nothing runs are pooled**, never dropped; when you start a
  session they're offered to forward as its opening prompt — tick the ones you
  want with ☐/☑ buttons, or tap **Send all** for the lot.
- **It tells you when the session is stuck.** A session that stops to ask a
  question or wait for an approval would otherwise sit there silently; after ~20s
  blocked, your phone gets *"⏸ myrepo is waiting for input or approval"*. Needs
  herdr — tmux can't report agent status, so on tmux the feature is simply absent.
- **The terminal keeps working unchanged** — plain `abs` still launches at the
  desk, and now shows the same resume-first picker.
- **The status bar grows a third dot.** `● Daemon` is green while absd is watching
  this bot, so you can see at a glance that a message sent after this session ends
  will still land.

  All three read the same way — green means *this is live right now*:

  | Dot | Green when |
  | --- | --- |
  | `● Text` | `reply text on`, not quiet, bot not off |
  | `● Voice` | `reply voice on`, not quiet, bot not off, **and** this machine has TTS installed |
  | `● Daemon` | absd has refreshed this profile's status in the last 3 minutes |

  Voice is dim on a machine with no TTS even with the switch on — the switch is
  what you asked for, TTS is whether it can happen.

  The name before the colon is yours to set:

  ```sh
  abs config label auto      # → Pran:@yourbot   (from your Claude display name)
  abs config label Pran      # or say it directly
  abs config label --clear   # back to abs:
  ```

Setup (a checkout install):

```sh
abs daemon install                    # render + install the systemd user unit
systemctl --user enable --now absd    # start it, and on login
sudo loginctl enable-linger $USER     # survive logout (once)
abs project add ~/Projects/myrepo     # projects ABS START can offer
abs config workspace-root ~/Projects  # root for remote "New folder" starts
abs doctor                            # diagnose the whole stack
```

| Command | What it does |
| --- | --- |
| 🛰 `abs daemon install\|start\|stop\|status\|logs` | Manage the always-on daemon |
| 🩺 `abs doctor` | Diagnose the v2 deps + v3 daemon stack (read-only) |
| 📂 `abs project add\|list\|rm <dir>` | Projects the ABS START flow offers |
| 🌱 `abs config workspace-root <dir>` | Root for remote "New folder" starts |
| ⚙️ `abs config start-menu on\|off` | Resume-first picker on interactive launch |
| 🖥 `abs sessions` / `abs attach [profile]` | List / attach engine sessions |
| ▶️ `abs --resume` / `abs --new` | Skip the terminal start menu (resume top / fresh) |
| 🏖 `abs sandbox build\|create\|list\|start\|stop\|destroy` | Docker sandboxes ([below](#-sandboxes--let-it-build-without-letting-it-near-your-machine)) |
| 🤖 `abs restricted create\|login\|list\|start\|stop\|destroy` | The restricted assistant bot |

From **Telegram** (daemon-mode grammar, whole-message):
`ABS START` · `ABS STATUS` · `ABS POOL` · `ABS CLEAR POOL` · `ABS OFF` · `ABS BLOCK`
(also the `/abs_start`, `/abs_status`, `/abs_pool`, `/abs_exit` "/" menu aliases).
`ABS OFF`/`ABS BLOCK` stop the daemon for that bot; recover only at the terminal
(`abs on` / `abs setup`).

## 🏖 Sandboxes — let it build without letting it near your machine

`abs sandbox` gives a session its own Ubuntu container: the project lives in one
dedicated host folder (`~/Projects/sandboxes/<name>`, the *only* path the container
can see), Claude Code runs inside, and ports you ask for are published so you can
open `localhost:3000` in your own browser.

```sh
abs sandbox build                          # once — build the image
abs sandbox create web --ports 3000:3000   # a box with its own workspace
abs start sandbox web                      # a session INSIDE it
abs sandbox list | stop | destroy <name>
```

From Telegram, `ABS START` offers **🏖 Sandbox…** alongside your projects. No
`--privileged`, no host mounts beyond that one folder, no docker socket; your
Claude credentials are **copied in at create**, never mounted, so what happens in
the box stays in the box.

### 🤖 The restricted assistant

A second kind of bot: everyday questions, web lookups, notes and arithmetic — but
it will not write or run project code, and it cannot start or stop sessions.

```sh
abs restricted create assistant   # new bot + a dedicated, credential-free box
abs restricted login assistant    # log Claude in INSIDE the box (once)
abs restricted list | start | stop | destroy <name>
```

One switch turns on four layers: an injected system prompt that refuses code, the
Haiku model, a dedicated sandbox, and **no host credentials at all** — the box logs
in separately. Being honest about which of those matter: the prompt is bypassable,
and the real containment is the last two. The daemon keeps it alive, relaunching
if it dies and telling you once if it needs logging in again.

## 🏗 Architecture

Telegram polls *outbound*, so nothing listens on a port and no webhook is needed —
which is why it works the same on a laptop, over SSH, or on a headless server.

```mermaid
flowchart LR
    You["📱 You (Telegram)"] <--> Bot["Your private bot"]
    Bot <--> Plugin["Official Telegram plugin"]
    Plugin <--> ABS["Agent Babysitter (abs.sh)"]
    ABS <--> Claude["Claude Code session"]
    Claude --> Project[("Your project")]
```

Agent Babysitter validates your token, pairs your phone, injects the "report when
done" behavior per session, manages profiles, and reports usage. The plugin owns
the inbound polling; Claude Code does the work.

## 🔒 Security

**Only you can message your bot.** Pairing records your Telegram user ID in an
allowlist, and anyone else who finds the bot is ignored before Claude sees the
message. The PIN pairing proves the phone is yours; the bot token is kept out of
`ps` and stored owner-only.

It is **not a sandbox**, though — within your paired account, Claude has whatever
power Claude Code gives it on your machine. Read **[SECURITY.md](SECURITY.md)**
for the full model, including what it deliberately does *not* protect against.

## ❓ FAQ

**Does it expose my code?**
No. It's your own private bot, and only your paired account can reach it. One
honest caveat: Telegram itself sees the messages (they're not end-to-end
encrypted), so don't use it for work where that matters.

**Can anyone else message my bot?**
No. Unpaired accounts are dropped before Claude ever sees them.

**Does voice processing stay local?**
Yes. Transcription and speech both run on your machine — no cloud speech vendor is
involved. (The audio still travels over Telegram, like any message.)

**Can I run multiple projects?**
Yes — one bot per project, via [profiles](docs/GUIDE.md#profiles--more-than-one-session-at-once).

**Does it work over SSH / on a server?**
Yes. Telegram polls outbound, so no public IP, open port, or webhook is needed.
Run it in `tmux` and close the laptop.

**Does it require a server?**
No. It runs anywhere Claude Code runs — a server is just one option.

**Can I stop Claude remotely?**
Not yet. Messages are read between turns, so you can steer the next step but not
interrupt the current one.

**Which platforms are supported?**
Linux and macOS, both tested — including macOS's ancient bash 3.2, with no
Homebrew or GNU coreutils needed. On macOS, reset times show as clock times
(`resets Jul 17 at 2:40am`) rather than countdowns, since that needs GNU `date`.
Windows via WSL should work but is untested; reports welcome.

## 🙏 Acknowledgements

Agent Babysitter stands on two things it doesn't reinvent:

- **[Claude Code](https://claude.com/claude-code)** — the AI coding agent doing
  the actual work.
- **Anthropic's official Telegram plugin** (`telegram@claude-plugins-official`) —
  it owns the inbound polling and the `download_attachment` / `reply` tools this
  builds on.

## 🤝 Contributing

Issues, ideas, and pull requests are all welcome — bug reports and "it broke on my
setup" notes especially. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to test a
change, and [our Code of Conduct](CODE_OF_CONDUCT.md) for the ground rules.
Version history lives in [CHANGELOG.md](CHANGELOG.md).

If Agent Babysitter saves you from waiting around for Claude Code, a ⭐ genuinely
helps other developers find it.

## 📄 License

MIT — see [LICENSE](LICENSE). Do what you like with it.
