# Task list — after 3.6.2

Written 16 Sep 2026 from a Telegram conversation, so nothing here depends on
remembering it. Order is the order we agreed. Tick things here as they ship;
this file is the source of truth until it is empty.

## Where prompts enter a session (the map this list is built on)

| Level | What | Who owns it | Editable today |
|-------|------|-------------|----------------|
| Session start | the ABS system prompt, built by `build_prompt` in `abs.sh`, passed as `--append-system-prompt` (~3.5k tokens) | ABS | only by editing `abs.sh` |
| Session start | `~/.claude/CLAUDE.md` (personal) and any project `CLAUDE.md` | Claude Code / the user | yes, by hand |
| Session start | auto-memory index `MEMORY.md` | Claude Code / the model | yes, by hand |
| Per turn | hook directives on a control phrase (ABS STOP / EXIT / UNMUTE …), printed by the UserPromptSubmit hook | ABS | only by editing `abs.sh` |
| Per tool call | one-line hook feedback ("delivered as a voice note … do NOT resend", command-guard refusal) | ABS | only by editing `abs.sh` |

Anthropic's own Claude Code system prompt sits under all of these and is not ours.

---

## 1. `abs prompt` — see and edit what goes into Claude  →  3.7.0

The operator's ask: *"interactive beautiful terminal commands … a table-like
structure where the user can see what the prompt says and can edit it, so they
have control over what goes into Claude and how it works."*

Design constraints come from `docs/PERSONA-AND-MEMORY.md` (decided 18 Aug):
the prompt is assembled in a fixed order — **bridge mechanics (locked) →
persona (editable) → safety epilogue (locked)** — so a persona that says
"ignore previous instructions" is itself followed by the non-negotiables. The
persona is one global file, never project-local.

- [ ] **Split `build_prompt` into three slots.** Mechanics: who is on the other
      end, the reply tool, the fallback, quiet mode, command menu, voice
      procedure, screenshots. Persona: TONE, WHAT MAKES A REPORT, MESSAGE TYPES,
      STAYING ON THE TASK, emoji table. Safety: SAFETY, COMMAND GUARD, HARD OFF,
      the kill-ladder obligations.
- [ ] **Trim the duplication first.** WHEN TO SEND / WHAT MAKES A REPORT /
      MESSAGE TYPES / THE VOICE NOTE IS THE ANSWER overlap by roughly a third.
      One rule, said once. Target: ≤ 2,500 tokens total without losing a rule.
- [ ] **`~/.abs/persona.md`** — the persona slot on disk. Shipped default =
      the trimmed persona sections, so upgrading changes nothing. Length-capped;
      a file containing `<channel` is rejected outright. Missing file = default.
- [ ] **Hook directives on disk too** — `~/.abs/hooks.md` (or one section per
      phrase): the *wording* of the STOP / EXIT / UNMUTE / COMPACT paragraphs is
      editable; the *enforcement* (mute, off, block, guard) stays in `abs.sh`
      and is not.
- [ ] **`abs prompt`** — interactive, using the existing scrolling picker:
      a table of sections with columns *section · slot · lines · locked/editable
      · source file*. Enter on a row shows it; `e` opens it in `$EDITOR`
      (persona and hooks only); `r` resets one section to the shipped default;
      locked rows say why. Non-interactive forms for scripts and docs:
      `abs prompt show [section]`, `abs prompt edit persona|hooks`,
      `abs prompt reset persona|hooks`, `abs prompt diff` (yours vs default).
- [ ] **`abs prompt show --built`** prints exactly what the next launch will
      pass, so the user can always read what Claude actually got.
- [ ] Tests: the assembled order is mechanics → persona → safety whatever the
      persona says; an over-long or `<channel`-bearing persona is refused; a
      missing file yields the default byte-for-byte; bash 3.2 in the container.
- [ ] Docs: GUIDE section, website docs page, CHANGELOG.

Not in this item: per-project memory / `ABS REMEMBER` (step 2 of the persona
design) — a separate feature, not asked for.

## 2. Voice: a file name is swallowed  →  small, fold into 3.7.0

Reported 16 Sep: in *"It is your own CLAUDE.md in your home directory"* the
note skipped straight to *"md in your home directory"* — the words before the
dot were not spoken. The engine's text normaliser treats the `.` in a file name
as a sentence end, and something on that path drops the fragment.

- [ ] Reproduce with `speak_kokoro.py` on that sentence; find which side drops
      it (the sentence splitter in `split_chunks`, or the engine).
- [ ] Fix in `_voice_prep`: a `name.ext` token becomes *"name dot ext"* before
      synthesis, the same way `3.6.2` already becomes *"3 point 6 point 2"*.
      Extensions to cover: md sh py txt json yml yaml html js ts toml env lock.
- [ ] Test: the prepared text of that sentence contains "CLAUDE dot md" and the
      note is spoken whole (stub engine).

## 3. ABS COMPACT, in place, on tmux  →  3.8 — parked until 1 and 2 ship

Agreed 16 Sep, then deliberately set aside: *"it's not connected with this
task."* The design is fixed; only the timing moved.

- COMPACT must **not** end the session. Wait for the current turn to finish
  (Stop hook), send `/compact` into the running Claude Code, then report
  "session compacted" to Telegram (SessionStart hook, source=compact) and carry
  on.
- Primitive: run `claude` inside a tmux session per profile so `abs` can send
  keystrokes. The operator has used tmux with Claude and prefers it. This also
  makes `/model`, `/exit` and a real ABS STOP possible from the phone.
- Launch-path change ⇒ same Mac caution as 3.6.0: a manual checklist run on a
  real Mac before it touches `main`.
- Branch `abs-monitor` (3.7.0-beta.1, restart-based `abs loop`, never pushed) is
  **parked, not merged** — push it as a backup; do not build on it.
- The operator has *"other plans for the monitoring part"* — ask before
  touching anything named monitor.

## 4. Housekeeping — whenever

- [ ] Publish GitHub Release entries for v3.5.3, v3.6.0, v3.6.2 (the Releases
      page still says 3.3.0 is latest).
- [ ] `origin/beta` is the v1/cldx line from May — delete or rename so nobody
      mistakes it for a 3.x beta. Operator agreed it is disposable.
- [ ] `plugin-experiment` (local, July) — push as archive or drop.
- [ ] Website 3.6.2 is committed in `agentbabysitter-web` and needs a push from
      the terminal (production deploy — never from Telegram alone).
