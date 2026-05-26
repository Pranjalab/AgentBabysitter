# cldx — Project Plan

> A second-layer terminal for Claude Code: monitors a tmux pane,
> classifies prompts, auto-responds based on policy, and bridges to
> Telegram for remote review.
>
> **License:** GNU General Public License v3.0 (see `LICENSE`).
> **Repository:** https://github.com/Pranjalab/cldx (public).

---

## 1. Mission

Make working with Claude Code **agent-friendly**: let the human stay in
flow (or step away entirely) while a configurable bridge handles routine
approvals, summarizes long-running results, and pings them only when
real judgment is needed.

The user types `cldx` from anywhere in their shell; cldx picks up
where it left off, lists running Claude sessions, lets them start a new
one, and gives them a "remote control" view of whatever they connect to.

---

## 2. User-facing experience

### 2.1 First-run install
```bash
$ git clone <repo> && cd cldx
$ ./install.sh
✓ Python 3.12 detected
✓ Installing cldx (pip install --user .)
✓ Wrote ~/.cldx/{memory.json,config/policy.yml,config/agent_name.yml}
→ Add this to ~/.zshrc:  export PATH="$HOME/.local/bin:$PATH"
→ Restart your shell, then run `cldx`.
```

### 2.2 Every-run launch
```
$ cldx
╭─ cldx ─────────────────────────────────────────────╮
│  agent     Aria                                       │
│  profile   auto-approve  (3 patterns remembered)      │
│  telegram  ✗ not configured  (run `cldx telegram`) │
│  last run  2h ago — 14 prompts handled                │
╰───────────────────────────────────────────────────────╯

Pick a session:
  [1] resume  cldx-1  last active 2h ago  (14 events)
  [2] connect 0:0.0      ✳ Claude Code       (running)
  [3] start   new tmux + claude               (recommended)
  [4] manage  config, profiles, telegram, history
> _
```

### 2.3 Connected view
- Top: previous transcript replayed from `events.jsonl`.
- Middle: live mirror of the Claude pane, refreshed when stable.
- Bottom: persistent `claude> ` input bar; context-sensitive label
  switches to `claude (y/n)>` or `claude (1/2/3)>` when a prompt is
  pending.
- Above the bar: bridge events stream in chronological order
  (classifications, decisions, what got sent).

### 2.4 Auto-approve wait bar
Whenever the bridge is about to auto-approve a prompt — whether from the
`auto-approve` profile *or* from a yolo-learned pattern — it shows a
countdown bar first:
```
[ASK→AUTO] Bash(npm install --save axios)            (matched: yolo learned)
  approving in [████████░░░░░░░░] 1.2s   (press any key to override)
```
- Countdown duration comes from `policy.yml:profiles.<profile>.wait_interval_seconds`
  (default `2.0`). Configurable per profile — `0` disables the bar entirely.
- Any keystroke during the countdown → cancels timer, switches to manual.
- Destructive patterns (`rm`, `unlink`, `DROP TABLE`, etc.) **skip the
  countdown entirely** and wait indefinitely, regardless of profile.

---

## 3. Locked-in decisions

| Decision                  | Choice                                                  |
|---------------------------|---------------------------------------------------------|
| State location            | `~/.cldx/` (override via `$CLDX_HOME`)            |
| LLM backend               | Claude API, Haiku 4.5 (`ANTHROPIC_API_KEY` required)    |
| Yolo learning granularity | `tool + first arg token` (e.g., `Bash(npm ...)`)        |
| Yolo + destructive ops    | Never learnable — always asks                           |
| Auto-approve + destructive| No wait bar — pends indefinitely until user replies     |
| Wait interval             | Configurable per profile (`wait_interval_seconds`, default `2.0`); applies to `auto-approve` and to yolo's learned auto-approvals; `0` disables |
| Install method            | `pip install --user .` via `install.sh`; `cldx` shim |
| Distribution layout       | `cldx/` package, `pyproject.toml`, `install.sh`      |

---

## 4. Target file layout

### Code (project tree)
```
cldx/                              # Python package (was src/)
├── __init__.py
├── cli.py                            # `cldx` entrypoint + subcommands
├── ui.py                             # second-layer TUI (was main.py body)
├── startup.py                        # greeting + session picker
├── tmux_monitor.py                   # async pane watcher
├── tmux_controller.py                # send-keys wrapper
├── session_picker.py                 # enumerate / auto-detect tmux panes
├── prompt_classifier.py              # snapshot → PromptType
├── policy_engine.py                  # decision + wait-bar config
├── session_store.py                  # events.jsonl writer / replayer
├── memory.py                         # ~/.cldx/memory.json read+write
├── agent.py                          # agent_name.yml loader
├── summarizer.py                     # Claude API summaries (with persona)
├── telegram_bridge.py                # send/receive (Phase 7)
└── settings.py                       # ~/.cldx path helpers, XDG-ish
install.sh
pyproject.toml
PLAN.md          ← this file
README.md
```

### Runtime state (`~/.cldx/`)
```
~/.cldx/
├── memory.json                       # learned patterns, telegram, last_session
├── config/
│   ├── policy.yml                    # editable; profiles + detection
│   ├── agent_name.yml                # persona for summaries
│   └── telegram.env                  # bot token + chat id (optional)
└── sessions/
    └── <profile>/
        └── 2026-05-26T10-32-15.jsonl # one event per line
```

### memory.json shape
```json
{
  "agent_name": "Aria",
  "active_profile": "auto-approve",
  "telegram": { "configured": false },
  "approved_patterns": {
    "yolo": ["Bash(npm)", "Read", "Edit(*.py)"]
  },
  "denied_patterns": { "yolo": ["Bash(rm)"] },
  "last_session": {
    "id": "2026-05-26T08-22-01",
    "profile": "auto-approve",
    "pane": "0:0.0",
    "events": 14,
    "ended_at": "2026-05-26T08-45-12"
  }
}
```

### events.jsonl shape
```
{"t":"2026-05-26T08:22:03","kind":"snapshot","lines":["⏺ Bash(ls)",...]}
{"t":"2026-05-26T08:22:03","kind":"prompt","type":"approval_menu","tool":"Bash(ls)","options":["1. Yes","2. ...","3. No"]}
{"t":"2026-05-26T08:22:03","kind":"decision","decision":"auto_yes","reason":"matched auto_approve","wait_ms":0}
{"t":"2026-05-26T08:22:03","kind":"action","keys":"1"}
{"t":"2026-05-26T08:22:08","kind":"telegram_out","summary":"Created test/test_sample.py"}
```

---

## 5. Policy profiles (final spec)

### auto-approve  (default)
- **Safe ops** (`Read`, `LS`, `Grep`, `Bash(ls|cat|...)`, etc.):
  countdown (`wait_interval_seconds`) → auto-yes.
- **Routine writes** (`Edit`, `Write`, `Bash(npm ...)`, etc.):
  countdown → auto-yes.
- **Destructive** (`rm`, `unlink`, `DROP`, `git reset --hard`, force-push,
  `chmod 777`, etc.): no countdown — pends until human/Telegram replies.
- Any keystroke during countdown cancels it and routes to manual.

### yolo
- First time we see a pattern (`tool + first arg token`): ask the user.
- On user-approve: add pattern to `memory.json:approved_patterns.yolo`
  and auto-approve future matches **with the same wait-bar countdown**
  so you can still override a learned decision.
- On user-deny: add to `denied_patterns.yolo` → auto-deny on future matches.
- Destructive patterns are never learnable; they always ask.

### restricted
- Every approval routes to user via terminal **and** Telegram (if
  configured). Zero automatic decisions, no wait bar.

---

## 6. Telegram summary strategy

Three summary modes, each with a strict char budget:

| Mode                  | Budget | Trigger                                          |
|-----------------------|--------|--------------------------------------------------|
| `prompt_summary`      | ≤ 200  | An escalation needs human reply                  |
| `escalation_summary`  | ≤ 500  | Long context — boil pane down for remote review  |
| `completion_summary`  | ≤ 500  | Claude finished a task while user was away       |

Summarizer is `summarizer.summarize(mode, context, agent)`:
1. Loads persona from `~/.cldx/config/agent_name.yml`.
2. Calls Claude Haiku 4.5 with the persona as system prompt, the raw
   context as user message, and an instruction tailored to the mode.
3. Returns text capped at the mode's budget.
4. Fallback: if API key missing or call fails, returns naïve truncation
   plus a `[unsummarized]` marker, so Telegram still works degraded.

`agent_name.yml`:
```yaml
name: Aria
persona: |
  You are a terse technical assistant. Summarize Claude Code activity
  for a developer who's away from their keyboard. Lead with the action
  taken, end with what's pending. No hedging, no fluff.
model: claude-haiku-4-5
api_key_env: ANTHROPIC_API_KEY
limits:
  prompt_summary: 200
  escalation_summary: 500
  completion_summary: 500
```

---

## 7. Build phases

Each phase is independently shippable and gets one commit.

| # | Phase                          | Status | Deliverable                                                       |
|---|--------------------------------|--------|-------------------------------------------------------------------|
| 1 | Package + installer            | ✅ done | `pyproject.toml`, `install.sh`, `cldx` console_script, package rename, README update |
| 2 | Runtime state + session store  | ✅ done | `~/.cldx/` bootstrap; `session_store.py`, jsonl event format     |
| 3 | Three profiles + wait bar      | ✅ done | `auto-approve` profile, async countdown with key-override, `wait_interval_seconds` per profile, destructive-op detection |
| 4 | Startup greeting + picker      | ✅ done | banner + numbered picker, replay last transcript, spawn new tmux+claude option |
| 5 | Yolo learning                  | ✅ done | pattern normalization, read/write `approved_patterns`, destructive exclusion |
| 6 | Agent + summarizer             | ✅ done | `agent_name.yml`, `summarizer.py` (Anthropic SDK), prompt-cached persona system msg |
| 7 | Telegram bridge                | ✅ done | `telegram_bridge.py` (python-telegram-bot v22), auth boundary, inbound parser, timeout helper |

**Total ≈ 11 hours.** Phases 1–3 unblock a usable v1 (no Telegram, no
LLM); phases 4–5 are UX polish; phases 6–7 add the remote capability.

---

## 8. Out of scope (for now)

These are intentionally deferred — flagged here so we don't lose them.

- Multi-pane watching (one bridge → many Claude sessions).
- Web dashboard / Mac menu-bar app.
- Voice notifications.
- Cost dashboard for LLM spend.
- Plugin system for custom classifiers / actions.
- Auto-detect on `tmux new-window` so cldx picks up new sessions
  without restart.
- Cross-machine bridging (cldx on laptop, Claude Code on server).

---

## 9. Open questions

To revisit before / during phase 6–7:

- Should `cldx telegram` walk the user through bot creation (link
  to BotFather, prompt for token, auto-discover chat_id from first
  message) or just print instructions?
- For prompt caching with Anthropic SDK: cache the persona system
  prompt + a static instruction block so each summary is a one-shot
  cache-hit. Worth doing on day one of phase 6.
- When `restricted` profile has Telegram configured but user is in the
  terminal: send to *both* and accept whichever replies first, or just
  one? My instinct: both, first reply wins, second one notified
  "already handled".

---

## 10. How we'll work

1. **No code yet** — confirm this plan reflects what you want.
2. After sign-off: build phases sequentially. Each phase ends with a
   manual smoke test and one commit.
3. PLAN.md updates as we learn — any spec drift gets recorded here so
   future you (or future me) can see how we got from idea to ship.
