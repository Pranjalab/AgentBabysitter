# Documentation audit — README, docs/, and the website, against 3.3.0

**Status: partly fixed on 18 Aug.** Everything under A (the false statements) and
B (the stale ones) is corrected in the README and on the website. What remains is
C — commands that shipped and are still undocumented in `docs/GUIDE.md` and on the
website — and D, the four decisions, which need the operator rather than an edit.

The original survey follows, kept intact so the reasoning is not lost.

---

Eight releases shipped on 18 Aug and none of the prose moved with them. This is
the list of what is now **wrong**, what is merely **stale**, and what is
**missing**, so the next session can fix it in one pass instead of discovering it
piecemeal.

Nothing here is fixed yet. That was the ask: find it, write it down, fix it next
round.

Ordered by how much damage it does, not by where it lives.

---

## A. Wrong — states something that is not true of 3.3.0

These are the ones that will actively mislead somebody installing today.

### A1. The website says the installer asks you to clone the repository

`agentbabysitter-web/index.html:659`

> Installs `abs` and asks one question — clone the repository? Say yes for the
> always-on daemon and sandboxes.

The clone prompt was removed in 3.0.2 and replaced in 3.2.0. The installer now
asks **nothing** and fetches the v3 source into `~/.abs/src` itself. This is the
first paragraph a new user reads under the install command, and it describes a
question they will never be asked.

**Replace with:** it installs `abs`, then fetches the daemon and sandbox layer
automatically (needs Python 3.11+; falls back to a complete v2 install with a
note if that is missing).

### A2. Voice replies are documented as off by default

- `README.md:154` — `abs config reply-voice on|off  # send replies as a voice note (default off)`
- `README.md:297` — same claim in the command table
- `docs/GUIDE.md:127` — same claim

Since 3.1.0 the default is **`both`** wherever `voice_can_speak` is true, and
`text` only where the machine cannot speak. This is the single most visible
behaviour change of the day and every document still states the opposite.

**Also needs saying:** `abs config reply text` is now *stored* rather than being a
deleted key, and `abs config reply auto` is the way back to the machine default.
Neither is documented anywhere.

### A3. The context percentage is documented as dim and deliberately uncoloured

`README.md:353`

> `ctx` — how much of *this conversation's* context window is left — dim and last,
> because a limit at 90% stops your work while a long conversation merely means a
> long conversation.

3.1.0 colour-grades it: green above 50, amber to 20, coral to 10, brick below.
The paragraph does not merely fail to mention the colour — it explains at length
why there deliberately isn't any.

### A4. The docs page describes the old speech engine as the engine

`agentbabysitter-web/docs.html:432`

> `speak.py` (local TTS), driven by `abs say` … Auto-picks CUDA when present, else
> CPU.

Since 3.2.3 the default engine is **Kokoro** (`speak_kokoro.py`), 82M parameters,
CPU-native. Chatterbox is opt-in via `abs voice setup --chatterbox` and exists for
voice cloning only. The file map at `docs.html:485` lists `speak.py` and omits
`speak_kokoro.py` entirely.

The same page's "Replace the voice engine" section (`docs.html:511-523`) documents
a two-script contract that is now three.

### A5. The usage footer is described as something the agent is asked to do

`agentbabysitter-web/docs.html:420`

> Tells the agent to append the cached glance to task-completion reports only.

Since 3.1.0 **abs appends it**, in `cmd_voice_then_text`, and the prompt tells the
model not to write its own. The distinction matters because "tells the agent to"
is exactly the mechanism that failed and got replaced.

---

## B. Stale — true once, misleading now

### B1. "not in 3.0.0" as a version marker

- `README.md:388` — restricted assistant "**experimental, not in 3.0.0**"
- `README.md:417` — "**Not part of 3.0.0.**"

Still experimental, still not shipped — but pinning it to 3.0.0 reads as though
the note was written for a release three versions ago, which it was. Say
"experimental, not enabled" without a version, so it stops aging.

### B2. The status-bar example shows v3.0.0

`README.md:338`. Cosmetic, but it is a screenshot-in-text of the exact thing this
release changed twice (the label default and the ctx colour), so it is worth
regenerating rather than just bumping the number.

Note the same example shows `Pran:@yourbot` — which is now the *default* since
3.1.0 seeds the label from the Claude account name, not something you have to set.
The three `abs config label` lines under it (`README.md:359-363`) present it as
opt-in.

### B3. `abs config voice standard|turbo` and `voice-sample` are listed as mainline

`README.md:294-295`, `README.md:304`

Both are **chatterbox-only** concepts. On a default 3.3.0 install neither does
anything, because chatterbox is not installed. They need to move under the
cloning section and say so.

---

## C. Missing — shipped today, documented nowhere

| Shipped | Where it should be documented |
| --- | --- |
| `abs voice samples` — six voices as voice notes, choose by ear | README voice section, docs.html command table |
| The one-time voice-preference offer | README voice section |
| `abs config voice-offer done\|reset` | README command table |
| `abs config footer on\|off` | README command table |
| `abs config reply auto` | README command table, GUIDE reply-mode section |
| `abs voice setup --chatterbox` | README (partly done), docs.html |
| `abs src install\|status\|path` | README has it; **the website has nothing at all** |
| `ABS_VOICE_TIMEOUT` / `ABS_VOICE_FIRST_TIMEOUT` / `ABS_VOICE_LOCK_WAIT` | GUIDE, as the knobs for a slow machine |
| The bash 3.2 test suite | CONTRIBUTING, as the rule that shell changes must survive `bash:3.2` |

The website is the weakest of the three. It has no mention of the daemon-without-
a-clone work at all, which is the change that makes a `curl` install a full
install — arguably the most sellable thing in the last eight releases.

---

## D. Worth deciding, not just fixing

1. **`docs/ANNOUNCE-3.0.1.md` is written, unsent, and now three versions stale.**
   It was held back while macOS launches were broken. They aren't any more. Either
   rewrite it for 3.3.0 or delete it — leaving a stale announcement in the repo
   invites sending it by accident.

2. **`docs/` has accumulated one-shot files**: `MAC-BUG-PROMPT.md`,
   `RELEASE-TEST-3.0.0.md`, `release-gate-3.0.0.html`, `VOICE_MAC_TESTING.md`,
   `VOICE_PIPELINE_ANALYSIS.md`. All are records of finished work. Keeping them is
   fine; leaving them at the top level next to the live docs means the next person
   cannot tell which is current. An `docs/archive/` would fix it in one move.

3. **The README is 523 lines.** It is good, and it is also the only document most
   people will read. The voice section alone now has to cover two engines, a
   picker, a default, cloning, and three timeout knobs. That may be the moment to
   split a `docs/VOICE.md` out and leave the README with the short version.

4. **`docs/GUIDE.md` overlaps the README heavily** (612 lines, much of it the same
   ground). Worth deciding which one is canonical before editing both.

---

## Suggested order for the fix pass

1. **A1** — the website install note. One sentence, and it is the one that lies to
   every new user.
2. **A2** — the reply-voice default, in all three places. It is the behaviour
   people will hit first.
3. **A3, A4, A5** — the remaining false statements.
4. **C** — the missing commands, README first, website second.
5. **B** — the cosmetic staleness.
6. **D** — the decisions, which need the operator rather than a fix.

Items 1-3 are perhaps an hour. Items 4-6 are a session.
