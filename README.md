# cldx

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

Second-layer terminal for Claude Code. Monitors a `tmux` pane,
classifies prompts, auto-responds based on a policy you control, and
(in upcoming phases) bridges to Telegram so you can review and reply
when you're away from your laptop.

> **Status:** Phase 1 (done) — local monitoring & auto-response.
> See [`PLAN.md`](./PLAN.md) for the full product roadmap.

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
3. Creates `~/.cldx/config/policy.yml` (the user-editable policy) and
   `~/.cldx/sessions/` (where event logs will go).
4. Tells you exactly what `export PATH=...` line to add to `~/.zshrc`
   or `~/.bashrc` if `cldx` isn't immediately findable.

Override the state location with `export CLDX_HOME=/some/other/path`.

To uninstall: `./install.sh --uninstall` (leaves `~/.cldx/` in place).

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
cldx --auto-detect            # most common
cldx --session work:0.0       # specify a pane
cldx --list-panes             # see what tmux is reporting
cldx --profile yolo           # override the active policy profile
cldx --dry-run                # classify + decide, never send keys
```

You can also run the module form: `python -m cldx --auto-detect`.

## Setup

```bash
cldx setup              # interactive wizard for both Anthropic + Telegram
cldx setup anthropic    # just the Claude API key (used by the summarizer)
cldx setup telegram     # bot token + auto-discovers your chat ID
cldx config show        # masked summary of what's configured + where it lives
```

The Anthropic wizard validates the key against the real API with a 10-token
call (~$0.0000001). The Telegram wizard creates the bot via your message
to `@BotFather`, then auto-discovers your chat ID by polling
`getUpdates` after you message your new bot once.

All secrets land in `~/.cldx/config/{anthropic,telegram}.env` with mode
`0600`. They're loaded into the process environment on every `cldx` run,
so `ANTHROPIC_API_KEY` and `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`
become available to the summarizer and Telegram bridge without any
shell-rc setup. (You can still override via `export VAR=...` if you
prefer — file values don't clobber a parent-shell environment.)

### Useful flags

| Flag                  | Purpose                                                       |
|-----------------------|---------------------------------------------------------------|
| `--session S:W.P`     | Skip discovery and watch this pane.                           |
| `--auto-detect`       | Watch the first pane that looks like Claude Code.             |
| `--profile NAME`      | Override `active_profile` from `policy.yml`.                  |
| `--policy PATH`       | Use a custom policy file (overrides `~/.cldx/config/...`).    |
| `--poll-interval N`   | Seconds between snapshots (default `1.0`).                    |
| `--mirror-lines N`    | How many tail lines of the Claude pane to mirror (default 25).|
| `--list-panes`        | Print all tmux panes (with command + title) and exit.         |
| `--dry-run`           | Classify and decide, but never send keys.                     |
| `--version`           | Print the installed version.                                  |

---

## Policy

`~/.cldx/config/policy.yml` ships with four profiles. Future phases will
replace these with the three profiles described in `PLAN.md` (auto-approve,
yolo, restricted) plus a configurable wait-bar interval.

Each profile evaluates patterns in this order:
`auto_deny → auto_approve → escalate_to_telegram → default_action`.

Patterns are case-insensitive Python regex, matched against the
extracted command and the surrounding pane context.

---

## File structure

```
cldx/                              # Python package
├── __init__.py                    # __version__
├── __main__.py                    # `python -m cldx`
├── cli.py                         # entrypoint + TUI
├── _paths.py                      # ~/.cldx/ resolver
├── session_picker.py
├── tmux_monitor.py
├── tmux_controller.py
├── prompt_classifier.py
├── policy_engine.py
└── defaults/
    └── policy.yml                 # bundled default policy

install.sh                         # one-shot installer
pyproject.toml                     # build + entry point
LICENSE                            # GPL-3.0
PLAN.md                            # product roadmap
README.md                          # this file
tests/                             # 79 passing + phase scaffolds
```

User state at runtime: `~/.cldx/` (override with `$CLDX_HOME`).

---

## Development

```bash
# Set up a dev env (no install needed for tests):
python3.12 -m pip install -r requirements-dev.txt

# Run the suite:
pytest -q                          # 79 passing, 57 phase-scaffolded

# Install the package in editable mode for end-to-end runs:
pip install --user -e .
cldx --version
```

See `tests/README.md` for the test layout and how to add coverage as
each phase ships.

---

## Roadmap

- **Phase 1 (done):** package + installer + local monitor + auto-respond.
- **Phase 2:** session storage (`~/.cldx/sessions/*.jsonl` + replay).
- **Phase 3:** three policy profiles + configurable wait bar with override.
- **Phase 4:** startup greeting + session picker / spawn-new flow.
- **Phase 5:** yolo learning (memory.json of approved patterns).
- **Phase 6:** agent persona + Claude API summarizer.
- **Phase 7:** Telegram bridge.

Full detail in [`PLAN.md`](./PLAN.md).

---

## License

Released under the [GNU General Public License v3.0](./LICENSE).
Contributions welcome via pull request.
