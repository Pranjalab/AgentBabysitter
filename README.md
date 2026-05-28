# Agent Babysitter

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Version](https://img.shields.io/badge/version-1.0.4-brightgreen.svg)](#release-104)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-508%20passing-brightgreen.svg)]()
[![Status](https://img.shields.io/badge/status-beta-yellow.svg)]()

> **Agent Babysitter monitors your AI coding agent — Claude Code, Gemini CLI, Codex — so you don't have to. A local LLM enforces your policy, approves safe moves, escalates risky ones to your phone, and watches your project from planning to deployment. You go live your life. The Agent babysits the Agent.**

---

## The problem

AI coding agents are powerful. They're also chatty: every `Bash`, every `Write`, every `Edit` pops an approval prompt. And the risky ones — a force-push, a `DROP TABLE`, a write to `~/.ssh` — genuinely need a human call.

So you sit there. Watching. Clicking. Babysitting the agent that was supposed to free you.

**Agent Babysitter flips that.** You define a policy. A local LLM reads it and babysits the agent on your behalf — approving the safe moves, escalating the risky ones to your phone via Telegram. You go to the gym. Your family gets dinner together. The AI keeps coding. You get a summary when it's done.

---

## How it works

```
┌─────────────────┐     ┌───────────────────────────────┐
│  AI Coding Agent│────▶│         Agent Babysitter          │
│  Claude Code    │     │                               │
│  Gemini CLI     │     │  1. Reads your policy.yml     │
│  Codex          │     │  2. Classifies every action   │
└─────────────────┘     │  3. Asks local LLM if unsure  │
                        │  4. Auto-approves safe moves  │
                        │  5. Escalates to your phone   │
                        └───────────┬───────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
             ┌──────────┐   ┌──────────┐   ┌─────────────┐
             │ Terminal │   │ Telegram │   │ ~/.abs/    │
             │  (you)   │   │ (phone)  │   │ logs+state  │
             └──────────┘   └──────────┘   └─────────────┘
```

- **Monitor** — watches the agent's tmux pane, detects every approval prompt
- **Policy engine** — matches the action against your `policy.yml` (approve / escalate / block)
- **LLM babysitter** — your local Ollama or LM Studio model gets the ambiguous calls
- **Telegram bridge** — escalations go to your phone; you reply `y` / `n` from anywhere
- **Lifecycle awareness** — stricter during deploy than during a dev spike

No daemons. No background services. One process per agent pane, cleanly killable with Ctrl-D.

---

## The policy file — write it once, walk away

This is the centrepiece. A single `policy.yml` that you can read and edit in under five minutes:

```yaml
version: "1.0"

# The local LLM that babysits your agent
babysitter:
  backend: ollama          # ollama | lmstudio | anthropic | gemini | disabled
  model: llama3.2
  endpoint: http://localhost:11434

# Where escalations go
notify:
  telegram:
    enabled: true

# Your policy
policy:
  profile: default

  profiles:
    default:
      approve:
        tools: [Read, Grep, Glob, LS, WebSearch]
        commands:
          - "git status"
          - "git log*"
          - "pytest*"
          - "npm test*"
          - "cargo test*"

      escalate:
        tools: [Write, Edit, Bash, MultiEdit]
        commands:
          - "git commit*"
          - "npm install*"
          - "pip install*"
          - "docker build*"
        wait_seconds: 5    # countdown before auto-escalating

      block:
        commands:
          - "rm -rf*"
          - "git push --force*"
          - "DROP TABLE*"
          - "mkfs*"
          - "sudo*"
        paths:
          - "/etc/**"
          - "~/.ssh/**"
          - "~/.aws/**"
          - "~/.gnupg/**"
```

The babysitter reads this file. The local LLM uses it as its decision constitution. Every auto-approval, every escalation, every block is traceable back to a rule you wrote.

### Built-in profiles

| Profile | Philosophy |
|---|---|
| `default` | Safe ops auto-approved, writes escalated, destructive ops blocked |
| `auto-approve` | Maximum autonomy — only destructive ops blocked |
| `yolo` | Like auto-approve, but learns your y/n choices per pattern |
| `restricted` | Everything escalates except reads |
| `paranoid` | Everything escalates. Everything. |

Switch live: `abs /profile paranoid` or from Telegram.

---

## Supported AI agents

| Agent | Status |
|---|---|
| Claude Code | ✅ Shipped (v1.0.4) |
| Gemini CLI | 🔄 In roadmap |
| OpenAI Codex terminal | 🔄 In roadmap |
| Generic tmux pane | 🔄 In roadmap |

---

## Babysitter backends (local LLM)

The babysitter uses a local or remote LLM to evaluate ambiguous actions. You own the model. Your code never leaves your machine.

| Backend | Cost | Setup |
|---|---|---|
| **Ollama** (recommended) | Free | `ollama pull llama3.2` |
| **LM Studio** | Free | GUI, OpenAI-compatible |
| **Disabled** | $0 | Pattern-match only, no LLM |
| **Anthropic** | ~$0.0001/eval (Haiku) | API key |
| **Google Gemini** | Free tier | API key |

---

## Telegram — your phone is the control panel

When the babysitter escalates, your phone buzzes:

```
🤔 Agent Babysitter — Needs Your Call
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🤖 Agent: Claude Code
🔧 Tool: Bash
📋 Command: npm install express
📁 Project: my-api  |  Phase: implementing

📊 Policy verdict: ESCALATE
🧠 Babysitter says: "New dependency — confirm you want this package"
⚖️  Confidence: 78%

Reply: y · n · ! (block forever) · ? (explain more)
```

You reply. Claude continues. You never had to open your laptop.

When the task finishes:

```
✅ Agent Babysitter — Task complete
━━━━━━━━━━━━━━━━━━━━━━━━
📌 Task: Add OAuth flow to user signup
📝 Summary: Added OAuth provider + 8 passing tests.
⏱️  Duration: 47s  |  ✅ 12 auto-approved  |  💬 1 escalated
⚙️  Profile: default

💬 Reply with your next task.
```

---

## Install

```bash
git clone https://github.com/Pranjalab/AgentBabysitter.git
cd AgentBabysitter
./install.sh
```

The installer picks a compatible Python (≥ 3.11), installs the `abs` command, and creates `~/.abs/` with bundled defaults. Existing configs are never overwritten.

```bash
abs setup          # interactive — LLM backend + Telegram in sequence
abs setup telegram # just Telegram
abs setup llm      # just the LLM picker
abs config         # show current config (secrets masked)
```

---

## Run

```bash
abs                  # pick a pane or start a new session
abs --auto-detect    # attach to the only running agent pane
abs --list-panes     # show all tmux panes
```

---

## Lifecycle awareness

The babysitter adjusts its strictness based on what phase your project is in:

| Phase | Detected by | Behaviour |
|---|---|---|
| Planning | Recent commits: `plan`, `spike`, `rfc` | Broad web access approved |
| Implementing | Code files being written | Profile defaults |
| Testing | `pytest`, `npm test`, `cargo test` detected | Bash runs more freely |
| Deploying | `kubectl`, `terraform`, `helm`, `docker push` | Strictest — even reads get flagged |

---

## Features (v1.0.4 — Claude Code)

> v1.0.4 is the foundation: a fully working Claude Code babysitter. The universal policy engine and multi-agent support are the next milestone.

### Auto-approval engine
- Per-tool classification — `Read`/`Grep`/`Glob` safe; `Write`/`Edit` elevated; `Bash` risk-refined from command
- Five built-in profiles — live-switchable via terminal or Telegram
- Configurable wait-bar countdown before auto-fire
- Yolo learning — remembers your y/n choices per pattern
- Built-in safety floor — `rm -rf`, `dd`, `sudo`, `git push --force` bypass auto-approve regardless of profile

### Telegram bridge
- Structured approval / completion / escalation cards
- Two-way control — reply from anywhere in the world
- Slash commands: `/help`, `/status`, `/panes`, `/snapshot`, `/stop`, `/pause`, `/resume`, `/profile`, `/yes`, `/no`
- Runtime toggle — `/telegram on` / `/telegram off` without restarting
- Session-limit detector — parses Claude's reset time, pings you when the window reopens

### UX
- Bordered input box styled like Claude Code's own
- Dynamic header: `Claude + TMUX [+ Telegram] [(Resets at HH:MM)]`
- Three-tier decision panels — yellow (pending) / red (your call) / green (done)
- Mirror panel preserves Claude's ANSI styling
- Arrow-key session picker with delete-by-`d`

### Observability
- Per-session log at `~/.abs/logs/YYYY-MM-DD/HH-MM-SS_<profile>_<pane>.log`
- JSONL event log at `~/.abs/sessions/<profile>/<timestamp>.jsonl`
- Every input, every decision, every Telegram message — plain text, `tail -f` friendly

---

## Roadmap

See [`FEATURES.md`](./FEATURES.md) for the full list. The next major milestone:

- [ ] Universal `policy.yml` schema with Pydantic validation
- [ ] LLM babysitter backend (Ollama + LM Studio, P0)
- [ ] Gemini CLI adapter
- [ ] Codex terminal adapter
- [ ] Lifecycle phase detector
- [ ] Enhanced Telegram cards with LLM reasoning + confidence

---

## Documentation

| File | What's in it |
|---|---|
| **[PLAN_POLICY.md](./PLAN_POLICY.md)** | Full implementation plan for the policy engine |
| **[GUIDELINE.md](./GUIDELINE.md)** | Full command reference — terminal + Telegram, profiles, troubleshooting |
| **[FEATURES.md](./FEATURES.md)** | Roadmap — done / near-term / mid-term / long-term |
| **[CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)** | How we work together |
| **[LICENSE](./LICENSE)** | GPL-3.0-or-later |

---

## Contributing

Agent Babysitter is built in the open. Bug reports, feature ideas, and adapters for new AI agents are all welcome.

```bash
git clone https://github.com/Pranjalab/AgentBabysitter.git
cd AgentBabysitter
python3.13 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,all-llm]"
pytest -q
```

The highest-value contributions right now:
- **Gemini CLI adapter** — detection patterns for Gemini's approval prompts
- **Codex adapter** — same for OpenAI Codex terminal
- **LM Studio backend** — OpenAI-compatible local backend
- **Policy schema** — Pydantic models for `policy.yml`

Before opening a PR, read [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md).

---

## Acknowledgments

Agent Babysitter stands on the shoulders of tools that made agentic coding possible:

- **[Claude Code](https://docs.claude.com/en/docs/claude-code)** (Anthropic) — the first agent this tool babysits, and the one that proved agentic coding is real
- **[Gemini CLI](https://github.com/google-gemini/gemini-cli)** (Google) — the second agent in scope, and a signal that this is a category
- **[tmux](https://github.com/tmux/tmux)** — the invisible backbone; none of this works without reliable terminal multiplexing
- **[Ollama](https://ollama.com)** — making local LLMs a one-line install; the reason the babysitter can run on your laptop for free
- **[LM Studio](https://lmstudio.ai)** — the GUI path to local LLMs; lowers the barrier for developers who aren't comfortable with CLIs
- **[Telegram](https://telegram.org)** — free, universal, runs on every phone, no proprietary clients, no subscription; the only messaging platform worth building on for a tool like this
- **[python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)** — the async Telegram library that made the bridge straightforward
- **[prompt_toolkit](https://github.com/prompt-toolkit/python-prompt-toolkit)** — the terminal UI layer behind the bordered input box
- **The open source community** — every library, every issue filed, every PR merged that made this possible

---

## License

[GPL-3.0-or-later](./LICENSE). If you use Agent Babysitter commercially, the GPL terms apply — share modifications back, keep the source open. If GPL conflicts with your use case, [open an issue](https://github.com/Pranjalab/abs/issues) and we'll talk.
