# Manual test — 3.7.0, from the laptop

Written 17 Sep for the operator's own run, then cut down the same day: every
check a machine can make has been made (1,188 tests; a 37-step headless sweep
of the page; ^Q and Esc in a real pty; the new commands executed under real
bash 3.2 in the container; a real Kokoro synthesis of "CLAUDE.md" transcribed
back with the words intact; a source install that built a venv with Textual).
Lines marked ✓ below are those. What is left needs eyes, a Mac, or a live
Claude session — a person.

Setup: `cd ~/Projects/research/AgentBabysitter && git checkout abs-prompt && git pull`.
Run everything as `bash abs.sh …` from that directory so you are on the branch,
not the installed copy.

## 0. Only a person can do these — five things, ~20 minutes

Colours and theme: approved by the operator on 19 Sep. Voice by ear: replaced by
two Kokoro → Whisper round trips (a file-name sentence and a 4,500-character
reply split into two notes, every sentence heard, no "carry" phrase). What is
left is Claude's own behaviour under the new prompt, and a Mac.

- [ ] **Session A — default** (`bash abs.sh`, then from the phone): "what's your name?" → ABS. Then one small task → 🛠 ACK first; on finish voice note + transcript + card with seven headings, and the card says ready to commit — and nothing was committed. Then, in the same session: `ABS STOP` mid-task → halts and says so; `ABS REVIEW` (create it first on the Hooks tab) → acts on your wording.
- [ ] **Session B — friend** (`bash abs.sh --persona friend`): "hello" → warm, short, no card.
- [ ] **Session C — cto** (`bash abs.sh --persona cto`): one small two-part task → plan, asks your agreement, subagents, one report. Strict enough?
- [ ] **Shipping**: in any session, with a change ready, "ship it" → ONE question naming action + target; nothing pushed until yes; not asked twice.
- [ ] **Mac**: `abs update` from 3.6.2, then `abs prompt` opens the page (Textual arrived); ^Q quits; ^PgDn switches tabs.

## 1. The page — `bash abs.sh prompt`

- [✓] Opens on **Overview**. Token counts, paths, active persona, keys all read right.
- [ ] Colours: navy background, violet active tab and focus borders, round corners. Contrast OK on your terminal?
- [✓] **^Q quits** from the Overview. Reopen. **Esc quits.** Reopen.
- [✓] Tabs: F1–F7 switch; **^PgDn / ^PgUp** step and wrap; **clicking** a tab works.
- [✓] Footer shows only the actions (^S ^R ^N ^T ^U ^Q) — nothing cut off at your width.
- [✓] **System**: read-only; VOICE block and the `Reply mode is 'both'` block are present (not empty headings).
- [✓] **Persona**: list shows default · ceo · cto · friend, default marked active. Select cto — editor shows ROLE — CTO; nothing becomes "unsaved" from just looking.
- [✓] Persona: type one character in cto, ^S → toast "Saved cto"; `ls ~/.abs/personas/` shows cto.md.
- [✓] Persona: ^N → name `reviewer` → editor opens with the copy → ^S → file exists.
- [✓] Persona: select friend, ^U → "● active" moves to friend; `cat ~/.abs/persona.active` = friend. ^U on default to put it back.
- [✓] Persona: select reviewer, ^T → confirm → gone from list and disk.
- [✓] Persona: select cto, ^R → confirm → cto.md removed, shipped text back.
- [✓] **Hooks**: MUTE/OFF/BLOCK show 🔒 and a read-only explanation. Select STOP, change a word, ^S → `~/.abs/hooks.json` has it.
- [✓] Hooks: ^N → `review` → becomes `ABS REVIEW` → type wording → ^S.
- [✓] **Project**: hint carries the ⚠ committed-with-repo warning; edit, ^S → `./CLAUDE.md` written. (Then `git checkout -- CLAUDE.md` or delete it if you did not want one.)
- [✓] **Global**: locked; ^S → "Are you sure…" → n keeps it locked; ^S → y unlocks; ^S saves. Status line says LOCKED / UNLOCKED.
- [✓] **Memory**: hint names the project; list shows MEMORY.md first. ^N `test-fact` → file + index line; ^T on it → both removed.
- [✓] Unsaved edit + ^Q → dialog with s / q / n. Try n (stays), then q (quits, nothing written).
- [✓] `bash abs.sh prompt | cat` prints the table, does not hang.

## 2. Personas from the command line

- [✓] `bash abs.sh persona` — table with default/ceo/cto/friend and the active mark.
- [✓] `bash abs.sh persona show cto | head` — starts with NAME / ROLE — CTO.
- [✓] `bash abs.sh persona create mine` → file; `persona rename mine ours`; `persona delete ours`.
- [✓] `bash abs.sh prompt show built | grep -n "persona is"` → `This session's persona is 'default'`.

## 3. A real session, per persona (the part that matters)

For each of default, cto, friend: `bash abs.sh --persona <name>`, then from the phone:

- [ ] **default** — send "what's your name?" → answers ABS. Send a small task → one-line 🛠 ACK arrives first; on finish, three messages: voice note, transcript, card with the seven headings.
- [ ] **cto** — give it a small two-part task → it audits/plans, asks you to agree the plan, dispatches subagents, checks, reports once. Does the strictness feel right?
- [ ] **friend** — say hello → warm, short, no card. Then give it a task → card appears for the task only.
- [ ] Voice: a reply containing `CLAUDE.md` or `abs.sh` is spoken as "… dot md" / "… dot sh", with the words before the dot intact.
- [ ] A long reply (ask for a detailed explanation) → several voice notes if needed, **no** "as much as a note can carry", text arrives in full.

## 4. Hooks from the phone (session live)

- [ ] `ABS STOP` mid-task → model halts and says so.
- [ ] `ABS REVIEW` (the one you created) → model receives your wording and acts on it.
- [ ] `ABS MUTE` → 🔇 ack; `ABS UNMUTE` → catch-up message.
- [ ] `ABS EXIT` while idle → session closes cleanly.

## 5. Shipping rule

- [ ] With a change ready, say "ship it" from the phone → model asks ONE question naming action + target, does not push until you say yes, does not ask twice.

## 6. One engine (fresh-machine behaviour)

- [ ] `mv ~/.abs/voice.declined /tmp 2>/dev/null; ABS_VOICE_ROOT=/tmp/empty bash abs.sh` → asked "Install it now? [Y/n]"; answer n → `~/.abs/voice.declined` exists; relaunch shows the one-line "Voice is off" hint and does not ask again. Delete the file afterwards.
- [ ] In a live session (mode both), ask the model to "run abs say hello" → the guard blocks it with the "second copy" message; one note per reply, never two.

## 7. Mac only (if you run there)

- [ ] `abs prompt` opens, ^Q quits, F-keys or ^PgDn work, colours render.
- [ ] `bash -n abs.sh` under /bin/bash (3.2) is clean; `abs persona` runs.

## What failed

| # | Step | What you saw |
|---|------|--------------|
|   |      |              |
