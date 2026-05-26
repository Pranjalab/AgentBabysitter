# claudex

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

Monitor a `tmux` pane running Claude Code, classify what Claude is asking
for, and auto-respond based on a policy file you control. Phase 2 will
add a Telegram bridge so you can review/respond remotely.

> **Status:** Phase 1 — local monitoring & auto-response only.
> See [`PLAN.md`](./PLAN.md) for the full product roadmap.

---

## What it does (Phase 1)

1. Picks a tmux pane (CLI flag, auto-detect, or interactive picker).
2. Polls the pane every second with `tmux capture-pane`.
3. Strips ANSI escape codes and classifies the visible state:
   `approval_yn`, `approval_menu`, `text_input`, `running`, `idle`,
   `complete`.
4. Looks up the prompt against `config/policy.yml`:
   - `auto_deny` → press `n`
   - `auto_approve` → press `y`
   - `escalate_to_telegram` → print to console (Phase 2 will send to Telegram)
   - else → fall back to the profile's `default_action`
5. Sends the response back to the pane with `tmux send-keys`.

---

## Requirements

- Python **3.11+**
- `tmux` installed and on `PATH`
- A tmux pane running `claude` (Claude Code CLI)

Install Python deps:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Quick start

In one terminal:

```bash
tmux new -s claude-work
# inside tmux:
claude
```

In another terminal:

```bash
# auto-detect (looks for a pane running claude or node)
python main.py --auto-detect

# or pick a specific pane
python main.py --session claude-work:0.0

# or run the interactive picker
python main.py
```

### Useful flags

| Flag                  | Purpose                                                       |
|-----------------------|---------------------------------------------------------------|
| `--session S:W.P`     | Skip discovery and watch this pane.                           |
| `--auto-detect`       | Watch the first pane whose command contains `claude`/`node`.  |
| `--profile NAME`      | Override `active_profile` from `policy.yml`.                  |
| `--policy PATH`       | Use a different policy file.                                  |
| `--poll-interval N`   | Seconds between snapshots (default `1.0`).                    |
| `--dry-run`           | Classify and decide, but never send keys.                     |
| `--no-telegram`       | Force-disable the Telegram bridge (Phase 1 default).          |

---

## Policy file

`config/policy.yml` ships with four profiles:

- `default` — auto-approve reads/lists, auto-deny dangerous ops, escalate
  anything that touches the filesystem or network.
- `yolo` — auto-approve everything except `rm -rf /`.
- `restricted` — escalate by default, deny obvious foot-guns.
- `paranoid` — deny almost everything destructive, escalate the rest.

Each profile has three pattern lists evaluated in order:
`auto_deny → auto_approve → escalate_to_telegram → default_action`.

Patterns are Python regex, case-insensitive, matched against the
extracted command and the surrounding context.

### Detection patterns

The `detection:` block is what the classifier uses to recognise prompts.
If Claude Code's UI changes, you can edit these patterns without
touching Python code.

---

## File structure

```
claude-tmux-bridge/
├── main.py                 # CLI + orchestrator
├── requirements.txt
├── README.md
├── config/
│   └── policy.yml          # auto-approve / deny / escalate rules
├── src/
│   ├── __init__.py
│   ├── session_picker.py   # enumerate / pick a tmux pane
│   ├── tmux_monitor.py     # async pane watcher (diff + ANSI strip)
│   ├── tmux_controller.py  # send-keys wrapper
│   ├── prompt_classifier.py# state → PromptType
│   └── policy_engine.py    # PromptType + policy.yml → PolicyDecision
└── sessions/               # reserved for Phase 3 jsonl logs
```

---

## Verifying Phase 1 manually

1. `tmux new -s claude-work` and start `claude` inside it.
2. `python main.py --auto-detect --dry-run` in another terminal.
3. Inside the Claude session ask it to `ls` something. The bridge should
   classify the approval prompt and decide `AUTO_YES`.
4. Ask it to `rm -rf something`. The bridge should decide `AUTO_NO`.
5. Drop `--dry-run` to let it actually send keys.

---

## Roadmap

- **Phase 1 (done):** local monitor + auto-respond + policy engine.
- **Phase 2:** Telegram bridge (`src/telegram_bridge.py`).
- **Phase 3:** `session_store.py` jsonl audit log, reconnect logic,
  approval timeouts, completion notifications.

See [`PLAN.md`](./PLAN.md) for the full multi-phase roadmap.

---

## License

Released under the [GNU General Public License v3.0](./LICENSE).
You're free to use, modify, and redistribute under the terms of GPLv3.
Contributions welcome via pull request.
