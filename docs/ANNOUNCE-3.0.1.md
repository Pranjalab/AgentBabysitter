# Announcing 3.0.1 — copy to send

Four pieces: the GitHub release body lives in `RELEASE-3.0.1.md`; this file is the
outward-facing copy for the waitlist and the site.

**Send these after `main` is pushed and the tag exists.** An announcement that
arrives before the install command works is worse than a late one — the first thing
a keen reader does is run it.

**A note on the claims.** Every line below is either true today or absent. No launch
copy says "secure", "bulletproof" or "production-ready" about a tool whose own
release notes list a blocklist as a known limit. If a claim here ever stops matching
the software, the claim is what changes.

---

## 1. WhatsApp / Telegram — short, for a broadcast list

> **Agent Babysitter 3.0.0 is out.** 🚀
>
> You can now start a Claude Code session **from your phone**. Send `ABS START`, pick
> a project, and it launches on your machine — no terminal, no laptop open.
>
> Also new: messages that arrive while nothing is running are kept and handed to the
> next session; your phone gets a ping when Claude stops to ask something; and if you
> want it spoken, the voice note arrives *before* the text.
>
> Install or update:
> `curl -fsSL https://agentbabysitter.com/install.sh | bash`
>
> Already have it? Just run `abs` — it offers you the update itself.
>
> Full notes: https://github.com/Pranjalab/AgentBabysitter/releases

Under 1000 characters, one link, one command. WhatsApp mangles tables and long code
blocks, so there are none.

---

## 2. Email — for the waitlist

**Subject:** Agent Babysitter 3.0.0 — start a session from your phone

> Hi,
>
> You signed up to hear when Agent Babysitter was ready for this. It is.
>
> **What changed.** Until now, ABS relayed a session you had already started at your
> desk; close the terminal and the bot went quiet. 3.0.0 adds a small daemon that
> holds the bot when no session does — so your phone works whether or not you are at
> the machine.
>
> - **Start work remotely.** Send `ABS START`, pick a project from the list it sends
>   back, and Claude Code launches on your machine. `ABS EXIT` ends it.
> - **Nothing gets lost.** Messages that arrive while nothing is running are kept and
>   offered to the next session, so a thought at 11pm is waiting for you at 9am.
> - **It tells you when it is stuck.** A session that stops for an approval used to
>   sit there silently. Now your phone gets a ping — once, not every thirty seconds.
> - **Voice, if you want it.** Ask for spoken replies and the voice note arrives
>   before the text, with the written version behind it. Speech runs entirely on your
>   own machine.
> - **Away mode**, for work you would leave alone: nothing stops to ask, and a command
>   guard refuses the irreversible things — `sudo`, `rm -rf`, force-pushes, machine-wide
>   installs, publishing.
>
> **Install or update — one command:**
>
>     curl -fsSL https://agentbabysitter.com/install.sh | bash
>
> It asks whether to clone the repository; say yes for the daemon and sandboxes. If
> you already run ABS, just start it — `abs` notices the new version and offers to
> update itself.
>
> **Being straight with you about two things.** The command guard is a blocklist, so
> it stops the irreversible things happening quietly while nobody is watching — it is
> not adversary-proof, and Away mode is for tasks you would be comfortable leaving
> alone. And the "restricted assistant" mentioned in earlier notes is *not* in this
> release: the code is there and tested, but nobody has run one end to end, so it
> ships switched off rather than announced.
>
> It is MIT-licensed, it is one bash script plus a small Python daemon, and it talks
> to nothing except Telegram's API and your own machine.
>
> — Pranjal
>
> Repository: https://github.com/Pranjalab/AgentBabysitter
> Release notes: https://github.com/Pranjalab/AgentBabysitter/releases
> Reply to this email if something does not work; it reaches a person.

---

## 3. The one-line version

For a tweet, a Show HN title, a site banner:

> Start a Claude Code session from your phone. Agent Babysitter 3.0.0 — one bash
> script, your own Telegram bot, nothing leaves your machine.

---

## 4. Site copy — the change worth making

`agentbabysitter.com` serves the installer, so the page and the file have to move
together. Two edits:

1. **The install block.** It already shows the one-liner; add a line under it saying
   the installer asks whether to clone, and that yes gets the daemon.
2. **A short "what's new in 3.0.0" section**, three bullets from the email above:
   remote start, nothing gets lost, blocked-session pings.

Do not put the version number anywhere except that section. A number hardcoded in a
hero is a number that goes stale on the next release and makes the whole page look
abandoned.

---

## Why the copy says 3.0.0 while the tag says v3.0.1

The tag is `v3.0.1` because 3.0.0 was finished, tested, and then improved for a day
before it was ever pushed — so the patch number carries a day of fixes that no user
ever saw broken.

Announcements should say **3.0.0**, because that is the release users are getting:
the daemon, remote start, the pool, the pings. Nobody outside this machine has a
3.0.0 to be upgraded from, and "3.0.1" in an announcement invites the reasonable
question of what 3.0.0 was and why they missed it. The release notes carry both
sections and the honest version history; the announcement carries the story.
