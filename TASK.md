# Agent Babysitter — Active Task List

> Internal work log. Pick a task, build it, strike it off.
> For the public feature roadmap see [FEATURES.md](./FEATURES.md).

---

## Legend

| Tag | Meaning |
|-----|---------|
| 🔴 **bug** | Something broken |
| 🟣 **feat** | New capability |
| 🔄 **refactor** | No new behaviour, cleaner internals |
| 🧪 **test** | Missing or flaky coverage |
| 📖 **docs** | Docs / comments / GUIDELINE updates |

Priority: **P0** (do now) → **P1** (next session) → **P2** (when we have time)

---

## P0 — In Progress / Blocking

- [x] 🔴 **Telegram bridge timeout on startup**
      PTB v22 default 5 s connect timeout too short for first cold connection.
      **Fix:** `_make_app()` now passes `connect_timeout=30`, `read_timeout=30`,
      `write_timeout=30`, `pool_timeout=10` to `Application.builder()`.
      `telegram_bridge.py:188`

---

## P1 — Next Up

### Telegram reliability
- [ ] 🔴 **Bridge start retry on transient timeout**
      If `start()` raises `TimedOut`, retry up to 3× with exponential backoff
      before giving up and setting `self.telegram = None`.
      _File:_ `cli.py:_maybe_start_telegram` (line ~1248)

- [ ] 🔴 **`/telegram on` should re-attempt with feedback**
      Currently shows a one-liner "still not connected". Should show attempt
      count and final error reason so user knows what failed.
      _File:_ `cli.py:_handle_telegram_toggle` (line ~920)

- [ ] 🟣 **Telegram reconnect on network drop**
      If the bridge loses its connection mid-session (e.g. WiFi roam),
      auto-reconnect with backoff without requiring a restart.
      _File:_ `telegram_bridge.py:start / stop`

### Policy engine
- [ ] 🟣 **Per-tool block in `policy.yml`**
      Let users declare e.g. `WebFetch: escalate`, `Bash: { rm: deny }`.
      Needs schema in `policy_engine.py` + GUIDELINE docs update.

- [ ] 🟣 **`/policy reload` command**
      Reload `~/.abs/config/policy.yml` at runtime without restarting abs.
      _File:_ `cli.py` (new slash command), `policy_engine.py`

### UX
- [ ] 🔴 **Claude result message truncated / not fully visible**
      When Claude finishes a task the result panel doesn't show the complete
      response — the bottom lines are cut off or the panel doesn't scroll to
      fit the full output. User has to look at the raw tmux pane to read the
      whole message.
      _Likely files:_ `cli.py` (mirror/result panel rendering), check panel
      height calculation and whether long replies are being clipped to a fixed
      line budget. Also check `extract_assistant_reply` in
      `conversation.py` — may be returning a truncated slice.
      _Steps to repro:_ ask Claude something that produces a long reply (>20
      lines) and observe the result panel in abs.

- [ ] 🟣 **Better mirror diff — highlight what changed since last capture**
      Instead of replacing the whole mirror panel, colour added/removed lines
      so the user can see at a glance what Claude just wrote.
      _File:_ `cli.py` or wherever the mirror panel is rendered

- [ ] 🟣 **Emoji-free mode** (`--no-emoji` flag or `policy.yml: emoji: false`)
      Strips icons from approval cards, completion messages, and the input
      label. Helps users on fonts that don't render emoji cleanly.

---

## P2 — Backlog

### More LLM backends
- [ ] 🟣 **OpenAI backend for summaries**
      `gpt-4o-mini` is cheap; adds value for users without Anthropic keys.
      Needs new `llm_test.py` entry + `setup_wizard.py` flow.

- [ ] 🟣 **Ollama backend**
      For offline / privacy-first users. Point at `http://localhost:11434`.
      Same `agent.py` interface, just a different HTTP call.

### Multi-pane
- [ ] 🟣 **Watch multiple Claude panes in one abs instance**
      Core design question: one `BridgeUI` subclass per pane, or one
      multiplexed loop? Start with a design spike before coding.

### Security
- [ ] 🟣 **Rate-limit Telegram inbound**
      Bucket per `chat_id`, max N messages per minute. Drop & log excess.
      _File:_ `telegram_bridge.py:_on_telegram_message`

- [ ] 🟣 **`pip-audit` in CI**
      Add a GitHub Actions step that fails the build if any dependency has
      a known CVE. Add to `.github/workflows/`.

### Memory / personalisation
- [ ] 🟣 **Per-project `policy.yml` override**
      If `.abs/policy.yml` exists in the cwd, shadow the global one.
      _File:_ `policy_engine.py` (loader) + `_paths.py`

### Tests
- [ ] 🧪 **`test_telegram_bridge.py` — cover `TimedOut` retry path**
      Once the retry logic (P1 above) is in, add tests for:
      1× retry succeeds, 3× all fail, non-timeout exception not retried.

- [ ] 🧪 **`test_policy_engine.py` — per-tool block coverage**
      Once per-tool policy (P1 above) is in, cover allow / deny / escalate
      for each tool category.

- [ ] 🧪 **Integration smoke test** (`tests/integration/`)
      Launch a mock tmux pane, fire a sequence of approval prompts, assert
      the right keys are sent. No real Claude needed — fake pane output.

---

## Ideas / Parking Lot

Things we've discussed but haven't decided on yet:

- **Streaming Telegram summaries** — start writing the card while LLM is
  generating. Needs PTB `editMessageText` + streaming Anthropic SDK.
- **Diff summaries for Edit/MultiEdit** — summarise the diff not the full
  result. Saves Telegram chars, more scannable on phone.
- **Session cost dashboard** — track tokens / USD per session in the
  `events.jsonl` and show a running total in the header.
- **Cross-machine bridging** — abs on laptop, Claude Code on remote server
  via SSH + tmux. Architecture TBD.

---

_Last updated: 2026-05-28 — Pranjal_
