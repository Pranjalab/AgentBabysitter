# cldx — Features & Roadmap

A living document of what's done, what's planned, and what's "would love
help with". Updated as the project grows.

For day-to-day usage of what's already shipped, see
[GUIDELINE.md](./GUIDELINE.md). For the project philosophy, see the
[README](./README.md).

---

## Done — v1.0.4 (current release)

### Core
- [x] Monitor a tmux pane via `tmux capture-pane`, fire `on_change` and
      `on_stable` callbacks.
- [x] Classify every snapshot — `APPROVAL_YN`, `APPROVAL_MENU`,
      `RUNNING`, `IDLE`, `COMPLETE`, `TEXT_INPUT`.
- [x] Structural classifier rule: a Claude turn starts with `⏺` and ends
      with `✻ <verb> for <time>`.
- [x] Built-in completion fallback so users with stale `policy.yml`
      still get correct classification.

### Policy engine
- [x] Five built-in profiles: `auto-approve`, `yolo`, `restricted`,
      `default`, `paranoid`.
- [x] Per-profile wait-bar duration (countdown before auto-fire).
- [x] Destructive-pattern safety floor that bypasses auto-approve.
- [x] Live profile switching via `/profile <name>` (terminal + Telegram).
- [x] Yolo learning — remembers your y/n choices per pattern.

### Tool classification
- [x] Typed `ToolCall` model with name / args / category / risk / icon /
      summary.
- [x] Registry covers Bash, Read, Edit, Write, Glob, Grep, LS,
      WebFetch, WebSearch, Task, TodoWrite, NotebookRead/Edit,
      MultiEdit, KillShell, BashOutput, SlashCommand, ToolSearch,
      Skill, ExitPlanMode, Run.
- [x] Multi-word display-name parsing (`Web Search` → `WebSearch`).
- [x] Bash risk refinement (`rm -rf` → destructive, `pip install` →
      elevated, `git push --force` → destructive, `sudo` → destructive,
      …).
- [x] File-write risk refinement (writes under `/etc`, `~/.ssh`,
      `~/.aws`, `~/.gnupg` → destructive).

### Telegram bridge
- [x] Two-way: outbound approval cards, inbound y / n / digit /
      free-form text.
- [x] Structured templates: approval, completion, escalation, greeting,
      error, help.
- [x] Slash commands: `/help`, `/start`, `/status`, `/panes`,
      `/snapshot`, `/stop`, `/cancel`, `/yes`, `/no`, `/pause`,
      `/resume`, `/profile`.
- [x] Auth boundary — messages from non-configured chat IDs are dropped.
- [x] Runtime `/telegram on` / `/telegram off` toggle in cldx terminal.
- [x] Auto-discovered chat ID via `getUpdates` in setup wizard.
- [x] Welcome card sent on first setup.

### LLM backends (for Telegram summaries)
- [x] Anthropic direct API.
- [x] AWS Bedrock (cross-region inference profiles auto-selected).
- [x] Google Gemini.
- [x] "Disabled" mode — raw sanitised pane sent to Telegram.

### UX
- [x] Bordered Claude-Code-styled input box (`prompt_toolkit`).
- [x] Dim-italic suggestion text + Tab-to-accept.
- [x] Dynamic header: `Claude + TMUX [+ Telegram] [(Resets at HH:MM)]`.
- [x] Three-tier decision panels — yellow (auto pending) / red (needs
      you) / green (done).
- [x] Mirror panel preserves Claude's own ANSI styling.
- [x] Arrow-key startup picker with delete-by-`d` for stale sessions.
- [x] Session-limit detection (`✻ You've hit your session limit · resets
      …`) + reset notifier.

### Reliability fixes shipped in 1.0.4
- [x] Signature dedup includes tool name to distinguish consecutive
      `Write(a.py)` / `Write(b.py)` approvals.
- [x] `extract_assistant_reply` anchors on latest user message (no
      cross-turn bleed).
- [x] Active prompts beat stale completion lines in the scrollback.
- [x] Telegram text-injection clears the completion lock (no silent
      Telegram replies).
- [x] Every Telegram reply reaches Claude (no "nothing pending" drops).

### Observability
- [x] Per-session human-readable interaction log
      (`~/.cldx/logs/YYYY-MM-DD/HH-MM-SS_*.log`).
- [x] JSONL machine-replayable event log
      (`~/.cldx/sessions/<profile>/*.jsonl`).
- [x] 508 passing tests covering classifier, policy engine, tool
      registry, Telegram parser, sanitiser, conversation extractor,
      session limit parser, completion lock flow.

---

## Near-term (next 1–2 releases)

The things we want to ship next. PRs especially welcome.

### Security
- [ ] **Audit of the application** — formal review of:
      - File-write paths and config persistence.
      - Telegram inbound auth (currently chat-id allowlist; want to
        document threat model).
      - Subprocess execution surface (`tmux send-keys`, `boto3`,
        `anthropic`).
      - Secret handling (mode-600 + `.env` parser).
- [ ] **Secrets at rest** — optional encryption of `~/.cldx/config/*.env`
      via the OS keyring.
- [ ] **PR / CI security scan** — `pip-audit` / `safety` on every PR;
      reject if upstream dependency has a known CVE.
- [ ] **Rate-limit Telegram inbound** — defence against accidental flood
      from a misconfigured bot.

### Per-tool policy
- [ ] **`per_tool:` block in `policy.yml`** — let users declare e.g.
      `WebFetch: escalate`, `Bash: { pip install: auto_yes, rm: deny }`.
- [ ] **Per-category profiles** — `read: auto_yes`, `write: wait_5s`,
      `exec: escalate`.
- [ ] **Live policy reload** — `/policy reload` after editing `policy.yml`
      without restarting cldx.

### More LLM backends
- [ ] **OpenAI** as a summary backend (gpt-4o-mini is cheap enough).
- [ ] **Local Ollama** for offline operation.
- [ ] **xAI Grok** if there's demand.

### UX polish
- [ ] Optional emoji-free mode for accessibility / terminal-font issues.
- [ ] Better mirror diff highlighting (show what changed since last
      capture).
- [ ] Persistent task durations — show "average completion time" in the
      status line.

---

## Mid-term

Bigger lifts. Likely require a non-trivial design conversation first.

### Multi-CLI bridge
- [ ] **Gemini CLI integration** — same auto-approval + Telegram
      pattern, but for Google's Gemini CLI. Same `cldx` binary, new
      backend.
- [ ] **Codex CLI integration** — when OpenAI ships their CLI publicly,
      bridge that too.
- [ ] **Generic "agent pane" abstraction** — any CLI that prints
      approval prompts can be wrapped.

### Multi-pane
- [ ] **Watch multiple Claude panes in one cldx instance** — one cldx,
      many agents, single Telegram chat for all.
- [ ] **Tab-style switcher** in the cldx terminal between active panes.

### Better summaries
- [ ] **Streaming summary** — start writing the Telegram summary while
      the LLM is generating (no "thinking..." dead air).
- [ ] **Persona library** — pick a summariser tone
      (`concise`, `verbose`, `engineer-speak`).
- [ ] **Diff summaries** — for Edit / MultiEdit calls, summarise the
      diff rather than the full result.

### Memory / personalisation
- [ ] **Cross-session yolo memory persistence** — share learned
      patterns across reinstalls.
- [ ] **Per-project policy overrides** — `.cldx/policy.yml` in a project
      dir shadows the global one.

---

## Long-term

Aspirational. Likely require significant new design.

### Other interfaces
- [ ] **Web UI** — local-only dashboard at `localhost:9876` to view
      panes / approvals from a browser.
- [ ] **Native mobile app** — iOS / Android client beyond Telegram.
- [ ] **Voice control** — accept y/n by voice through a phone
      integration.

### Agent shaping
- [ ] **Custom "auto-approve" policies as code** — write a small Python
      function that returns the decision instead of editing YAML.
- [ ] **Confidence-weighted summaries** — LLM tells us how sure it is the
      task is done; we flag uncertain completions for review.
- [ ] **Multi-step approval chains** — let cldx ask Claude to break down
      a destructive op into safe steps before approving.

### Ecosystem
- [ ] **Plugin system** — third-party tools / templates loadable from
      `~/.cldx/plugins/`.
- [ ] **Hosted setup helper** — generate `~/.cldx/config/*.env` from a
      web form (no keys touch our servers; output is downloadable).
- [ ] **Public benchmark suite** — standard "agent dev scenarios" to
      measure cldx's auto-approval accuracy across releases.

---

## How to propose a feature

1. **Open an issue** at <https://github.com/Pranjalab/cldx/issues>.
   Lead with the problem (not your proposed solution). Examples:
   - "I want to disable auto-approval for `WebFetch` specifically"
     (good — describes a goal)
   - "Add `--no-webfetch` flag" (less good — already a solution; the
     problem might be better solved by per-tool policy)

2. **Tag it** with `feature-request`. If it matches something already
   listed above, link to the section.

3. **For small / obvious features**, open a PR directly. Match the
   surrounding style. Add tests. Update GUIDELINE.md if user-visible.

4. **For controversial features**, expect discussion. cldx tries to stay
   small. "Why not just …" is a real answer. We'd rather ship one
   tightly-designed thing than ten half-baked ones.

5. **For security-sensitive features**, please email maintainers
   privately first (don't open a public issue with the attack vector
   exposed).

---

## "Will not ship"

A small list of things we've explicitly decided against, so people don't
need to keep asking:

- **Cloud-hosted multi-tenant cldx.** Not the design. cldx is your local
  process talking to your local tmux and your personal Telegram bot.
  Any "hosted" version would create the exact privacy / control problem
  cldx exists to avoid.
- **A non-GPL re-license for proprietary forks.** If you need a custom
  arrangement, open an issue. Default is GPL-3.0-or-later — share
  modifications back.
- **Auto-approving destructive ops** (no matter how confident the LLM is).
  The safety floor is non-negotiable. You can edit `destructive_patterns:`
  to ADD entries, never to silently bypass them.
- **Telemetry by default.** cldx never phones home. The session log
  stays on your disk. The Telegram bot is yours.

---

If something on this list excites you, **pick one and start the
conversation**. cldx grows because real users say "here's the workflow
this would unlock for me".
