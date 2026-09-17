# Manual test — 3.7.0, from the laptop

Written 17 Sep for the operator's own run. Every automated check has passed
(1,169 tests, bash 3.2 in the container, ^Q in a real pty); this is the part
only a person at a real terminal can do. Tick as you go; anything that fails
goes in the box at the bottom with what you saw.

Setup: `cd ~/Projects/research/AgentBabysitter && git checkout abs-prompt && git pull`.
Run everything as `bash abs.sh …` from that directory so you are on the branch,
not the installed copy.

## 1. The page — `bash abs.sh prompt`

- [ ] Opens on **Overview**. Token counts, paths, active persona, keys all read right.
- [ ] Colours: navy background, violet active tab and focus borders, round corners. Contrast OK on your terminal?
- [ ] **^Q quits** from the Overview. Reopen. **Esc quits.** Reopen.
- [ ] Tabs: F1–F7 switch; **^PgDn / ^PgUp** step and wrap; **clicking** a tab works.
- [ ] Footer shows only the actions (^S ^R ^N ^T ^U ^Q) — nothing cut off at your width.
- [ ] **System**: read-only; VOICE block and the `Reply mode is 'both'` block are present (not empty headings).
- [ ] **Persona**: list shows default · ceo · cto · friend, default marked active. Select cto — editor shows ROLE — CTO; nothing becomes "unsaved" from just looking.
- [ ] Persona: type one character in cto, ^S → toast "Saved cto"; `ls ~/.abs/personas/` shows cto.md.
- [ ] Persona: ^N → name `reviewer` → editor opens with the copy → ^S → file exists.
- [ ] Persona: select friend, ^U → "● active" moves to friend; `cat ~/.abs/persona.active` = friend. ^U on default to put it back.
- [ ] Persona: select reviewer, ^T → confirm → gone from list and disk.
- [ ] Persona: select cto, ^R → confirm → cto.md removed, shipped text back.
- [ ] **Hooks**: MUTE/OFF/BLOCK show 🔒 and a read-only explanation. Select STOP, change a word, ^S → `~/.abs/hooks.json` has it.
- [ ] Hooks: ^N → `review` → becomes `ABS REVIEW` → type wording → ^S.
- [ ] **Project**: hint carries the ⚠ committed-with-repo warning; edit, ^S → `./CLAUDE.md` written. (Then `git checkout -- CLAUDE.md` or delete it if you did not want one.)
- [ ] **Global**: locked; ^S → "Are you sure…" → n keeps it locked; ^S → y unlocks; ^S saves. Status line says LOCKED / UNLOCKED.
- [ ] **Memory**: hint names the project; list shows MEMORY.md first. ^N `test-fact` → file + index line; ^T on it → both removed.
- [ ] Unsaved edit + ^Q → dialog with s / q / n. Try n (stays), then q (quits, nothing written).
- [ ] `bash abs.sh prompt | cat` prints the table, does not hang.

## 2. Personas from the command line

- [ ] `bash abs.sh persona` — table with default/ceo/cto/friend and the active mark.
- [ ] `bash abs.sh persona show cto | head` — starts with NAME / ROLE — CTO.
- [ ] `bash abs.sh persona create mine` → file; `persona rename mine ours`; `persona delete ours`.
- [ ] `bash abs.sh prompt show built | grep -n "persona is"` → `This session's persona is 'default'`.

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

## 6. Mac only (if you run there)

- [ ] `abs prompt` opens, ^Q quits, F-keys or ^PgDn work, colours render.
- [ ] `bash -n abs.sh` under /bin/bash (3.2) is clean; `abs persona` runs.

## What failed

| # | Step | What you saw |
|---|------|--------------|
|   |      |              |
