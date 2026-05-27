# cldx

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-341%20passing-brightgreen.svg)]()

A second-layer terminal for **Claude Code**. cldx watches a `tmux` pane
running Claude, classifies every approval prompt, auto-responds based on
a policy *you* control, and (optionally) bridges to Telegram so you can
review and reply when you're away from your laptop.

It works like a remote control: you see Claude's pane mirrored in
cldx's terminal, decisions are made visible as **yellow / red / green
panels**, and a Claude-Code-styled bordered input box lets you type
directly into Claude or override pending decisions.

> **Status:** All 7 build phases shipped + extensive UX work.
> See [`PLAN.md`](./PLAN.md) for the original roadmap, kept as
> historical record.

---

## Install

```bash
git clone https://github.com/Pranjalab/cldx.git
cd cldx
./install.sh
```

The installer:

1. Finds a Python ≥ 3.11 (`python3.13` / `3.12` / `3.11`, in that order).
2. Runs `pip install --user .` so the `cldx` command lands on your PATH.
3. Bootstraps `~/.cldx/{config,sessions}/` with the bundled defaults.
4. Tells you exactly what `export PATH=...` line to add to `~/.zshrc`
   or `~/.bashrc` if `cldx` isn't immediately findable.

Override the runtime state location with `export CLDX_HOME=/some/path`.
Uninstall with `./install.sh --uninstall` (leaves `~/.cldx/` in place
so your config + history survive).

### Optional LLM extras

cldx works fully without an LLM (Telegram messages just forward the raw
Claude pane). To enable upstream summarisation:

```bash
pip install --user 'cldx[bedrock]'   # AWS Bedrock (boto3)
pip install --user 'cldx[gemini]'    # Google Gemini (google-genai)
pip install --user 'cldx[all-llm]'   # both
# Anthropic direct uses the `anthropic` SDK (already a core dep)
```

---

## Quick start

In one terminal:

```bash
tmux new -s work
# inside tmux:
claude
```

In another terminal:

```bash
cldx                          # default — banner + arrow-key picker
cldx --auto-detect            # skip picker, pick the live Claude pane
cldx --session work:0.0       # skip picker, target a specific pane
cldx --profile yolo           # override the active policy profile
cldx --dry-run                # classify + decide, never send keys
cldx --no-llm                 # skip LLM summarisation for this run
cldx --no-telegram            # don't start the Telegram bridge
cldx --list-panes             # show every tmux pane and exit
```

Module form also works: `python -m cldx`.

### The connected view

Once cldx is connected to a Claude session, you'll see:

- **Blue mirror panel** — Claude's pane re-rendered with its own ANSI
  styling preserved (dim placeholders, coloured tool calls, etc.).
- **Yellow approval panel** — when policy says "auto-approve in 2s",
  showing the tool + menu options + countdown.
- **Red approval panel** — when a destructive op (`rm`, `sudo`, etc.)
  needs *your* go-ahead; pends indefinitely.
- **Green completion panel** — final summary of the task, plus a
  Telegram status line ("✓ sent" / "✗ not configured" / "✗ send
  failed").
- **Bordered input box** — Claude-Code styled, with the current
  suggestion shown dim italic; press **Tab** to accept it.

Approvals fire on the FIRST poll that sees the prompt (no waiting for
Claude's UI to fully stabilise), and the completion panel only renders
for tasks that actually used tools — chat replies get a one-liner.

---

## Setup wizards

```bash
cldx setup              # picks an LLM backend, then walks through Telegram
cldx setup llm          # just the LLM picker
cldx setup anthropic    # direct Anthropic API key
cldx setup bedrock      # AWS Bedrock (bearer token + region)
cldx setup gemini       # Google Gemini API key
cldx setup none         # persistently DISABLE the LLM (raw pane → Telegram)
cldx setup telegram     # bot token + auto-discovers your chat ID
cldx config show        # masked view of every configured secret + active backend
cldx test llm           # end-to-end smoke test of the configured LLM
```

Every wizard:
- Uses **paste-tolerant input** (no truncation of 2-3KB Bedrock tokens
  on macOS).
- Validates credentials with a tiny upstream call before saving.
- Stores to `~/.cldx/config/*.env` with mode `0600`.
- Offers to flip your `agent_name.yml` over to that backend so the
  very next `cldx` run uses it.

The Telegram wizard creates the bot via `@BotFather`, then
**auto-discovers your chat ID** by polling `getUpdates` after you
message your new bot once — no manual copy-paste of numbers.

The Bedrock wizard is region-aware: it picks the right cross-region
inference profile prefix (`us.*` / `eu.*` / `apac.*`) for your region,
and on a `ValidationException` queries Bedrock for the models you
actually have access to and lets you pick from the live list.

### LLM backends

| Prefix      | Backend           | Auth needed                        | Install extra                |
|-------------|-------------------|------------------------------------|------------------------------|
| `claude-*`  | Anthropic direct  | `ANTHROPIC_API_KEY`                | (built-in)                   |
| `bedrock:`  | AWS Bedrock       | `AWS_BEARER_TOKEN_BEDROCK`         | `pip install 'cldx[bedrock]'`|
| `gemini:`   | Google Gemini     | `GEMINI_API_KEY`                   | `pip install 'cldx[gemini]'` |
| `ollama:`   | Local Ollama      | (none — stub, falls back to raw)   | (not yet implemented)        |
| `none:raw`  | Disabled          | (none)                             | (built-in)                   |

The agent's `model:` field in `~/.cldx/config/agent_name.yml` selects
the backend. When the LLM call fails for any reason, cldx falls back
to forwarding the **raw pane context** to Telegram (no
`[unsummarized: …]` marker leaks into the chat — that reason is
logged locally only).

### Where secrets land

All secrets go to `~/.cldx/config/*.env` files with mode `0600`:

| File              | Variables                                          |
|-------------------|----------------------------------------------------|
| `anthropic.env`   | `ANTHROPIC_API_KEY`                                |
| `bedrock.env`     | `AWS_BEARER_TOKEN_BEDROCK`, `AWS_REGION`           |
| `gemini.env`      | `GEMINI_API_KEY`                                   |
| `telegram.env`    | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`           |

These are loaded into the process environment on every `cldx` run, so
the summarizer and Telegram bridge find them automatically. (You can
still override via `export VAR=...` — file values don't clobber a
parent-shell environment.)

---

## Useful flags

| Flag                  | Purpose                                                                |
|-----------------------|------------------------------------------------------------------------|
| `--session S:W.P`     | Skip discovery and watch this pane.                                    |
| `--auto-detect`       | Watch the first pane that looks like Claude Code; picker on ambiguity. |
| `--profile NAME`      | Override `active_profile` from `policy.yml`.                           |
| `--policy PATH`       | Use a custom policy file (overrides `~/.cldx/config/...`).             |
| `--poll-interval N`   | Seconds between snapshots (default `1.0`).                             |
| `--mirror-lines N`    | Tail lines of the Claude pane to mirror (default `25`).                |
| `--list-panes`        | Print all tmux panes (with command + title) and exit.                  |
| `--dry-run`           | Classify and decide, but never send keys to tmux.                      |
| `--no-llm`            | Skip the LLM summarizer for this run (raw pane → Telegram).            |
| `--no-telegram`       | Don't start the Telegram bridge even if configured.                    |
| `--version`           | Print the installed version.                                           |

---

## Policy

`~/.cldx/config/policy.yml` (falls back to the bundled default) ships
with five profiles. The active one is set by the `active_profile:`
field — default is `auto-approve`.

| Profile        | Behaviour                                                                                                 |
|----------------|-----------------------------------------------------------------------------------------------------------|
| `auto-approve` | Default. Auto-approves routine work after a configurable wait bar; destructive ops pend indefinitely.     |
| `yolo`         | Learns from your decisions — first time approve, future identical patterns auto-fire (with wait bar).     |
| `restricted`   | Every approval pends; nothing auto-fires.                                                                 |
| `default`      | Legacy fine-grained allow/deny rule lists (kept for users who prefer explicit patterns).                  |
| `paranoid`     | Denies most things destructive, escalates the rest.                                                       |

### Wait bar

The `auto-approve` and `yolo` profiles support a per-profile
`wait_interval_seconds` (default `2.0`). Whenever the bridge is about
to auto-fire, a yellow panel shows a countdown — any keystroke during
the countdown cancels the auto-action and switches to manual approval.
Destructive ops (`rm`, `sudo`, `DROP TABLE`, `git push --force`, etc.)
**bypass the bar entirely** and pend indefinitely.

### Adding a new profile

```bash
# Copy the bundled defaults so you can edit them:
cp $(python -c 'from cldx._paths import bundled_default; print(bundled_default("policy.yml"))') \
   ~/.cldx/config/policy.yml

# Edit ~/.cldx/config/policy.yml and add under `profiles:`
```

```yaml
profiles:
  my-profile:
    wait_interval_seconds: 5.0
    auto_approve:
      - "Bash\\((ls|cat|pwd|whoami)\\)"
    auto_deny: []
    escalate_to_telegram:
      - "Bash\\(rm "
    default_action: escalate_telegram
```

Then `cldx --profile my-profile` (or set `active_profile: my-profile`
at the top of the file to make it default).

---

## Session picker

Running `cldx` (no `--session` / `--auto-detect`) opens an arrow-key
picker showing:

- **resume** rows for the most recent JSONL event logs in
  `~/.cldx/sessions/<profile>/`.
- **connect** rows for every live tmux pane that looks like Claude Code.
- **start new** — spawn a detached tmux session and start Claude in it.

Navigation:

- `↑` / `↓` — move the `❯` cursor.
- `PageUp` / `PageDown` — jump 5 rows.
- `Home` / `End` — top / bottom.
- `Enter` — select.
- `d` then `y` — delete the highlighted row (removes the event log for
  resume rows; runs `tmux kill-session` for connect rows).
- `q` / Ctrl-C — cancel.

Old `cldx-*` tmux sessions and stale event logs can be cleaned up
right from this picker without needing a separate command.

---

## Audit log

Every cldx run writes a JSONL event log to
`~/.cldx/sessions/<profile>/<ISO-timestamp>.jsonl`. Each line is one
event:

```jsonl
{"t":"2026-05-27T10:42:01+00:00","kind":"prompt","type":"approval_menu","command":"Bash(npm install)",...}
{"t":"2026-05-27T10:42:01+00:00","kind":"decision","decision":"auto_yes","reason":"matched auto_approve","profile":"auto-approve",...}
{"t":"2026-05-27T10:42:03+00:00","kind":"action","keys":"sent option 1 (Yes)","source":"policy"}
{"t":"2026-05-27T10:42:08+00:00","kind":"complete"}
{"t":"2026-05-27T10:42:08+00:00","kind":"telegram_out","summary":"Created /tmp/foo.py","mode":"completion_summary"}
```

Sources for `action` events: `policy` (auto-fire), `user_terminal` (you
typed in cldx), `user_telegram` (you replied via Telegram). Tail the
file with `jq` for live debugging:

```bash
tail -f $(ls -t ~/.cldx/sessions/auto-approve/*.jsonl | head -1) | jq .
```

---

## Slash commands (inside the cldx prompt)

| Command           | What it does                                                       |
|-------------------|--------------------------------------------------------------------|
| `/y`, `/yes`      | Approve the pending prompt (or send `y` if nothing pending).       |
| `/n`, `/no`       | Deny the pending prompt.                                           |
| `/1`, `/2`, ...   | Pick a specific menu option.                                       |
| `/skip`           | Clear `pending` — leave the prompt for you to handle in tmux.      |
| `/refresh`        | Force-reprint the mirror panel.                                    |
| `/snapshot`       | Debug: dump the current pane + classifier output + signature.      |
| `/profile NAME`   | Switch policy profile mid-session.                                 |
| `/panes`          | List all tmux panes (with the watched one marked).                 |
| `/raw KEYS`       | Send named tmux keys (e.g. `/raw C-c`, `/raw Escape`).             |
| `/help`           | Show the command list.                                             |
| `/quit`, Ctrl-D   | Exit cleanly.                                                      |

Anything not starting with `/` is **typed into Claude's text box**.

When an approval is pending, bare `y` / `n` / a digit act on the
pending prompt (instead of being typed into Claude).

---

## File structure

```
cldx/                              # Python package
├── __init__.py                    # __version__
├── __main__.py                    # `python -m cldx`
├── cli.py                         # entrypoint + TUI
├── _paths.py                      # ~/.cldx/ resolver
├── agent.py                       # agent_name.yml loader + backend dispatch
├── framed_input.py                # bordered input box (Tab-accept suggestions)
├── llm_test.py                    # `cldx test llm`
├── memory.py                      # ~/.cldx/memory.json (yolo-learned patterns)
├── picker.py                      # arrow-key session picker
├── policy_engine.py               # PromptType + policy.yml → PolicyDecision
├── prompt_classifier.py           # snapshot → PromptType + menu_options
├── secrets.py                     # loads ~/.cldx/config/*.env into os.environ
├── session_picker.py              # tmux pane enumeration
├── session_store.py               # JSONL audit log
├── setup_wizard.py                # cldx setup [target] flows
├── startup.py                     # banner + picker + spawn-new flow
├── summarizer.py                  # Anthropic / Bedrock / Gemini / none backends
├── telegram_bridge.py             # python-telegram-bot integration
├── tmux_controller.py             # send-keys wrapper
├── tmux_monitor.py                # async pane watcher
├── wait_bar.py                    # cancellable countdown helper
└── defaults/
    ├── policy.yml                 # bundled default policy
    └── agent_name.yml             # bundled default agent persona

install.sh                         # one-shot installer
pyproject.toml                     # build + entry point + extras
LICENSE                            # GPL-3.0
PLAN.md                            # historical product roadmap
README.md                          # this file
tests/                             # 341 passing tests
```

User state at runtime lives under `~/.cldx/` (override with
`$CLDX_HOME`):

```
~/.cldx/
├── config/
│   ├── policy.yml                 # user-editable rules
│   ├── agent_name.yml             # agent persona for summaries
│   ├── anthropic.env              # 0600 — ANTHROPIC_API_KEY
│   ├── bedrock.env                # 0600 — AWS_BEARER_TOKEN_BEDROCK + region
│   ├── gemini.env                 # 0600 — GEMINI_API_KEY
│   └── telegram.env               # 0600 — TELEGRAM_BOT_TOKEN + chat id
├── memory.json                    # yolo-learned patterns, last_session, etc.
└── sessions/<profile>/*.jsonl     # event logs
```

---

## Development

```bash
# Set up a dev env (no install needed for tests):
python3.12 -m pip install -r requirements-dev.txt

# Run the suite:
pytest -q                          # 341 passing

# Install the package in editable mode for end-to-end runs:
pip install --user -e .
cldx --version
```

`tests/README.md` documents the test layout. Phase markers
(`phase2`..`phase7`) let you filter to a specific surface area.

---

## License

Released under the [GNU General Public License v3.0](./LICENSE).
Contributions welcome via pull request.
