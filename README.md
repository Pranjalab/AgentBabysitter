# cldx — your remote control for Claude Code

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Version](https://img.shields.io/badge/version-1.0.4-brightgreen.svg)](#release-104)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-508%20passing-brightgreen.svg)]()
[![Status](https://img.shields.io/badge/status-beta-yellow.svg)]()

> **cldx is a layer on top of [Claude Code](https://docs.claude.com/en/docs/claude-code) that auto-approves safe tool calls and lets you supervise Claude from your phone via Telegram. Step away from your laptop. Come back to a finished feature.**

---

## Why this exists

### 1. Auto-approval — stop babysitting your AI

Claude Code is brilliant. It's also chatty: every `Bash`, every `Write`, every `Edit` pops a "Do you want to proceed?" menu. If you're a developer who writes a precise prompt and just wants the **tested, summarised result**, those approval clicks add up to dozens of context-switches per task.

cldx classifies every approval by **tool category** and **risk level**, then auto-approves the safe ones based on a policy *you* control. `Read`, `Grep`, and `Glob` get a green pass. `Write` and `Edit` go through a configurable 2-second countdown (so you can still cancel). `rm -rf`, `dd`, `sudo`, and writes under `/etc` or `~/.ssh` always escalate to a human — no policy can override that.

You stop clicking. Claude keeps moving. You see the final result.

### 2. Telegram bridge — step away from the laptop

Even with auto-approval, *some* things need a human call. The destructive ones. The genuinely ambiguous ones. That used to mean staying glued to your terminal.

cldx forwards those approvals to **Telegram** — the cheapest, most universal messaging bridge that runs on every phone. Go to the gym. Take a walk. Get coffee. When Claude hits a real decision point, your phone buzzes with a formatted card showing the tool, the args, the risk level, and a `y / n / 1 / 2 / 3 / free-text` reply protocol. You answer; Claude continues.

You get the agentic-coding life back. The model works while you live.

---

## What it does, in one screen

```
┌────────────────────|  Claude + TMUX + Telegram  |─────────────────────┐
│ ❯ Add an OAuth flow to the user signup                                │
└───────────────────────────────────────────────────────────────────────┘
[14:32] → injected: Add an OAuth flow to the user signup

╭──────── ⏳ AUTO-APPROVE — firing in 2.0s ────────╮
│ type    approval_menu                            │
│ tool    ✏️ Write · write · elevated              │
│ args    src/auth/oauth.py                        │
│ profile auto-approve                             │
│         type to override · /skip to leave to you │
╰──────────────────────────────────────────────────╯
[14:32] → sent option 1 (Yes)

(4 more auto-approvals happen silently...)

╭──────────── ✓ Task complete ────────────╮
│ ⏺ Write(src/auth/oauth.py)              │
│   ⎿ Wrote 142 lines                     │
│ ⏺ Edit(src/auth/__init__.py)            │
│   ⎿ Updated 3 lines                     │
│ ⏺ Bash(pytest tests/test_oauth.py)      │
│   ⎿ 8 passed in 0.3s                    │
│ ⏺ Added OAuth signup. All tests pass.   │
│                                         │
│ Telegram: ✓ sent                        │
╰─────────────────────────────────────────╯
```

Meanwhile on your phone:

```
✅ cldx — task complete
━━━━━━━━━━━━━━━━━━━━
📌 Task: Add OAuth flow to user signup
📝 Summary: Added OAuth signup + tests (8 passing).
⏱️  Duration: 47s
⚙️  Profile: auto-approve

💬 Reply with your next task, or /help for options.
```

---

## Install

```bash
git clone https://github.com/Pranjalab/cldx.git
cd cldx
./install.sh
```

The installer:

1. Picks a compatible Python (≥ 3.11; tries 3.13 → 3.12 → 3.11 in that order).
2. `pip install --user .` so the `cldx` command lands in your user-scripts dir.
3. Creates `~/.cldx/{config,sessions,logs}/` with bundled defaults (existing files are left untouched).
4. Tells you the exact `export PATH=...` line to add to `~/.zshrc` if `cldx` isn't on PATH yet.
5. Detects stale binaries from a different Python version and offers the one-line `ln -sf` to fix it.

**Override the state location** with `export CLDX_HOME=/some/path`. **Uninstall** with `./install.sh --uninstall` (leaves `~/.cldx/` in place so a re-install keeps your configs).

### Optional setup wizard

```bash
cldx setup          # interactive — picks LLM backend + Telegram in sequence
cldx setup telegram # just the Telegram bridge
cldx setup llm      # just the LLM picker
cldx config         # show current secrets (masked)
```

The Telegram wizard walks you through `@BotFather`, auto-discovers your `chat_id`, sends a greeting message, and prints the slash-commands list to your phone.

### Pluggable LLM backends

cldx uses an LLM to **summarise** Claude's output before sending to Telegram (so you get one paragraph instead of 200 lines of tool output). Backends ship pluggable:

| Backend | Cost | Setup command |
|---|---|---|
| **Anthropic** (direct API key) | ~$0.0001 / summary (Haiku) | `cldx setup anthropic` |
| **AWS Bedrock** (bearer token) | Your AWS rate | `cldx setup bedrock` |
| **Google Gemini** (free tier OK) | Free up to a quota | `cldx setup gemini` |
| **Disabled** (raw pane to Telegram) | $0 | `cldx setup` → option 4 |

When disabled, the raw structural `⏺...✻` slice of Claude's pane goes to Telegram (sanitised: no box-drawing chars, no banner art, no UI chrome).

---

## Run

```bash
cldx                  # interactive picker — pick a pane or start a new one
cldx --auto-detect    # auto-attach to the only running Claude pane
cldx --list-panes     # show all tmux panes and what's in them
```

For a complete command reference (terminal slash commands, Telegram slash commands, policy profiles, troubleshooting) see **[GUIDELINE.md](./GUIDELINE.md)**.

---

## Features (v1.0.4)

### Auto-approval
- **Per-tool classification** — `Read`/`Grep`/`Glob` are `safe`; `Write`/`Edit` are `elevated`; `Bash` gets risk refined from the command (`rm -rf` → `destructive`, `pip install` → `elevated`); writes under `/etc` or `~/.ssh` always escalate.
- **Per-profile defaults** — `auto-approve`, `yolo`, `restricted`, `default`, `paranoid`. Pick yours in `policy.yml` or switch live with `/profile <name>`.
- **Configurable wait bar** — auto-fires after N seconds; type anything during the countdown to override.
- **Yolo learning** — in `yolo` profile, your y/n choices get remembered per pattern (never for destructive ops).
- **Built-in safety floor** — `rm -rf`, `dd`, `mkfs`, `chmod -R`, `sudo`, `git push --force` etc. bypass auto-approve regardless of profile.

### Telegram bridge
- **Structured cards** — separate templates for approval / completion / escalation / greeting / error, all phone-screen-friendly.
- **Two-way control** — reply `y` / `n` / `1` / `2` / free-form text from anywhere in the world.
- **Slash commands** on Telegram: `/help`, `/status`, `/panes`, `/snapshot`, `/stop`, `/pause`, `/resume`, `/profile`, `/yes`, `/no`, `/cancel`. These never leak into Claude.
- **Runtime toggle** — `/telegram on` / `/telegram off` in the cldx terminal to silence forwarding without restarting.
- **Session-limit detector** — when Claude's 5-hour rolling Pro window hits, cldx parses the reset time, updates the header, and pings you on Telegram when the window reopens.

### UX
- **Bordered input box** styled like Claude Code's own (Tab to accept Claude's suggestion).
- **Dynamic header** shows what's connected: `Claude + TMUX`, `Claude + TMUX + Telegram`, `... (Resets at 7:50 pm)`.
- **Three-tier decision panels** — yellow (auto-approve pending), red (needs your call), green (task complete).
- **Mirror panel** preserves Claude's own ANSI styling, syntax colours, dim placeholders.
- **Structural ⏺...✻ extraction** — completion panels show exactly Claude's response, dropping your echo'd question and the duration line.

### Observability
- **Per-session interaction log** at `~/.cldx/logs/YYYY-MM-DD/HH-MM-SS_<profile>_<pane>.log` — every terminal input, every Telegram in/out, every cldx decision, every Claude output. Plain text. `tail -f` friendly.
- **JSONL event log** at `~/.cldx/sessions/<profile>/<timestamp>.jsonl` — machine-replayable.
- **Multi-session picker** — arrow keys, `d` to delete, resume from any prior session by date.

---

## Release 1.0.4

The first stable release. Everything since the original prototype has gone through real-world use:

- ✅ All 7 build phases shipped (session storage, three-tier policy + wait bar, startup picker, yolo learning, agent persona + LLM, Telegram bridge).
- ✅ Multi-backend LLM (Anthropic / Bedrock / Gemini / disabled).
- ✅ Tool-call classification with risk refinement.
- ✅ Structural `⏺...✻` result extractor.
- ✅ Multi-word display-name parsing (`Web Search`, `Web Fetch`, `Multi Edit`…).
- ✅ Auto-detect Claude's session limit + reset notifier.
- ✅ 508 passing tests covering classifier, policy, Telegram, sanitiser, conversation extractor, tool registry.
- ✅ Documented: install, GUIDELINE, FEATURES roadmap, code of conduct.

See [`FEATURES.md`](./FEATURES.md) for what's next.

---

## How it works

```
┌────────────────┐                    ┌─────────────────┐
│   Claude Code  │ ──── tmux pane ──→ │      cldx       │
│   (in tmux)    │ ←──  send-keys  ── │  (this thing)   │
└────────────────┘                    └────────┬────────┘
                                               │
                              ┌────────────────┼────────────────┐
                              │                │                │
                              ▼                ▼                ▼
                       ┌────────────┐  ┌────────────┐  ┌─────────────┐
                       │  Terminal  │  │  Telegram  │  │ ~/.cldx/    │
                       │  (you)     │  │  (phone)   │  │ logs+state  │
                       └────────────┘  └────────────┘  └─────────────┘
```

- **Monitor** — polls `tmux capture-pane` once a second, computes a stable hash of the pane tail, fires `on_change` / `on_stable` callbacks.
- **Classify** — every snapshot runs through `PromptClassifier` which uses both pattern matching AND structural rules (a Claude turn = `⏺ ... ✻`).
- **Decide** — `PolicyEngine` reads `policy.yml`, applies the active profile, runs destructive-pattern checks, returns `AUTO_YES / AUTO_NO / WAIT_LOCAL / ESCALATE_TELEGRAM`.
- **Act** — for auto-decisions, `TmuxController.send_keys` types the response into the pane. For escalations, `TelegramBridge` sends a card and waits for your reply.

No daemons. No background services. One process per Claude pane, cleanly killable with Ctrl-D.

---

## Documentation

| File | What's in it |
|---|---|
| **[GUIDELINE.md](./GUIDELINE.md)** | Full command reference (terminal + Telegram), profile configuration, troubleshooting |
| **[FEATURES.md](./FEATURES.md)** | Roadmap — done / near-term / mid-term / long-term, how to propose features |
| **[CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)** | How we work together — required reading before contributing |
| **[LICENSE](./LICENSE)** | GPL-3.0-or-later |

---

## Contributing

cldx is built in the open. Bug reports, feature ideas, and PRs welcome at <https://github.com/Pranjalab/cldx>.

Quick start for contributors:

```bash
git clone https://github.com/Pranjalab/cldx.git
cd cldx
python3.13 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,all-llm]"
pytest -q                       # all 508 tests must stay green
```

Before opening a PR, read [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md) and check [`FEATURES.md`](./FEATURES.md) to see where your idea fits.

---

## Inspiration

cldx is inspired by the pattern of **remote-controlling a long-running agent**. If you've used OpenInterpreter or thought "I wish I could just text my Claude session from the bus", that's the same itch.

The two design choices that define cldx:

- **Auto-approval is the default**, not a power-user toggle. The whole point is *not clicking*.
- **Telegram is the chosen medium**, not because it's the best, but because it's the cheapest, most universal, and easiest to integrate. Free on every phone. No proprietary clients. No subscription. Your bot, your chat, your control.

---

## License

[GPL-3.0-or-later](./LICENSE). If you use cldx commercially, the GPL terms apply — share modifications back, keep the source open. If GPL conflicts with your use case, [open an issue](https://github.com/Pranjalab/cldx/issues) and we'll talk.
