# Persona and memory — the design, as decided

Roadmap item 3 ("configurable persona + developer extension framework"), parked on
18 Jul with the note *"deferred to a separate design discussion"*. This is the
outcome of that discussion, on 18 Aug. **Not built yet.**

The reasoning behind it is in the design note; this file is the settled shape, so
whoever builds it does not have to re-derive the decisions or re-run the argument.

---

## The two constraints that decide everything

**1. Memory must never write itself.** claude-mem was measured at 33% of the
operator's tokens — a hidden observer session, 107 in one day — and switched off.
That disqualifies the whole category. Every entry is written because a human
decided it was worth keeping. If the store can grow without anyone choosing to,
it is the wrong design.

**2. The persona is not a file the work can reach.** The security audit already
worked out the ordering: fixed bridge mechanics → persona slot → safety epilogue
appended *after*, so a persona saying "ignore previous instructions" is itself
followed by the non-negotiables. Safety stays compiled into `abs.sh` and is never
read from anywhere a session can write.

This is why the persona is **not** project-local, which was the operator's first
instinct and the one part of his proposal that was rejected. A directory inside a
working tree is the most writable place on the machine: clone a repo that ships a
`.abs/persona.md` and its author is now writing your agent's character. An
identity that changes when you `cd` is also not an identity.

---

## Where things live

```
~/.abs/persona.md                one identity, across every bot and every project
<project>/.abs/memory/*.md       facts about THIS project, one per file
<project>/.abs/memory/index.md   the cheap pointer layer a session loads
```

**Persona: one file, global.** Decided explicitly — not per profile, not per
project. One voice, whichever project it is pointed at.

**Memory: per project, never committed.** `.abs/` is gitignored by default. The
operator's call, and the right one: memory in a pull request is memory leaked to
everyone who clones, and memory that arrives with a clone is somebody else's
memory presented as yours.

**Not a second CLAUDE.md.** Claude Code already reads `CLAUDE.md` from the
project, and two files competing to tell the agent how to behave is worse than one
imperfect one. The split:

| | Holds | Shared |
| --- | --- | --- |
| `CLAUDE.md` | instructions — how to work in this repo | yes, committed |
| `.abs/memory/` | observations — what happened, what was decided | no, local |

Nothing goes in both.

---

## Secrets: refuse, do not redact

Asked to remember a password, `abs` **declines**. It does not store it redacted,
and it does not store it at all.

The operator's reasoning, which is better than the redaction plan it replaced: the
model already holds it for the length of the session, which is as long as it is
actually needed. Writing it to a file inside a working directory converts a
transient thing into a durable one that will eventually be committed by accident.
Reuse `_log_redact` to *detect*, then refuse — not to sanitise and keep.

---

## Build order

1. **Persona out of `build_prompt()` into `~/.abs/persona.md`.** The current 283
   lines ship as the default so upgrading changes nothing. `abs config persona
   edit | show | reset`. Length-capped, and a persona containing `<channel` is
   rejected outright.

2. **`ABS REMEMBER …` from Telegram.** One small file per fact, in the current
   project's `.abs/memory/`, plus an index entry. Creates `.abs/` and its
   `.gitignore` on first use.

3. **The index is loaded, the files are not.** Same discipline that makes this
   work in practice elsewhere: the session reads a one-line-per-memory index and
   opens a file only when it is relevant. A memory system that loads everything is
   just a longer prompt.

4. **An end-of-session offer — still undecided.** "Three things worth keeping from
   this?", writing only what is approved. The open question is whether it should
   ask or wait to be asked; an offer that appears every time is an irritation, and
   irritation is how a good feature gets switched off permanently. Lean toward
   `abs remember` invoked at the end of a session worth keeping.

5. **Skills: expose, do not rebuild.** `ABS SKILLS` to list what a project has and
   trigger one from the phone. A second skill system would compete with the one
   that keeps improving without us.

---

## Explicitly rejected

- **A vector store.** Solves a retrieval problem that does not exist at this
  scale, and makes the memory unreadable to the person it belongs to — which
  removes the only real check on whether it is any good.
- **Memory shared across profiles by default.** The work bot knowing things from
  the personal one is a surprise, and surprises in what an agent knows are
  expensive to debug.
- **Persona per project or per session.** See the constraint above.
- **Automatic distillation of `abs log`.** Something has to read the whole
  transcript to do it. That is precisely the cost already measured and rejected.
- **`plan.md` as its own file.** Plans go stale faster than anything else and a
  stale plan misleads more confidently than no plan. A plan is a memory with a
  date and a note on what would make it wrong.

---

## `ABS NEW` — clear the context without losing the thread

Agreed 18 Aug, and it is the reason the memory work comes first.

A true `/new` cannot be done from Telegram: clearing a conversation is not
something the model can do to itself, and there is no tool for it. What abs can do
is own the session — end the running one and start a fresh one in the same folder.
Same effect, different mechanism.

**This already works today, in two messages:** `ABS EXIT` then `ABS START`. The
weak half is `ABS EXIT`, which works by injecting a directive telling the model to
run `abs exit` — the same "depends on the model remembering" pattern that reply
mode and the usage footer were both moved off. `ABS NEW` should be enforced, not
requested.

**The sequence, and the reason for the order:**

1. Distil what matters from the session that is ending, into `.abs/memory/`.
2. End the session.
3. Start a fresh one in the same project, seeded with a short handoff — what was
   being worked on, what was decided, what is still open.

Step 1 is why this is not just a restart button. A new-session command without the
memory layer is a delete button: it throws away everything decided in the
conversation it is ending. With it, the operator's framing, it becomes "remember
what mattered, then start fresh".

**Two hazards to design around**

- It kills whatever is in flight. Mid-task, it should refuse and say what is
  running rather than confirm-and-destroy. abs can already tell a busy session
  from a waiting one through herdr.
- If the operator is at the desk, the terminal session dies under him. That
  deserves a different confirmation from the remote case.

**Needs the daemon.** Nothing else can launch a session when none is running.

---

## The persona: opinion, and when to keep it to yourself

Asked for on 18 Aug — "I would like you to give me this type of insight always"
— and to be part of the persona rather than a habit of one session.

The instruction is right and it needs one guard, because the obvious reading of it
produces something worse than what it replaces. "Always suggest a better approach"
becomes an agent that editorialises on every trivial request, and an agent that
comments on everything is one whose comments stop being read. The version worth
having is narrower:

> **Say it when it changes the outcome. Stay quiet when it does not.**

What that means in practice, from the cases that actually landed well today:

- **Disagree before building, not after.** The project-local persona file was
  rejected on a security argument *before* it was written. Raising it afterwards
  would have been a code review of my own work.
- **Bring the constraint the operator already established.** The strongest
  arguments today were not mine — claude-mem measured at 33% of tokens, and the
  registry's terminal-only rule. Both were his own prior decisions, applied to a
  new question. Look for those first.
- **Name what the request costs.** "Kokoro only" was accepted, with the note that
  it would delete voice cloning. He kept cloning. The suggestion was worth making
  precisely because it changed what got built.
- **Recommend, do not enumerate.** A menu of options is a decision handed back.
- **Say which part is a guess.** The 300-word threshold and the 5-minute timeout
  were both numbers chosen by feel that turned out wrong in use. Both should have
  been flagged as guesses when they shipped.

And the counterweight, which belongs in the persona just as much: routine work
gets done, not discussed. The judgement is whether a different choice would change
the result — not whether an opinion exists.
