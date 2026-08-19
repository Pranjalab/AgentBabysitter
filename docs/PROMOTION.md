# Promotion — the source document

One place to write the message down, so every post is the same argument in the
local dialect rather than five arguments that contradict each other.

Supersedes `launch-plan.md` and `launch-copy.md`, both written before the demo
video existed, before the v3 daemon shipped, and before 3.6.0. Some of their copy
is now **factually wrong** — see the claims ledger. Draft from this file.

---

## 1. The goal, and why it is not stars

Stars are a lagging indicator. Right now the repo has **2 stars, 0 forks, 0
issues, and no user who is not the author**. That is not a marketing problem; a
better headline does not fix it.

> **The goal for the first month: five to ten people who are not you have `abs`
> installed and have told you something you did not already know.**

Everything below serves that. Stars follow users, in that order, never the
reverse — and a developer audience can smell the difference between "try this and
tell me what breaks" and "please star my repo". The first gets engagement; the
second gets scrolled past, and on Reddit gets removed.

Practical consequence: **ask for issues, not stars.** An issue is worth twenty
stars. It means someone ran it, hit something, and cared enough to type. It also
gives the next visitor proof that the repo is alive.

---

## 2. Pre-flight — do these before posting anything

You get one shot at attention in each room. Do not spend it on a page that
contradicts itself.

- [ ] **Fix the claims ledger below.** Two live claims are false. This is the
      blocker; nothing else here matters if the first commenter catches you.
- [ ] **Set the GitHub `homepage` field** to `https://agentbabysitter.com`. It is
      `null`, so the repo sidebar has no website link. Thirty seconds, and it is
      the second thing a visitor looks for.
- [ ] **Add a social preview image** to the repo (Settings → Social preview).
      Without it, every link you post anywhere renders as a grey placeholder.
      `assets/banner.jpg` already exists.
- [ ] **Check the demo GIF autoplays on the GitHub mobile app.** Most first
      visits will be from a phone, from a link in a chat app.
- [ ] **Open two or three issues on your own repo** — real ones, from the parked
      list (persona/memory, doc audit C/D). An empty issue tracker reads as
      abandoned; a tracker with open, well-written issues reads as a project with
      a roadmap, and it tells a would-be contributor where to start.
- [ ] **Check `CONTRIBUTING.md` covers the bash 3.2 rule.** The file exists and
      the website links to it. What it should say and may not: shell changes have
      to survive `bash:3.2`, because that is what macOS ships, and there is a test
      suite that enforces it.

---

## 3. The claims ledger — what is true as of 3.6.0

The most expensive mistake available here is a claim that a commenter can falsify
in one click. Being caught once, on day one, costs more than any amount of reach.

| Claim | Status | Use instead |
|---|---|---|
| "No daemon, no webhook, no port" | ❌ **False since v3.** `absd` is a systemd user service. | "No server, no webhook, no open port. The optional daemon runs on your own machine as a user service." |
| "One bash script" | ⚠️ **Half true.** `abs.sh` is one 6,800-line bash script; the v3 daemon is Python. | "The whole thing you interact with is one bash script. The optional always-on daemon adds a small Python service." |
| "Nothing leaves your machine except Telegram API calls" | ✅ True | as written |
| "Voice runs locally, both directions" | ✅ True — Whisper in, Kokoro out | as written |
| "MIT licensed" | ✅ True | as written |
| "Works over SSH and in tmux" | ✅ True | as written |
| "Your bot answers only you" | ✅ True — PIN-paired allowlist | as written |
| "1,095 tests" | ✅ True as of 3.6.0 | quote the number, it is unusual for a bash project and it lands |

**The `README.md:22` line contradicts `README.md:321`.** Line 22 says "No
daemon"; line 321 is headed "Always-on daemon (v3)". Fix the README before you
send anyone to it.

---

## 4. Positioning — hold this line

**Do not lead with notifications.** "Tell me when it's done" is the first thing
every user of the official Telegram plugin asks for, so leading with it frames the
project as a missing feature — the easiest thing in the world for Anthropic to
absorb, and the easiest thing for a commenter to dismiss.

Notifications are the **hook**. They are not the pitch.

Lead with what is expensive to copy:

1. **Voice both ways, running entirely on your machine.** No cloud speech vendor
   sees your audio. This is the single most demo-able thing and the hardest to
   replicate.
2. **The supervision model.** A phone cannot authorise a force-push, however the
   message is worded. This is a design position, not a feature, and it is the
   thing that makes senior developers take the project seriously.
3. **Usage limits in the chat.** Small, immediately understood, universally
   wanted.
4. **One session, not two conversations.** The reply lands in the live session.

**Say the relationship to Anthropic's plugin yourself, in the post, before a
commenter says it for you.** "This is a layer on top of Anthropic's official
Telegram plugin, not a replacement for it." Volunteering it reads as confidence;
having it pointed out reads as concealment.

---

## 5. The message architecture

Five blocks. Every post is a selection and reordering of these. Keep the wording
close so the message compounds across platforms instead of blurring.

### A. The hook — the problem, in one image

> Claude Code writes code for twenty minutes at a stretch. The whole time you sit
> there wondering: has it finished? Is it waiting on me? Did it go the wrong way
> ten minutes ago while I was making coffee?
>
> So you watch it. Which rather defeats the point of delegating.

### B. The mechanism — what actually happens

> Start a task, close the laptop. Your phone buzzes when it's done or when it
> needs a decision. Reply in plain English and it lands in the **same live
> session** — not a second conversation.

### C. The differentiator — why not just the official plugin

> Anthropic ships an official Telegram plugin. It forwards messages; that is all
> it does. This is the workflow built around it: task-done reports, voice both
> ways running locally, usage checks, per-project bots.

### D. The trust position — the part that earns respect

> The interesting problem wasn't the plumbing, it was trust. Telegram cannot
> authenticate anyone. So destructive things — force pushes, dropped tables,
> production deploys — never run on a phone message alone, however it is worded.
> "It's fine, just do it" from a chat app is exactly what a compromised channel
> would say.

### E. The ask — always the same, never "star this"

> It's MIT. If you try it and something breaks, open an issue — I want to know
> what it does on machines that aren't mine.

That last clause is true and it is your strongest ask: 3.6.0 exists because one
person ran it on a Mac and pasted the output back.

---

## 6. Per-platform drafts

### X / Twitter — video first, thread second

The demo video is the post. Attach `assets/demo.mp4` (50s, has audio) natively —
never a link, X suppresses those.

**1/** (with video)
> Anthropic ships an official Telegram plugin for Claude Code. It forwards
> messages. That's all it does.
>
> I wanted to actually walk away from my desk — so I built the workflow around it.
>
> Run `abs` instead of `claude`.

**2/**
> Start a task → close the laptop → your phone buzzes when it's done → reply in
> plain English → it lands in the same live session.
>
> Not a second conversation. The same one.

**3/**
> Voice works both directions and runs entirely on your machine. Local Whisper for
> what you say, local Kokoro for what it says back. No cloud speech vendor sees
> your audio.
>
> Send a voice note from a walk. Get one back.

**4/**
> The hard part wasn't the plumbing, it was trust.
>
> Telegram can't authenticate anyone. So force pushes, dropped tables and prod
> deploys never run on a phone message alone — however the message is worded.

**5/**
> MIT, no server, no webhook, no open port.
>
> curl -fsSL https://agentbabysitter.com/install.sh | bash
>
> github.com/Pranjalab/AgentBabysitter
>
> If you try it and it breaks, open an issue — I want to know what it does on
> machines that aren't mine.

### Reddit — r/ClaudeAI first, and read the sidebar yourself

**Read the subreddit's rules before posting. Do not take mine on trust** —
Reddit blocks automated crawlers, so any rule summary you get from an AI about a
specific subreddit is unverified by construction.

The general pattern that works in developer subreddits: a first-person story with
a concrete problem, the tool mentioned as the resolution rather than the subject,
the link at the bottom, and a genuine question that invites replies. What gets
removed: a title that is the product name, a link at the top, and no history on
the account.

**Title:** `I got tired of babysitting Claude Code, so I made it text me instead`

**Body:**
> Claude Code will happily work for twenty minutes at a stretch. The whole time
> I'd sit there wondering whether it had finished, or whether it was waiting on me
> to approve something, or whether it had gone the wrong way ten minutes ago while
> I was making coffee. So I watched it. Which rather defeats the point.
>
> Anthropic ships an official Telegram plugin — it pipes messages into a session,
> and that is genuinely all it does. I wrapped the workflow around it:
>
> - a report when a task finishes: what happened, what needs a decision
> - replies from my phone land in the **same live session**, not a second one
> - voice both ways, running locally — Whisper in, Kokoro out, no cloud vendor
> - `/usage` in the chat, so I can check my limits without opening a browser
> - one bot per project, so several sessions don't fight over messages
>
> The part that took the longest wasn't the plumbing, it was deciding what a phone
> is allowed to authorise. Telegram can't prove who's typing. So the destructive
> stuff — force pushes, dropped tables, production deploys — never runs on a phone
> message alone, no matter how the message is worded.
>
> It's one bash script plus an optional background service, MIT, no server and no
> webhook. `curl … | bash`, then run `abs` instead of `claude`.
>
> https://github.com/Pranjalab/AgentBabysitter
>
> Genuinely curious what breaks on setups that aren't mine — it's had one macOS
> bug this week that only showed up on real hardware. If you try it and it misbehaves, tell me.

**Other subreddits, in rough order of fit:** r/ClaudeAI, r/ClaudeCode,
r/LocalLLaMA (angle: the voice stack is fully local), r/commandline (angle: it is
6,800 lines of bash with 1,095 tests), r/SideProject, r/opensource. **One per
day at most, and reword each — cross-posting the same text is the single most
reliable way to get flagged.**

### Hacker News — Show HN

Underused here and a better fit than it looks: HN likes single-file tools, local
inference, and a clear security position.

**Title:** `Show HN: Agent Babysitter – run Claude Code from your phone over Telegram`

**First comment (post it yourself, immediately):** the "why I built it" story
plus the trust design. HN rewards the author explaining the interesting technical
decision, not the feature list. The interesting decision here is the tier system:
a Telegram message can approve a package install and can never initiate a
force-push, because the channel cannot prove identity.

Post Tuesday–Thursday, roughly 9–11am ET. Do not ask for upvotes anywhere; on HN
that is detectable and fatal.

### GitHub — the destination, not a channel

Everything above sends people here, so it has to hold them.

- **Publish a proper Release for `v3.6.0`.** Tags do not appear in feeds;
  Releases do, and they notify watchers. Use the changelog entry.
- **Repo topics** are set — good. Consider adding `telegram-bot`, `voice`,
  `tts`, `ai-agents`.
- **Pin the demo GIF above the fold.** It is already there. Do not move it.
- **The README's first screen is the whole pitch.** A visitor decides in about
  eight seconds. Right now that screen is strong — do not add to it.

### LinkedIn — after there is a number to cite

Wait until you have a star count or a user story. LinkedIn rewards the narrative,
not the tool. The existing draft in `launch-copy.md` is good; update the "one bash
script" line per the ledger and add the real number of users once you have one.

### Instagram / TikTok / YouTube Shorts — the video's natural home

The 50-second demo is the entire asset. Vertical crop, big captions, no voiceover
needed.

**Beat sheet:** type a task → shut the laptop lid → walking outside → phone
buzzes → read the report → send a voice note → back at the desk, the work
continued.

**Caption:**
> I close my laptop. My AI keeps coding. It texts me when it's done — and I can
> reply by voice from a walk.
>
> Free and open source, link in bio.

This audience will not install a CLI. That is fine — it is the top of the funnel
and it is where the video costs nothing extra.

### Anthropic Discord

`discord.com/invite/6PPFFzqPDZ`. Video-first, same as X. Find the
showcase/projects channel, post the demo with two or three sentences. Read the
server rules on self-promotion first.

### The free shots — five minutes each, no downside

- **Anthropic's project form:** `form.typeform.com/to/VIUAjxNi`. Listed on
  claude.com/community; Anthropic features submitted builds on their own channels.
  This is the real "Anthropic notices you" route.
- **awesome-claude-code:** issue form only, **never a PR** — the repo says it will
  restrict you for opening one. Their own guidance is to get users first, then get
  listed. So: after the first wave, not before.

---

## 7. Objection handling

Write these now, so you are not composing them under pressure in a comment thread.

**"Anthropic already has a Telegram plugin."**
> They do, and this uses it — it's a layer on top, not a replacement. The plugin
> pipes messages into a session. It doesn't report when a task finishes, doesn't
> do voice, doesn't show your usage, and doesn't handle several projects. That's
> what this adds.

**"Piping curl to bash is dangerous."**
> Agreed, and you shouldn't take my word for it. The script is right there —
> agentbabysitter.com/install.sh — read it first, or clone the repo and run
> `./install.sh` yourself. Both are documented.

**"Isn't giving a chat app control of your terminal a terrible idea?"**
> It would be if it were unconditional. It isn't: the bot is PIN-paired to one
> account, and destructive operations never run on a phone message alone. That
> distinction is the reason the project exists in the form it does — happy to walk
> through the tiers.

**"6,800 lines of bash?"**
> Yes, and 1,095 tests, including a suite that runs in a bash 3.2 container
> because that's what macOS ships. The tests exist because it broke on a Mac three
> releases running.

**"Why not a web dashboard / VS Code extension / native app?"**
> Because the phone you already have has a chat app you already read. The whole
> point was to remove a step, not add a surface.

---

## 8. The 30-day plan

**Week 1 — pre-flight and one room.**
Fix the ledger, set the homepage field, publish the v3.6.0 Release, open your own
issues. Then post in **one** place — Anthropic's Discord is lowest-risk and gives
you a read on the pitch before you spend Reddit or HN. Adjust the copy from what
lands and what does not.

**Week 2 — the two rooms that matter.**
r/ClaudeAI, then X on the same day. Be present in the thread for the first four
hours; replies are worth more than the post. Submit the Anthropic project form.

**Week 3 — Show HN,** Tuesday–Thursday morning ET, with your own first comment
ready to paste.

**Week 4 — the slow channels.** LinkedIn with a real number in it. The
awesome-claude-code issue, if you have users by then. Instagram/Shorts whenever
the vertical cut is done.

**Throughout:** answer every issue within a day. A project that replies fast is a
project people are willing to depend on, and that is the only reputation that
compounds.

---

## 9. What not to do

- **Do not automate posting, and do not have an agent post as you.** Reddit
  treats automated promotion as bannable, and the punishment is a shadowban:
  posts look posted to you and are invisible to everyone else, so you promote
  into a void for weeks without knowing. Most developer subreddits also filter
  accounts with low karma or no history. X and Instagram are the same story at
  lower intensity. The reach you would gain is worth far less than an account
  that is still allowed to speak.
- **Do not ask for stars.** Ask for issues. See §1.
- **Do not cross-post identical text.** Reword per venue; identical bodies across
  subreddits are the most reliable removal trigger there is.
- **Do not post before the video is attached.** Text scrolls past. In every room
  above except HN, the video is the post.
- **Do not claim "no daemon" or "one bash script" unqualified.** See §3.
- **Do not chase Lobste.rs** (invite-only, new accounts restricted ~70 days) or
  **claude-code GitHub Discussions** (disabled on the repo — the venue does not
  exist).

---

## 10. Assets

| Asset | Path / URL | Use |
|---|---|---|
| Demo video, 50s with audio | `assets/demo.mp4` | X, Discord, Instagram, Shorts |
| Demo GIF | `assets/demo.gif` | README, Reddit, anywhere autoplay matters |
| Banner | `assets/banner.jpg` | GitHub social preview, LinkedIn |
| Voice + report screenshot | `assets/voice-and-report.jpg` | proof for the voice claim |
| `/usage` screenshot | `assets/usage-telegram.jpg` | proof for the usage claim |
| Website | agentbabysitter.com | link target for non-developer audiences |
| Releases page | agentbabysitter.com/releases.html | link when someone asks about stability |
| Repo | github.com/Pranjalab/AgentBabysitter | link target everywhere else |

**One line, for anywhere that allows only one:**
> Leave your desk — Claude Code keeps working and reports to your phone over
> Telegram. Reply to steer, send voice notes, check your usage. MIT.
