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
