# Roadmap

Planned features, not yet built — captured so they aren't lost. No timeline;
we build them one at a time as they become worth it. Ideas and PRs welcome.

## 1. Conversation backup — a local, viewable, deletable archive

Keep a local copy of every conversation between the Telegram side and the
terminal session (the messages that flow through the plugin), written to memory
as **separate files segregated by date**.

It's a plain local cache of the whole conversation. The user owns it: view it
any time, keep and reuse it, or delete it when it's not needed.

- Storage: date-segregated files (e.g. `~/.abs/<profile>/log/YYYY-MM-DD.jsonl`),
  owner-only like the rest of the state.
- Commands to add: view (`abs log`), and clear (`abs log --clear`).
- Docs: add a section to the website — "Your conversations are backed up here.
  View them any time, and delete them whenever you want." Be explicit that it's
  local-only and never leaves the machine.
- Open question: cap size / retention, and whether to redact anything.

## 2. Per-project bot selection at launch

One system can run abs across several projects. With a single default bot, those
conversations would mix. Profiles already solve this (`abs --profile <name>`),
but the choice is invisible at launch.

On `abs` start (especially in a project with no profile yet), **ask**:

> Start this session with the default bot, or add a new bot for this project?

- Reuse → default profile. New → guide through creating a new profile/bot.
- Remember the choice per project directory so it isn't asked every time.
- Builds on the existing profiles system; the new part is the launch prompt and
  the per-directory memory of which bot a project uses.

## 3. Configurable persona + a developer extension framework

Two related pieces.

**Persona configuration.** Today the agent's personality lives hardcoded in
`build_prompt()`. Give it a real configuration surface — a file and/or an `abs`
command — so a user can shape tone and behavior without editing the script.

**Extension guide for developers.** Document how a developer builds their own
feature/skill for abs, adds it to the agent's skills, and saves it for future
use. Crucially, help them decide **where a new capability belongs**:

- a new **Python file** (standalone processing, like the voice pipeline)?
- a **shell script** that runs and returns output to the agent?
- a **system-prompt** modification (teach the agent a new behavior/rule)?
- a **CLAUDE.md** modification (project-specific instruction)?

This extends the "Build on abs" docs with a decision guide, and pairs it with the
persona-config mechanism so customization is first-class, not a fork.

## 4. Show the voice transcript for verification

When a voice note comes in and gets transcribed, **surface the transcript** — in
the terminal (so anyone watching sees exactly what was heard) and optionally
echoed back to Telegram — before acting on it. Transcription isn't perfect, so
the operator can catch a wrong word and correct or cancel instead of the agent
running off a mis-heard instruction.

- Print `🎙️ heard: <transcript>` to the terminal after `transcribe.py` returns.
- Optionally reply "Heard: … — on it" to Telegram (overlaps with #5).
- Correction path: messages are read between turns, so a wrong transcript can be
  followed by "no, I meant …". Consider whether a confirm-before-acting step is
  worth the friction.
- Open question: terminal-only vs also echo to Telegram; act-and-allow-correction
  vs confirm-first.

## 5. Instant "got it" acknowledgment on inbound

When a message arrives from Telegram and Claude starts working, the sender has no
signal it was received. **Acknowledge immediately** — a quick "got it, working on
it" (or a clarifying question if one's needed) — then continue the real work.

The persona already *asks* for this ("send a one-line 'on it' first"), so this is
mostly about making it **reliable** rather than net-new:

- Option A: harden the persona rule so the ack is near-guaranteed.
- Option B: a hook on the inbound message that auto-sends a lightweight signal —
  a 👀 reaction (via the plugin's `react` tool) or a one-line reply — independent
  of whether the agent remembers.
- Open question: reaction vs text (reaction is quieter); avoid double-messaging
  (ack then the real reply); hook-driven (reliable) vs prompt-driven (flexible).
