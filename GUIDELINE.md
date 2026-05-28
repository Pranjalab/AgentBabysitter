# Agent Babysitter — User Guideline

The complete reference for running abs day-to-day: every flag, every slash
command (terminal AND Telegram), every policy profile, and the things that
trip people up the most. Pair this with the [README](./README.md) (the
overview + install) and [FEATURES.md](./FEATURES.md) (what's coming next).

## Table of contents

- [First-run checklist](#first-run-checklist)
- [Starting abs](#starting-abs)
- [Policy profiles](#policy-profiles)
- [Tool categories & risk levels](#tool-categories--risk-levels)
- [Terminal slash commands](#terminal-slash-commands)
- [Telegram slash commands](#telegram-slash-commands)
- [Reply formats](#reply-formats)
- [Setup wizards](#setup-wizards)
- [LLM backends](#llm-backends)
- [Files & state](#files--state)
- [Troubleshooting](#troubleshooting)
- [Advanced](#advanced)

---

## First-run checklist

1. **Install:** `./install.sh` (see [README](./README.md#install))
2. **Optional — set up Telegram:**
   ```bash
   abs setup telegram
   ```
   This walks you through `@BotFather`, captures your `chat_id`, sends a
   welcome card to your phone.
3. **Optional — pick an LLM backend** for Telegram summaries:
   ```bash
   abs setup llm
   ```
   Pick Anthropic, Bedrock, Gemini, or "disabled" (raw pane forwarded).
4. **Start a Claude Code session in tmux:**
   ```bash
   tmux new -s claude
   # inside the tmux pane:
   claude
   ```
5. **Run abs** in another window:
   ```bash
   abs --auto-detect
   ```

---

## Starting abs

### Command-line flags

```
abs [SUBCOMMAND] [OPTIONS]
```

| Flag | What it does |
|---|---|
| `--session <id>` | Attach to a specific tmux pane (e.g. `0:0.0`) |
| `--auto-detect` | Find the only running Claude pane and attach |
| `--list-panes` | Print all tmux panes + what's running, then exit |
| `--policy <path>` | Use a non-default policy YAML |
| `--poll-interval <s>` | Pane-capture interval (default `1.0`) |
| `--mirror-lines <n>` | Lines of pane mirror to display (default `25`) |
| `--dry-run` | Classify everything but never send keys to tmux |
| `--no-telegram` | Disable Telegram for this run (overrides config) |
| `--no-llm` | Skip the LLM step — raw pane content goes to Telegram |
| `--verbose` | Extra log output |
| `--version` | Print version and exit |

### Subcommands

| Subcommand | What it does |
|---|---|
| `abs setup` | Interactive — picks LLM backend + Telegram, prints final config |
| `abs setup llm` | Just the LLM backend picker |
| `abs setup anthropic` | Save an Anthropic API key, optionally test it |
| `abs setup bedrock` | Save an AWS Bedrock bearer token + region + model |
| `abs setup gemini` | Save a Google Gemini key + model |
| `abs setup telegram` | Save a bot token, auto-discover chat ID, send greeting |
| `abs setup all` | Same as `abs setup` |
| `abs config` | Show every configured secret (masked) + LLM backend |
| `abs test llm` | End-to-end LLM smoke test (runs all three summary modes) |

---

## Policy profiles

`policy.yml` ships with five profiles. Switch live with `/profile <name>` or
override at startup with `--profile`. Edit `~/.abs/config/policy.yml` to
add your own.

| Profile | Default action | Use when |
|---|---|---|
| **auto-approve** | All approvals auto-yes (2 s wait) | You trust your prompt + want a hands-off run |
| **yolo** | Auto-yes everything, *learn* from your y/n on the few that escalate | You're iterating and want to teach abs your patterns |
| **restricted** | Auto-yes for read-only / search tools; escalate writes & exec to Telegram | Day-to-day with safe defaults |
| **default** | Same as restricted but no Telegram escalation | Local-only, no phone bridge |
| **paranoid** | Escalate every approval, no auto-fires | When you want to review everything |

The wait bar (`auto-approve` and `yolo` profiles) gives you N seconds before
firing — type *anything* in the terminal during that countdown to cancel.

### Safety floor (always on)

These bypass auto-approve regardless of profile and require an explicit
y/n from you:

- `rm -rf` / `rm -r --recursive`
- `dd if=/dev/...`
- `mkfs.*`
- `chmod -R` / `chown -R`
- `sudo` anything
- `git push --force` / `git reset --hard` / `git clean -fd`
- `Write` / `Edit` of files under `/etc`, `/var`, `/usr`, `~/.ssh`, `~/.aws`, `~/.gnupg`
- Fork bombs and `/dev/sd*` writes

You can add to the list in `policy.yml` under `destructive_patterns:`.

---

## Tool categories & risk levels

Every approval is tagged with a tool category and a risk level. These show
up in the decision panel and the Telegram card.

| Category | Tools | Default risk |
|---|---|---|
| **read** | Read, BashOutput, NotebookRead | safe |
| **search** | Glob, Grep, LS, WebSearch | safe |
| **write** | Write, Edit, MultiEdit, NotebookEdit | elevated |
| **exec** | Bash, Run, KillShell | normal (refined per-arg) |
| **fetch** | WebFetch | elevated |
| **agent** | Task | elevated |
| **meta** | TodoWrite, Skill, ToolSearch, SlashCommand, ExitPlanMode | safe |
| **other** | Any unknown tool | normal |

### Bash risk refinement

Bash gets risk-refined by the arguments. Examples:

| Command | Risk |
|---|---|
| `ls -la` | normal |
| `git status` | normal |
| `mkdir -p /tmp/foo` | normal |
| `pip install requests` | elevated |
| `curl ... \| bash` | elevated |
| `docker run -it ubuntu` | elevated |
| `rm -rf /tmp/test` | **destructive** |
| `git push --force origin main` | **destructive** |
| `sudo apt install foo` | **destructive** |

---

## Terminal slash commands

Anything not starting with `/` is typed into Claude. The commands below are
abs-specific — they never reach Claude.

### Approval shortcuts (only when a prompt is pending)

| Command | Effect |
|---|---|
| `/y` or `/yes` | Approve the pending prompt |
| `/n` or `/no` | Deny the pending prompt |
| `/<digit>` | Pick the numbered menu option |
| `/skip` | Clear the pending state without acting |

### Telegram

| Command | Effect |
|---|---|
| `/telegram` | Show current Telegram state |
| `/telegram on` | Enable forwarding (starts bridge if needed) |
| `/telegram off` | Silence outbound Telegram cards (inbound still works) |

### Inspection

| Command | Effect |
|---|---|
| `/snapshot` | Print the abs view of the pane + classifier output |
| `/refresh` | Reprint the mirror panel |
| `/panes` | List tmux panes (active one marked) |

### Modes

| Command | Effect |
|---|---|
| `/profile` | Show current policy profile |
| `/profile <name>` | Switch profile (e.g. `/profile yolo`) |

### Raw / exit

| Command | Effect |
|---|---|
| `/raw <keys>` | Send named tmux keys to the pane (e.g. `/raw C-c`) |
| `/quit` (or `/q`, `/exit`) | Cleanly exit abs |

---

## Telegram slash commands

Send these from your phone to the bot. They never get injected into Claude.

| Command | Effect |
|---|---|
| `/help` (or `/start`) | Show the command list |
| `/status` | Active pane, profile, paused state, pending prompt |
| `/panes` | List tmux panes |
| `/snapshot` | Send the current pane snapshot |
| `/stop` | Send ESC to Claude — interrupt the current task |
| `/cancel` | Clear the pending prompt without acting |
| `/yes` / `/no` | Explicit y/n on the pending approval |
| `/pause` | Stop auto-approving (approvals queue) |
| `/resume` | Re-enable auto-approval |
| `/profile` | List profiles |
| `/profile <name>` | Switch profile |

---

## Reply formats

When Claude is waiting for input (either an approval menu or a chat prompt),
you can reply from either the abs terminal or Telegram. abs parses your
reply into one of these intents:

| You send | Interpretation when approval is pending | Interpretation when nothing pending |
|---|---|---|
| `y`, `yes`, `ok`, `👍` | Approve | Text → Claude |
| `n`, `no`, `stop`, `👎` | Deny | Text → Claude |
| `1`, `2`, `3` | Pick menu option | Text → Claude |
| Any other text | Inject as Claude input | Inject as Claude input |

The rule: **every reply reaches Claude**. Never dropped.

---

## Setup wizards

Each wizard is paste-tolerant (bracketed paste works for >1 KB tokens on
macOS), saves to `~/.abs/config/*.env` with `chmod 600`, and offers an
optional test call.

### Anthropic

```bash
abs setup anthropic
```
Asks for a key starting with `sk-ant-`. Test call uses `claude-haiku-4-5`
and costs ~$0.0001.

### AWS Bedrock

```bash
abs setup bedrock
```
Asks for an AWS bearer token, region, and model ID. Defaults the model to
the right cross-region inference profile for your region (`us.*`, `eu.*`,
`apac.*`). On validation errors, queries Bedrock for available models and
offers an inline retry.

### Google Gemini

```bash
abs setup gemini
```
Asks for a Gemini API key (`https://aistudio.google.com/apikey`). Defaults
the model to `gemini-2.0-flash`. Free tier is plenty for abs summaries.

### Telegram

```bash
abs setup telegram
```
1. Walks you through messaging `@BotFather`.
2. Validates the token via `getMe`.
3. Asks you to send any message to your bot.
4. Auto-discovers your `chat_id` via `getUpdates`.
5. Sends a connection ping + the full greeting card (with command list).
6. Saves to `~/.abs/config/telegram.env` (mode `600`).

### Show current config

```bash
abs config
```
Prints a masked view of every configured secret + which file it's in.

---

## LLM backends

abs uses an LLM to summarise Claude's pane content before sending it to
Telegram. The backend is selectable per-installation.

### Which one should I pick?

- **Easiest / best quality:** Anthropic direct (~$0.0001 / summary with Haiku).
- **Already paying for AWS:** Bedrock — uses your AWS account, no separate key.
- **Want a free option:** Gemini — generous free tier covers casual abs use.
- **Want zero LLM dependency:** disable. Telegram gets the raw `⏺...✻` slice,
  sanitised for chat readability.

### Switching backends

Re-run `abs setup llm` and pick a new option. It rewrites
`~/.abs/config/agent_name.yml` with the new `model:` field.

### How LLM disabled works

Set `model: none:raw` (or pick option 4 in the wizard). abs skips every
upstream API call. The raw conversation step (after sanitisation: ANSI
stripped, box-drawing chars removed, banners dropped, runs of separator
lines collapsed) is sent to Telegram.

---

## Files & state

```
~/.abs/
├── config/
│   ├── policy.yml          # the active policy (auto-copied from defaults on first run)
│   ├── agent_name.yml      # LLM backend + persona
│   ├── anthropic.env       # mode 600 — your API key
│   ├── bedrock.env         # mode 600 — bearer token + region
│   ├── gemini.env          # mode 600 — Gemini key
│   └── telegram.env        # mode 600 — bot token + chat ID
├── sessions/
│   └── <profile>/
│       └── 2026-05-27T20-25-13.jsonl   # machine-replayable event log
└── logs/
    └── 2026-05-27/
        └── 21-32-15_auto-approve_0-0-0.log   # human-readable interaction log
```

### Where to look

- **What did abs do at 9:17 PM?** → `~/.abs/logs/<today>/`
- **What approval did I decline last Tuesday?** → grep the JSONL session file
- **What's my current policy?** → `cat ~/.abs/config/policy.yml`
- **Did Telegram actually send?** → check the `cldx_action` / `telegram_out` lines in the latest log

---

## Troubleshooting

### "unknown command: /telegram on"

You're running an older `abs` binary. Either an earlier install used a
different Python's user-scripts dir and is shadowing the fresh one, or
PATH resolves to the stale one. Run `./install.sh` again — the verify
step at the end now prints the exact `ln -sf` one-liner to fix it.

### "PATH resolves to X, but this install wrote to Y"

The installer detected a stale binary. Run the one-liner it suggests, e.g.

```bash
ln -sf ~/Library/Python/3.13/bin/abs ~/.local/bin/abs
```

### Claude finishes but abs is silent on Telegram

Three things to check, in order:

1. **Telegram running?** `/telegram` in the terminal — must say `on`.
2. **Configured?** `abs config` should show a `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`.
3. **Reach you?** `abs setup telegram` re-sends the greeting card.

### Auto-approve doesn't fire on a known-safe tool

Check `abs config` and the policy file. Likely candidates:

- You're in the `paranoid` or `restricted` profile (escalates by default).
  Switch with `/profile auto-approve`.
- A `destructive_patterns:` rule matched. Inspect the `matched:` line in
  the decision panel.
- The tool is new and not in abs's registry. Open an issue with a sample
  snapshot — registry additions are one-line PRs.

### Pasting tokens longer than 1 KB on macOS

The setup wizards use `prompt_toolkit`'s bracketed-paste mode, not raw
`input()`, so >1 KB pastes work. If you're piping in a token via stdin or
a non-TTY (unusual), it falls back to `input()` which respects macOS's
1 KB canonical-mode limit.

### Session resets at the wrong time

abs parses the time string Claude prints (`resets 7:50pm` etc.) in the
declared timezone (parenthesised) or your system local timezone if none
given. If your laptop's clock is wrong, the parsed reset will be off.

### Chat reply not showing up in the green panel

Make sure you have v1.0.4 or newer — earlier versions had a known bug
where the panel only showed truncated content and dropped multi-step
turns. Run `abs --version` to confirm.

---

## Advanced

### Multiple Claude sessions

Run `tmux new -s claude1`, `tmux new -s claude2`, etc. abs's startup
picker shows you all panes with arrow-key navigation; `d` deletes a stale
recorded session.

### Resume from a prior session

The startup picker shows recent sessions newest-first. Picking one
replays the event log as a transcript before going live.

### Running headlessly (no tmux already open)

The picker offers "Start a new tmux + claude" — it runs
`tmux new -d -s abs-N && tmux send-keys claude Enter` and attaches.

### Custom policy.yml

Copy the bundled default and edit:

```bash
cp abs/defaults/policy.yml ~/.abs/config/policy.yml
$EDITOR ~/.abs/config/policy.yml
```

Fields you might tune:

- `active_profile:` — switch the default
- `<profile>.wait_interval_seconds:` — change the countdown duration
- `destructive_patterns:` — add your own dangerous-command regexes
- `<profile>.approved_patterns:` / `denied_patterns:` — pre-seed yolo memory
- `detection.*_patterns:` — extend classifier triggers (rarely needed; abs has built-in fallbacks)

### Dry-run mode

```bash
abs --auto-detect --dry-run
```

Classifies and panels everything, but never sends keys to tmux. Useful
when tuning policy patterns or auditing what abs would do on a known
workflow.

### CI / automated runs

Use `--no-telegram --dry-run` for a fully offline test. abs still
writes the JSONL session log so you can grep classifications later.

### Environment variables

| Variable | Effect |
|---|---|
| `ABS_HOME` | Override `~/.abs/` (state dir) |
| `ANTHROPIC_API_KEY` | Anthropic API key (also loaded from config file) |
| `AWS_BEARER_TOKEN_BEDROCK` | Bedrock token |
| `AWS_REGION` / `AWS_DEFAULT_REGION` | Bedrock region |
| `GEMINI_API_KEY` | Gemini key |
| `TELEGRAM_BOT_TOKEN` | Bot token |
| `TELEGRAM_CHAT_ID` | Chat ID |
| `TELEGRAM_APPROVAL_TIMEOUT_SECONDS` | How long to wait for a Telegram reply before fallback (default `600`) |
| `TELEGRAM_TIMEOUT_ACTION` | `auto_no` (default) or `auto_yes` on timeout |

---

## Reporting bugs

Open an issue at <https://github.com/Pranjalab/AgentBabysitter/issues> with:

1. `abs --version` output.
2. A pane snapshot if relevant (`/snapshot` in abs shows what it's seeing).
3. The last few lines from your log under `~/.abs/logs/`.
4. What you expected vs what happened.

Patches especially welcome for:

- New tool entries in `abs/tool_call.py` (Claude adds tools regularly).
- New destructive patterns in `abs/defaults/policy.yml`.
- Edge-case classifier inputs (paste a real snapshot, we'll turn it into a test).
