# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased — deferred out of 3.0.0

- **The restricted assistant is not part of this release.** `abs restricted` is
  complete in code and covered by unit tests, but no human has ever provisioned one
  end to end: it needs a third @BotFather token, which has blocked the checklist
  since July. Shipping it as a feature on that basis would be claiming something
  nobody has seen work.
  It ships **dormant** rather than removed. Nothing runs unless you type
  `abs restricted create`, which now warns that it is experimental before it does
  anything, and the help entry says so too. Removing it instead would mean unpicking
  ~220 references across `abs.sh`, `daemon.py`, `sandbox.py`, `flow.py`,
  `prompts.py`, `config.py` and `profiles.py` on the eve of a release, to delete code
  that is currently passing its tests — a far larger risk than leaving it in place
  and honest about its status.
  Development continues on the **`restricted-assistant`** branch; the manual
  checklist is ready at `docs/v3/manual-tests/restricted.md`.
  What *is* verified is the containment, which is the part that would matter if it
  broke: a `--no-creds` box holds no credentials, has no `~/.claude` at all, and
  cannot see the host home or projects. Checked on 17 Aug on a throwaway box, with a
  control check on a normal sandbox returning creds-present so the test can fail.

## [3.3.0] — 2026-08-18 — choose a voice by ear

- **`abs voice samples`** sends one short voice note per voice — six of them, each
  saying its own name so the note is self-labelling when the caption scrolls away.
  A list of identifiers is not a choice anybody can make: `af_bella` and
  `bm_george` mean nothing until you have heard them.

- **The choice is offered once, and then never again.** It appears in the injected
  prompt only where the machine can actually speak and nobody has chosen — raised
  in a sentence, early, with the samples synthesised only if the answer is yes.
  Picking a voice and keeping the default both end it for good;
  `abs config voice-offer reset` brings it back.

- The default is unchanged: `af_heart`, the warm American female voice. It is what
  the operator picked out of the six, which is a fair argument for it having been
  the right default all along.

## [3.2.4] — 2026-08-18 — a green status page on an install that could not speak

- **Kokoro's English phoneme model is installed at setup.** It was not, and
  kokoro tried to fetch it at the moment you first asked for a voice note — which
  it cannot, because `uv venv` builds an environment with no package installer in
  it. What surfaced was `error: No virtual environment found` at synthesis time,
  on an install whose `abs voice status` was entirely green.

- **Setup now synthesises a phrase and fails loudly if it cannot.** This is the
  fix that matters. Every voice bug in this project has been found by the operator
  on a machine reporting itself healthy, because the checks counted files instead
  of producing sound. It caught the bug above, and it caught a second one.

- **A warning when the voice path is long.** espeak-ng keeps its data path in a
  fixed 160-byte buffer and, past that, silently falls back to the path compiled
  into the wheel at build time — somebody else's CI checkout. The error names a
  directory on a machine you have never seen, so it reads as a corrupt install
  rather than as "your path is too long". It is not a bug at any normal path.

## [3.2.3] — 2026-08-18 — Kokoro is the voice engine everyone gets

`abs voice setup` built chatterbox and never built kokoro at all — which is how
the operator's MacBook Air came to be doing multi-minute CPU inference for every
voice note, and why a burst of three replies wedged it.

- **Setup builds Kokoro.** 82M parameters, designed for the CPU, a note in
  seconds on a laptop. Chatterbox is torch on a GPU, and on a machine without one
  it is not merely slow — it is slow enough for overlapping notes to pile up on
  each other, which was the whole 3.2.1 failure.

- **Chatterbox is kept, as `abs voice setup --chatterbox`.** It is not deleted,
  because it does one thing kokoro cannot: clone a voice from a sample. Making it
  opt-in makes it the exception it always was, instead of the thing everybody got
  by default. A configured voice sample still selects it automatically.

- **`voice_have` no longer demands `.venv-tts`.** This is the part that would have
  broken quietly: left alone, every install built by this release would report
  itself broken, and `build_prompt` would tell the model voice was unavailable on
  a machine that speaks perfectly well. It is "transcription in, and EITHER engine
  out" now. An existing chatterbox-only install still counts, so nobody's working
  setup is invalidated by upgrading.

- **`abs voice status` says which engine will actually run**, and shows chatterbox
  as optional rather than missing. A bare ✗ next to "TTS" on a good kokoro install
  reads as a broken install.

Verified by running the real `abs voice setup` into a scratch root: Whisper and
Kokoro built, no chatterbox, status correct, and `from kokoro import KPipeline`
succeeds in the venv it produced.

## [3.2.2] — 2026-08-18 — the v3 install gave up on the first Python it tried

Both of these came straight out of the operator's macOS install log.

- **`abs src install` tried exactly one interpreter and quit.** His Mac has
  Python 3.14; `venv` failed on it; the install gave up — with Python 3.12 and
  3.11 sitting on the same PATH. It now tries each candidate all the way through
  venv, pip and an `import absd`, and keeps the first that survives.

  The order changed too, and not to newest-first. A brand-new Python is the
  *worst* first choice here: the wheels it needs may not be published yet, so it
  fails at pip rather than at venv and looks like an unrelated bug. Well-supported
  middle first, then the floor, then the rest.

- **The failure hid the real error and named the wrong operating system.** Every
  step's output went to `/dev/null`, and what surfaced was "On Debian/Ubuntu:
  sudo apt install python3-venv" — printed on a Mac. An error message that names
  an OS you are not running is worse than no message, because it sends you
  looking somewhere that cannot be the answer. The last attempt's output is shown
  now, and the hint matches `uname`.

- **`abs voice status` shows the kokoro engine as its own row.** It was invisible,
  and its absence is a 10x difference in how long a note takes: chatterbox wants
  a GPU, kokoro is an 82M model that runs on CPU. The operator's MacBook Air had
  only chatterbox, so every note was a multi-minute CPU inference — which is what
  turned a burst of replies into the three wedged processes fixed in 3.2.1. The
  status page also says which engine will actually run, and how to add the fast
  one. You cannot fix what the status page does not show.

## [3.2.1] — 2026-08-18 — voice notes that wedged on macOS and never arrived

Reported from the Mac within minutes of 3.1.0 going out: text arrived, audio
never did, and three TTS processes sat on the box, one per reply, none of them
finishing. Two defects, and Linux only looked healthy because one hid the other.

- **The synthesis lock was `flock`, which is Linux-only.** The old code knew, and
  called the macOS case "a weaker ordering guarantee, not a failure". It was a
  failure: with no lock at all, every reply started its own synthesis, so three
  replies meant three copies of a multi-gigabyte speech model loading at once on
  an 8 GB machine. They thrashed and none returned. Serialisation is an atomic
  `mkdir` mutex now — one code path on every OS — with the holder's pid and the
  lock's age as two independent staleness signals, so neither a crashed holder
  nor a recycled pid can silence voice permanently.

- **Nothing was ever bounded, on any platform.** This is the worse half. `flock`
  kept Linux to one process at a time, so a hang there read as slowness rather
  than a wedge — but a single hung engine wedges Linux just as hard, and in
  voice-first mode it takes the TEXT with it, because the words are held until
  the note has gone. Synthesis runs under `with_timeout` now, which also learned
  to escalate TERM to KILL: the tree under a speech engine is `abs` → `abs say`
  → python → ffmpeg, and a timeout that only asks politely is not a timeout.
  Verified on bash 3.2 and 5.2 that a wedged engine is abandoned in seconds and
  the whole process tree is reaped, with no orphans.

  The consequence of that bound is the point: `_voice_fallback_text` — the "voice
  failed, send the words instead" path — existed all along and was **unreachable
  on the only platform that needed it**, because a run that never returns never
  sets a failure status.

Found in parallel on the Mac and on Linux, from opposite ends. The pid-based
reaping and the TERM→KILL escalation come from the Mac session's patch; the
mutex, the timeout and the tests are this side's.

## [3.2.0] — 2026-08-18 — the daemon, without a clone

The last of the four items queued behind the macOS crash, and the one that made a
`curl … | bash` install a second-class one.

- **`abs src install`.** The v3 layer — the daemon, sandboxes, the start menu's
  registry and recents — is the `absd` Python package plus a venv, and until now
  the only way to have them was a git checkout with `abs.sh` living inside it. So
  the installer asked "clone the repository?", which is exactly the interview an
  installer should not conduct. The prompt went in 3.0.2; this is what replaces
  it. The release tarball is unpacked into `~/.abs/src` and the venv is built
  there. No git, no question.

  The installer runs it automatically and does **not** fail if it can't: the step
  needs Python 3.11+, and losing a working v2 over an optional layer would be the
  wrong trade. It says what happened and moves on.

  Staged and swapped rather than written in place, so an interrupted download or
  a failed venv build leaves the working copy alone. Success is claimed only
  after `import absd` actually succeeds in the new venv — a venv that exists but
  cannot import is the failure this command exists to stop happening later, at
  launch, in a pane nobody is watching.

- **One place decides where the v3 source is.** `abs_src_root()`, shaped exactly
  like `voice_root()`: a checkout beside `abs.sh` always wins, so working on the
  repo can never pick up a stale copy from `~/.abs/src`; anything else looks
  under `$ABS_HOME/src`, which `abs uninstall` already wipes. Nineteen separate
  `dirname "$SCRIPT_PATH"` resolutions collapsed into it.

- **Every v3 command names the one command that fixes it.** They used to say
  "needs the full checkout" and point at a `git clone` the installer had
  deliberately stopped offering — a dead end. They say `abs src install` now, and
  `abs doctor` reports where the source is and whether it matches `abs`.

## [3.1.0] — 2026-08-18 — voice by default, and the numbers you were promised

Everything held back from 3.0.2 as `TODO(3.0.3)`, plus two things the operator
asked for while this was being built. Ships on top of the 3.0.3 crash fix below;
macOS launches cleanly for the first time in three releases.

- **Voice is on by default where the machine can speak.** `reply_mode` with
  nothing stored asks `voice_can_speak` and answers `both`. Never `voice`: an
  unattended default must not be the one mode that suppresses the written record.
  The consequence that made this more than one line — `text` used to be stored by
  DELETING the key, which with the new default would hand back `both` and make
  `abs config reply text` look ignored. Both `text` and `reply-voice off` now
  write the value explicitly, and `abs config reply auto` is the way back to the
  machine default.

- **The status bar says who you are.** The label defaults to the Claude account
  display name instead of the literal `abs` — resolved once at launch and stored,
  never at render time, because `~/.claude.json` is a large file that also holds
  account tokens and the bar redraws on every frame. `--clear` still means "back
  to abs", and stays that way.

- **Emoji never reach the speech engine.** They were read aloud as invented
  words. Stripped as raw UTF-8 byte ranges under `LC_ALL=C` — no perl, no python,
  nothing new inside the one path that must never fail. Verified identical under
  GNU sed and busybox sed, on bash 5 and bash 3.2.

- **The context percentage is coloured by how much is left**: green above 50,
  amber to 20, coral to 10, brick below. Deliberately a separate colour scale from
  the usage limits beside it — those grade percent USED and climb as they fill,
  this grades percent REMAINING and falls as it empties.

- **The usage footer is appended by abs, not remembered by the model.** The
  limits and the context percentage were reaching Telegram only when the model
  thought to paste them, which is to say rarely. In voice-first `both` — the
  default now — abs owns the send and appends the line itself. It is kept out of
  the voice note, dropped rather than pushing a message past Telegram's 4096-char
  ceiling, and switchable with `abs config footer off`.

## [3.0.3] — 2026-08-18 — the third bash 3.2 crash, and the gap that let all three through

- **`abs` died on every macOS launch with voice installed.** `build_prompt` exited
  127 at `voice_section="$(cat <<VOICEON` with `line 1248: text: command not found`.
  bash 3.2 — still `/bin/bash` on macOS — scans for the closing `)` of `$( … )`
  without knowing here-documents exist, so it lexes the prose body as shell. The
  apostrophes in "it's" and "don't" unbalanced the quoting, the substitution ended
  early, and the next line of prose ran as a command. The three prompt blocks now
  live in their own functions (`_prompt_reply_both`, `_prompt_voice_on`,
  `_prompt_voice_off`) and the substitution contains nothing but a function call.
  The prompt produced is byte-identical to what bash 5 produced before the change.

- **The suite now runs bash 3.2.** This is the actual fix. Three crashes in a week
  were invisible here and fatal on the operator's Mac, every one of them a bash 3.2
  parse difference, because the tests only ever ran bash 5. `tests/test_bash32.py`
  drives `build_prompt` under a real `bash:3.2` container across all five
  reply-mode/voice branch combinations, asserts it does not crash, and asserts the
  prompt matches bash 5's byte for byte. A static guard bans
  `$(cat <<TAG … TAG)` outright across every shipped shell script and runs with no
  Docker at all. Reverting the fix turns 7 of the 12 red.

## [3.0.2] — 2026-08-18 — two crashes that only appear where I do not run

- **The macOS upgrade crashed.** `info "… at $clone_dir…"` — an unbraced variable
  followed by a multibyte ellipsis, which bash 3.2 reads as part of the variable
  name until `set -u` aborts. Two more instances existed elsewhere. The clone prompt
  is gone entirely rather than repaired: an installer should install, not interview.

- **Every launch in reply mode `both` died**, shipped in 3.0.1. The prose in a
  double-quoted assignment was full of bare double quotes, each ending the string
  early. It is a quoted here-doc now. Unreachable from the tests, because the test
  profiles leave `reply_mode` unset and unset means `text`.

- **Voice no longer mangles version numbers.** The engine's normaliser read `3.0.1`
  as a sentence boundary and swallowed everything after it. Dotted versions become
  spoken words first.

## [3.0.1] — 2026-08-17 — the shape of the thing, corrected in use

3.0.0 was finished and unpushed when the operator started using it in earnest, and
everything below came out of that: a security pass over the branch, two bugs he hit
by simply launching sessions, and the reporting surface reworked around how he
actually reads it — by ear. Released together with 3.0.0, since 3.0.0 never shipped.

- **The status bar, as the operator wanted it read.** Four changes, all from seeing
  it in use:
  **The `● Daemon` dot is gone.** It answered a real question — is the bot being
  watched, so a message sent after this session ends still lands — but the verdict on
  it was "I'm not able to understand what it is", and a segment nobody can read costs
  width and teaches nothing on the most-seen surface in the tool. The state is still
  in `abs status` and `abs daemon status`, where there is room for a sentence.
  `ABS_DAEMON_FRESH_MIN` no longer does anything.
  **A reset time showed as a raw epoch.** `(resets 1786992000)`. Absorbing Claude
  Code's payload introduced it: `resets_at` arrives as a unix timestamp, and
  `date -d 1786992000` reads a bare integer as a *time of day*, so a formatter that
  had been correct for months against the `/usage` output's ISO strings silently
  produced nonsense. Bare integers are now treated as epochs.
  **Context moved last and went dim.** Lowercase `ctx 68%`, never colour-graded like
  the limits: a limit at 90% stops your work, a context window at 30% only means the
  conversation is long. It should read as a footnote.
  **The version is on the bar**, dim and last, so anyone debugging their own install
  can see which `abs` is rendering without running anything.

- **The status bar reads Claude Code's render payload, so it can show what is left
  of the context window.** Claude Code pipes JSON to the statusline command on every
  render, and it carries `context_window` (used and remaining percentage, window
  size) plus `rate_limits` (five-hour and seven-day utilisation with reset times).
  ABS had been paying a 90-second `claude -p "/usage"` subprocess to discover the
  second of those every few minutes, and had no way at all to get the first.
  The bar now absorbs the payload into the same usage cache everything else already
  reads, so `● Ctx 68% left` appears on the bar, the Telegram report footer carries
  it, and the expensive poll stops firing while a session is rendering. Context
  remaining is the number that decides whether a long task can finish in this
  session, which is why it was asked for.
  Guarded three ways, because a status bar that blocks freezes the terminal: skipped
  when stdin is a terminal, bounded by a one-second timeout, and every field dropped
  unless it parses as a plain number.

- **`curl … | bash` can install the whole thing now.** It installed one file, and one
  file cannot run v3: the daemon is Python in this repository with its own venv and
  sandboxes need the Dockerfile, both gated on being a checkout. The headline install
  could not reach the headline feature — it delivered a script that then had to
  explain what it was unable to do.
  It now offers to clone (default yes, `ABS_CLONE_DIR` to choose where) and continues
  down the checkout path that was already tested; declining installs exactly what it
  always did, which is the fallback for machines without git and for every
  non-interactive install. Fixing this exposed that `ask_yes` had no notion of a
  default, so a prompt written `[Y/n]` would have treated Enter as "no" and quietly
  handed people the cut-down install.
  The PyPI instructions are gone from the README. The package was two major versions
  behind, and `abs` already tells you when a new version exists and offers to update
  itself — one upgrade path, and it works for both kinds of install.

- **`abs send "text"` — an outbound path that does not depend on the plugin.** The
  operator finished a session waiting for a report that never arrived: the Telegram
  plugin's MCP server had dropped, the `reply` tool went with it, and the session
  wrote its report to a terminal nobody was watching. MCP servers dropping is not the
  bug; having no other way to send text was. `abs say` needs the TTS venvs and ffmpeg
  and delivers audio, and `abs usage --send` sends one fixed report.
  This is plain text to the paired chat over the same sender the daemon already uses,
  needing nothing but the token. `abs send -` reads stdin, because a report has
  newlines and quoting a wall of text through a shell is the friction that makes a
  fallback go unused. Past Telegram's 4096-character ceiling it trims and says so —
  sending nothing is the failure being fixed, so it never fails quietly.
  The injected prompt now tells a session to use it whenever the reply tool is
  missing or errors, and to say that the bridge dropped so the different-looking
  delivery is explained.

- **The agent reports like a colleague, with emoji that mean something.** A fixed,
  small vocabulary tied to state rather than decoration — 🔍 diagnosing, 🛠 building,
  🧪 testing, 🔊 audio, ✅ worked, ❌ failed, ⚠️ works-but-know-this, ⛔ refused,
  ⏸ waiting on you, 🚀 shipped, 📊 numbers, 🤔 a question for you, 🐛 a bug,
  🔒 security-relevant. One per line at most: on a phone this is the difference
  between reading a message and seeing it, and a message that is all emoji stops
  meaning anything.
  The tone section asks for warm, direct and good-humoured — and says outright what
  that never licenses: praising an idea before thinking about it, manufacturing
  enthusiasm for a plan the agent thinks is wrong, or softening a real problem so it
  goes down easier. Warmth and honesty are not in tension; flattery and honesty are.

- **Security: two ways the Away guard could be walked past, both fixed.** Found by
  a security pass over this branch before release, and both were introduced by this
  branch — which matters because Away now means `bypassPermissions`, so this guard is
  the only thing between an inbound message and the machine.
  **Leading whitespace defeated four rules.** They anchored on
  ``(^|[;&|`(][[:space:]]*)``, with the optional-space group inside the second
  alternative, so `^` demanded column 0. Two spaces was enough, and a tab, and an
  indented line: privilege escalation, machine state, service stops and
  process-substitution download-and-run all walked past. The `rm -rf` rule had it
  right, which is exactly why it went unnoticed — every test used column 0. Nor is
  indentation an adversarial trick: grep is line-oriented, so any multi-line script
  the model writes inside a conditional or a loop is indented by habit. The command
  is now normalised once before any rule runs, so every rule added later inherits the
  fix.
  **The guard trusted state the guarded session could rewrite.** `.session_away` and
  `.no_guard` were read live from `rc.json`, and the hook opened by returning 0 when
  that file was absent — so from inside an auto-approving session, editing the flag
  out and then turning the guard off disarmed it, and deleting the file alone failed
  it open for the rest of the session, taking the ABS MUTE/OFF/BLOCK ladder along
  with it. Worse, only `Bash` and the reply tool were matched, so one unprompted
  `Edit` to `abs.sh` — the script the hook re-invokes on every call — needed no shell
  at all. None of it was in the blocklist, and none of it should have to be: a
  control the controlled thing can edit is not a control.
  An Away launch now writes `--session-away` into the hook's own command line, so
  being unattended is a fact of the ARGV, where the session cannot reach it. That
  flag outranks `rc.json`, makes a missing state file fail **closed** (a normal
  session still fails open — a hook that errors on every call is worse than the gap,
  and there Claude is still asking), and adds `Write`/`Edit`/`MultiEdit`/
  `NotebookEdit` inspection for Away only, refusing writes to `abs.sh` and the
  profile directory while leaving ordinary project files alone. Shell writes to those
  same paths are refused too.
  The honest limit: this raises the bar rather than proving anything. An
  auto-approving agent with write access could still work toward subverting its own
  controls; what it cannot do any more is any of the one-step versions.
  Five mutations, each caught by the intended test. What the same pass checked and
  found already correct is recorded in `docs/RELEASE-3.0.1.md`.

## [3.0.0] — 2026-08-17 — v3: the always-on daemon

- **"Already being polled" now tells you what is holding the bot, and takes it
  back when nothing is.** One flat refusal covered four different situations, and
  gave advice ("quit that session first") that only fitted one of them.
  It now decides from the **process tree**, not from `session.pid` — a `claude`
  started by hand never writes one, which is precisely the case where the old
  message helped least. A live owner is named: pid, uptime, working directory, and
  `abs attach` when there is something to attach to. A poller that outlived its
  session is reclaimed rather than reported, because there is nothing to quit.
  And a pid file that describes a process which no longer exists is *ignored*:
  **pids are recycled** — this machine wrapped from ~3.8M to ~1.2M inside a day —
  so a live pid proves only that something owns that number now. Refusing over it
  was unclearable, and signalling it would have killed a stranger's process, so
  abs now checks the holder really is the plugin's poller before believing the file.
  `abs --reclaim` covers the rest: a wedged poller, or one misattributed to a live
  session. It ends the **poller**, never the session — the plugin reconnects.
  Also: a zombie poller counts as gone. `kill -0` succeeds on an unreaped process
  forever, so waiting for it to fail meant reporting "could not stop the poller"
  about a process that had stopped.

- **A slow network no longer stops `abs` from starting.** The launch-time version
  check exits 28 when `raw.githubusercontent.com` cannot be reached in three
  seconds. `pipefail` reported that status even though `tr` had succeeded, and
  `set -e` killed the command substitution *before* `_fetch_latest`'s own
  `return 0` — so the "always returns 0" contract its comment promised was never
  actually kept, and curl's exit propagated up three frames into the ERR trap and
  took `abs` with it. Four "Unexpected failure" lines and no session, because an
  optional version check could not reach GitHub. An offline laptop, hotel wifi, a
  corporate firewall or a GitHub blip was enough.
  Same shape as the corrupt-`rc.json` bug in `state_get`: a value read as `"$(…)"`
  where no caller checks the status. Fixed the same way, with the reasoning in a
  comment so the next person does not "simplify" it away.
  Found by the operator mid-release, on the first launch after the network went
  slow. There were no tests for the update check at all; there are eight now,
  including one that drives a real launch — the first version of that test used
  `--daemon-start`, which skips the update check entirely, and mutation testing is
  the only reason that was caught rather than shipped as coverage.

- **An Away session gets past Claude Code's bypass disclaimer instead of hanging
  on it.** `bypassPermissions` is not granted on trust: in a terminal Claude Code
  shows a "1. No, exit / 2. Yes, I accept" modal and waits, and non-interactively
  it downgrades the mode to the default. Away hit the first — a session stalled
  before it started, with the phone reporting only "waiting for input or
  approval", which is *worse* than the approval prompt this release set out to
  remove. An Away launch now writes `skipDangerousModePermissionPrompt` into the
  settings file it already passes with `--settings`, and says so in the launch
  warning. Scoped to that session: nothing is written to the operator's global
  config, so every ordinary `claude` still asks. Choosing Away is the acceptance,
  and it comes with the command guard, which the dialog does not.

- **A sandbox Away session was auto-approving with the guard optional.** The
  in-container launcher mapped `acceptEdits` to abs.sh's `--away` and forwarded
  anything else straight through — so once the daemon started sending
  `bypassPermissions`, an in-box Away session got the permission mode and none of
  the protection: no forced guard, no `.session_away`, no cleared disclaimer.
  `--away` is not a synonym for a permission mode, and both values now land on it.

- **In reply mode `both`, the voice note now arrives before the text.** The old
  order came from where the work happened: the reply tool sent the text and a
  `PostToolUse` hook spoke it afterwards. On a phone that is backwards — by the
  time the note plays you have read the message, so the audio is a duplicate of
  something you already know.
  It is one `PreToolUse` gate and one detached worker now: the gate blocks the
  reply tool's own send, the worker speaks and then sends the same words as text.
  Because that worker becomes the *only* thing that will deliver the message,
  every branch in it ends with the text going out — failed synthesis, a busy
  engine, a sentence already spoken five minutes ago. A voice note is a nicety;
  the message is not. Delivery gets one retry, and a total failure is written to
  the conversation log rather than vanishing.
  The gate declines, leaving the old order untouched, for anything it should not
  own: an attachment (the plugin does the upload), MarkdownV2 (the plugin does the
  escaping), and everything `voice` mode also declines — code, links, a wall of
  text, too little to be worth saying. Declining costs a message in the less
  useful order; wrongly accepting costs the message.
  It also does the auto-silent bookkeeping the blocked `PostToolUse` would have
  done, so a session replying perfectly well no longer drifts toward muting
  itself for being too quiet.
  **The note is the answer, not a preview.** What is spoken is the reply's first
  paragraph, and the prompt asks that paragraph to carry the whole thing — outcome,
  meaning, and the decision as a real question — so the listener never has to open the
  text. Nothing in it defers: "the rest is in the text" and "see below" are out, in
  both the places that used to say it. The second one was `_voice_mirror`'s own
  ceiling, which re-trimmed an already-trimmed note at 1200 characters; that ceiling
  is a parameter now, so `voice`-only mode keeps its tighter bound (there the note
  replaces the text) while voice-first passes its own.
  **Length is a mechanical rail, not a style.** 4000 characters, about 90 seconds,
  because synthesis costs roughly a second per twenty characters and the text waits
  behind the audio. It was 400, then 700 — both of them a guess about how much the
  operator wants to hear, which is not a thing to guess.
  **A long note announces itself.** Past ~400 characters a one-line "🔊 Recording a
  voice note (~Ns)…" goes first, quoting the opening words: otherwise a 40-second note
  is 40 seconds of silence, and silence is indistinguishable from a crash.
  **A long message is led, not skipped.** The first real report after this shipped
  was 1854 characters against a 1200-character ceiling, so the gate declined and the
  operator got text first with a truncated note behind it — the exact order the
  feature exists to fix, looking broken while behaving as written. A finished-task
  report is long *because* it is the thing worth hearing about, so length now means
  "speak the opening" (~400 characters, cut at a sentence end, ending "the rest is
  in the text") and the full text follows. Only code and links still stand aside
  entirely: those have to be read, not heard.
  The cost is stated rather than hidden: the words only exist once the reply is
  written, synthesis is ~5s for a sentence and ~9s for a lead on this machine, and
  the text waits for the note. `abs config voice-first off` restores the old order.

- **The daemon says when a session is stuck.** A remotely-started session that
  stops to ask a question is invisible from the phone: the daemon has handed the
  bot to the session, the session is waiting for a human, and nothing says so.
  A block sustained past `blocked_debounce_s` (20s) now pings the chat that
  started it — once per block, not once per check. The debounce is the whole
  design: a block answered at the desk in five seconds never needed a phone ping,
  and herdr takes a beat to recognise an approval prompt as one.
  **herdr only.** tmux cannot report what the program in a pane is doing, so
  there the feature is absent rather than half-working — and `agent_status` is
  deliberately NOT on the `Engine` protocol, so tmux stays a complete backend
  instead of a non-conforming one.
  Where PLAN.md sketched an `events.subscribe` socket client, what shipped
  samples `pane list` on the existing 3s watch tick. Against a ≥20s debounce,
  push buys nothing but a reconnect path and a second long-lived task — and a
  window where a dropped connection silently stops the pings.
  `unknown` is treated as *no information*: it neither starts a block nor ends
  one. Reading it as "not blocked" would let one bad sample cancel a real,
  still-pending block, which is exactly when this feature needs to work.

- **Pooled messages are picked by tapping, not typing.** Each waiting message
  gets a ☐/☑ toggle and the action button becomes "📤 Send 2"; with nothing
  ticked it still reads "📤 Send all" and still means all, so the common case
  stays one tap rather than N+1. Ticks repaint the same screen in place instead
  of stacking one per tap, and past eight messages the toggles step aside for the
  typed protocol (`send 1,3`) rather than making the keyboard a wall.

- **A third dot on the status bar: `● Daemon`.** Green while `absd` has refreshed
  this profile's status file recently — i.e. the bot is being watched, so a
  message sent after this session ends will still land. It appears only where a
  daemon directory exists, so a v2 install sees the bar it always saw.

- **The Text and Voice dots now answer the same question, and obey their own
  switches.** Both mean: if a reply happened right now, would it go out this way?
  Two bugs are fixed. `● Voice` reported *activity* — green only within
  `ABS_VOICE_ACTIVE_SECS` (120s) of a real send — which was the honest signal
  when voice was on-demand through `abs say`, but with reply switches it fires on
  every reply, so the dot went dim two minutes after a note that had arrived
  exactly as configured. And `● Text` never consulted `reply text off` at all,
  because the dots predate the switches. Voice still goes dim when the machine
  has no TTS: the switch is a wish, `voice_can_speak` is the fact.
  `ABS_VOICE_ACTIVE_SECS` no longer does anything; `.last_voice_ts` is still
  stamped as a record of when audio last worked end-to-end.

- **Away mode actually means away now.** It launched with `acceptEdits`, which
  auto-approves file edits and nothing else — so a session left running still
  stopped dead on the first Bash approval, which is the thing that actually
  halts work. Away didn't deliver the one thing its name promises. It now
  launches `bypassPermissions` on both the host and in-sandbox paths: nothing
  prompts.
  What replaces the prompts is the **command guard, made non-optional for Away**.
  `abs config guard off` can't disable it there, and it bites on *every* turn
  rather than only Telegram-driven ones — unattended is a property of the
  session, so attaching at the desk to type one command no longer disarms the
  remaining hours. (Verified against a real `claude -p` run that PreToolUse
  hooks still fire under `bypassPermissions`; the whole design rests on it.)
  The guard grew to match its new job: `sudo`/`doas`/`pkexec`, `shutdown`/
  `reboot`, `systemctl stop|disable|mask` and `service … stop`, `docker rm|rmi|
  prune|volume rm|compose down -v`, machine-wide package install/remove,
  publishing (`npm publish`, `docker push`, `gh release create`, `cargo
  publish`), writes to block devices or `/etc`, `crontab -r`, `kill -9 -1`.
  Project-local work is deliberately untouched — `npm install`, `pip install`,
  `cargo add`, `docker stop`, `compose down` without `-v`, `rm` of a single
  file, `DELETE … WHERE`. That half matters as much as the blocks: a guard that
  cries wolf gets switched off, and off is exactly what must not happen here.
  **A blocklist is never complete.** This keeps irreversible things from
  happening quietly while nobody watches; it is not adversary-proof.

- **The status-bar label is yours: `abs config label`.** `abs:@yourbot` becomes
  `Pran:@yourbot`. `auto` takes the display name off your Claude account —
  exactly that one field of `~/.claude.json`, resolved once and stored, so no
  render reads the file and the label can't change under you. The value is
  **sanitised, not validated**: it is reprinted into a terminal status bar
  surrounded by real ESC bytes on every render, where a control character would
  move the cursor rather than merely look wrong. Cleaning happens on the way out
  too, so a hand-edited `rc.json` can't inject either.

- **A corrupt `rc.json` no longer breaks the status bar.** `state_get` passed
  jq's exit 5 up through `x="$(state_get …)"`, tripping the ERR trap — two
  `Unexpected failure` lines on *every* Claude Code render, naming a file the
  operator can't see. An unreadable state file now reads as empty, which every
  caller already handles.

- **Fixed: a running session made every OTHER profile resolve to its bot.**
  `abs run` exports `TELEGRAM_STATE_DIR` so the plugin can find the token, and
  every `abs` command typed inside that session inherited it — but `use_profile`
  applied it to whatever profile it was *resolving*, not just the session's own.
  Visibly, `abs profiles` marked every profile `live (pid N)` with the running
  bot's pid. The half that actually mattered: `TG_DIR` is where the token and the
  allowlist are read from, so `abs --profile work …` inside a `default` session
  drove the wrong bot with the wrong allowlist. The export now carries
  `ABS_SESSION_PROFILE` naming who it belongs to; with that absent it is the
  user's own variable and the documented pre-profiles two-bot trick is unchanged.

- **Fixed: `abs restricted` printed two errors on a single-file install.**
  `_restricted_py` called `die` while being read as `py="$(_restricted_py)"`, and
  `exit` inside a command substitution ends only the subshell — so the parent
  carried on with an empty `$py` and the ERR trap added `Unexpected failure (exit
  1) at line N` underneath the real message. Two errors, the useless one last, on
  the first v3 command a `curl | bash` user tries. Now one message, naming the
  fix (`git clone …`).

- **Reply-channel changes say WHEN they land.** `abs config reply-text|reply-voice`
  answered "Takes effect next session" to everything, which is wrong in both
  directions: voice is decided at send time and lands on the very next message,
  while suppressing text needs the PreToolUse gate and so waits for a relaunch.
  A blanket message either sends you restarting for nothing or teaches you to
  skip the line — and then a live session that correctly still sends text reads
  as broken.

- **A handoff marker can no longer outlive its session.** Boot recovery caught
  that at startup; nothing caught it in a daemon that had been up for weeks after
  a machine slept, a session was hard-killed, or a reclaim was interrupted between
  killing the engine and clearing the marker — and the leftover collides with the
  next `ABS START`. A periodic sweep now reclaims one, conservatively: only when
  the marker is old, BOTH liveness signals are silent, and the poller is not in
  SESSION_LIVE (where `watch_once` already decides, with more context). An engine
  that cannot answer counts as alive — this is the one path that can end a session
  someone is using, and guessing there would destroy work. `stale_handoff_after_s`
  is validated to sit at or above the launch grace, so a session that is still
  booting can never be swept.

- **Lifecycle chaos test** (Step 2.3's critique gate): seeded random walks over
  message/start/hard-kill/daemon-restart/409/clock-jump, asserting after every
  single operation that no message consumed from Telegram exists nowhere in the
  pool, that the offset never goes backwards, and that a quiesced poller always
  converges to IDLE with no marker left behind. `ABS_CHAOS_ITERS=500` runs the
  plan's full length.
  Two of its invariants were vacuous when first written — one read the pool to
  decide what the pool should contain, the other pointed at `pool.json` when the
  file is `pool.jsonl` — and both survived deliberate mutants (a pool that drops
  one write in five, a sweep that never clears the marker). They are derived from
  the server side now, and every mutant is caught.

- **`abs config reply text|both|voice` — "always answer me in voice", enforced.**
  Asking the assistant to always reply with a voice note worked until the session
  got long and the instruction drifted out of the model's attention, which is the
  failure mode of every standing preference kept in a prompt. Reply mode is stored
  state and the session hooks act on it: `both` mirrors every outbound Telegram
  message as a voice note from PostToolUse, and `voice` intercepts the message at
  PreToolUse, speaks it, and blocks the text so the voice note *is* the reply. The
  model is told which mode is on — but only so it doesn't ALSO call `abs say` and
  send the same sentence twice. Enforcement never depends on it.
  Markdown is stripped before speaking (a URL read aloud is a minute of alphabet),
  the same sentence is never spoken twice within five minutes, and synthesis is
  serialised behind a lock and detached from the hook, which has a 5s budget
  against TTS's ~30s.
  `voice` still lets a message through as text when it carries a code block, a
  link, or an attachment — a voice note cannot carry any of them, and a blocked
  message is one the operator simply never receives. Same reason it refuses to
  engage at all on a machine that can't speak: the failure mode of this feature
  must be "text as usual", never silence.
  Three ways it could still have lost a message, all found in review and closed
  before release: the repeat-suppressor applied in `voice` mode too, so saying the
  same sentence twice inside five minutes blocked the text and skipped the audio;
  a failed synthesis recorded itself as "already said", so the retry vanished as
  well; and a synthesis that failed after the text was suppressed produced nothing
  at all — it now falls back to sending the words as text. Reply bodies also go to
  the speech engine on stdin rather than in argv, where `/proc/<pid>/cmdline`
  exposed them to every user on the box for the ~30s synthesis takes.

- **The terminal pickers are arrow-key menus now.** Every list `abs` offers —
  which bot, which session to resume, which project, which sandbox, where a new
  bot should run — is one `menu_select` with ↑/↓ (or k/j) moving a highlight,
  Enter taking it, and Esc/q backing out. Typing the number still works, so
  nothing anyone already does stops working, and the chosen row collapses to a
  single line so the scrollback keeps the decision without the whole menu.
  Long rows are truncated to the terminal width rather than wrapping — a wrapped
  row would desync the cursor arithmetic and smear the menu on every redraw.
  It degrades instead of breaking: no terminal on stderr, `TERM=dumb`, no
  `/dev/tty`, or `ABS_NO_TUI=1` all fall back to the old numbered prompt, which
  is what keeps these functions usable under `docker exec` without `-t`, over a
  pipe, and in CI.
  Row widths are counted in printed columns, not characters, because an emoji or a
  CJK glyph costs two — and every one of these menus carries an emoji. Labels come
  from folder names, so they are stripped of newlines and of every escape sequence
  except colour; a row that splits in two puts the highlight somewhere other than
  where it is drawn, which is worse than a smear. Verified against real bash 3.2
  (what stock macOS ships), where the escape-sequence timeout had to stop being
  fractional — 3.2 rejects that outright and the arrow keys did nothing.

- **Fixed: a reclaimed sandbox session left an orphan poller stealing messages.**
  Live-testing symptom: roughly half the operator's replies stopped arriving, with
  nothing erroring. `engine.kill()` closes the pane's process group on the *host*,
  and a sandbox session is a `docker exec` client — killing that client does **not**
  kill the claude it started inside the container, nor its Telegram plugin (verified
  against a real container, not assumed). That orphan kept polling the bot, and since
  Telegram gives each update to whichever consumer asks first, the next session saw
  only a random half of them. Teardown now reaps the in-container half too, via the
  in-box `session.pid`, and warns if a survivor is detected instead of passing in
  silence. The reap is TERM, then wait, then KILL: the first version asked whether
  anything had survived immediately after sending TERM, which both reported healthy
  shutdowns as survivors and let a claude that ignores TERM live on — the exact
  orphan this exists to prevent.
- **Fixed: the launch grace window predated ABS-in-the-box.** `session_start_grace_s`
  is 30s, set when an in-box session was bare `claude`. A v4 box must `docker cp` ABS
  in, run abs.sh, boot claude and start the plugin before its channel exists, so
  healthy launches were being declared dead — which is what triggered the orphan
  above. Sandbox launches now get their own `sandbox_start_grace_s` (120s); host
  sessions keep the tight 30s.

- **A sandbox that isn't logged in now says so.** Found in live testing: a sandbox
  session started, received the operator's Telegram message, and answered nothing —
  Claude *inside* the box was not authenticated. Nothing warned, because the
  pre-launch check tests the **host** credentials, and the only box-side check asks
  whether the credentials *file* exists. It does: the copy made at `create` is a
  frozen snapshot that expires while the host's keeps refreshing, so it stays present
  and well-formed while authentication fails. The daemon now asks `claude auth status`
  inside the box and refuses the handoff with the exact fix rather than launching a
  session that will read messages and reply to none. A probe that cannot run counts as
  *unknown* and fails open.
- **`abs sandbox login <name>`** — log Claude in inside a box, one time. Mirrors
  `abs restricted login`, which was previously the only one of the pair that existed.

The big v3 story: `abs` was a passenger — when Claude Code wasn't running, the bot
was deaf. v3 adds **`absd`**, a background systemd user daemon that polls every
idle bot so you can start, resume, and manage sessions entirely from Telegram, and
picks them up at the desk. Built in Python (asyncio, stdlib + aiohttp); the CLI
stays bash. All behind a small fixed grammar with the security model unchanged.

### Added
- **Always-on daemon (`absd`)** — one systemd user service manages every profile;
  polls idle bots, enforces the profile allowlist itself, and answers a small
  fixed command grammar. `abs daemon install|start|stop|status|logs`.
- **Remote session start — `ABS START`.** From Telegram: pick a project (registered
  projects + workspace-root children + "➕ New folder", jailed under one configured
  root) → pick Normal / Away → the daemon launches Claude Code in a persistent,
  attachable session and confirms with `abs attach <profile>`.
- **Session engines** — herdr (preferred) or tmux (reference), interchangeable
  behind one adapter; `abs sessions` / `abs attach [profile]` (searches both).
  Precise per-pane liveness so an attach can never be mistaken for the session.
- **Message pool** — messages that arrive while nothing runs are kept per profile
  (never dropped), acknowledged, and offered to **forward** as a starting
  session's opening prompt (`send all` / `send 1,3` / `skip`). `ABS POOL`,
  `ABS CLEAR POOL`.
- **Resume-first start**, both doors: Telegram `ABS START` offers up to 3 one-tap
  "▶ Resume" buttons; interactive `abs` at the terminal shows the same picker
  (`--resume` / `--new` to skip, `abs config start-menu off`).
- **Telegram "/" menu** — `/abs_start /abs_status /abs_pool` while idle, `/abs_exit`
  + `/usage` in-session (auto-switched by the daemon).
- **Kill ladder while idle** — `ABS OFF` / `ABS BLOCK` stop the daemon for that bot
  (recover only from the terminal), `ABS CLEAR POOL`.
- **Login detection** — a stat-only credentials presence check before launch
  (contents never read); a session that dies immediately reports a likely login
  issue.
- **Crash/restart recovery** — on boot the daemon re-derives full state from disk:
  a surviving daemon session resumes with precise pane/pid tracking, a dead one is
  reclaimed with a reboot notice; sessions that didn't survive a restart notify you
  and the pool is kept.
- **Observability** — a structured, metadata-only event log
  (`~/.abs/daemon/events.jsonl`, never message text) and a consolidated dashboard
  in `abs status` / `abs daemon status`; `abs doctor` diagnoses the whole stack.
- **Real log rotation** for `daemon.log` and `events.jsonl` (size-based, N
  generations); the installer refreshes the unit and can install a pinned herdr.
- **Sandbox sessions — `abs sandbox build|create|list|start|stop|destroy`, and
  🏖 Sandbox as an `ABS START` target.** Claude Code runs inside a long-lived Ubuntu
  container: non-root `dev`, no `--privileged`, no docker socket, and exactly one
  host mount — a dedicated `~/Projects/sandboxes/<name>` folder — so work syncs live
  and nothing else on the host is visible. Credentials are **copied** in (never
  mounted), sanitised on the way: `~/.claude.json` included, host hooks stripped,
  the plugin marketplace re-homed to `/home/dev`, and the box workspace pre-trusted
  (without those last two, a box starts with no Telegram channel or blocks forever
  on the trust prompt). If an in-box session's channel never comes up, the daemon
  reclaims the bot and says so instead of silently swallowing messages.
- **ABS itself runs inside the sandbox** (image `absd-sandbox:v4`). A box session is
  the *same* launcher the host runs — `abs.sh --profile <p> --daemon-start` — just
  inside the container, so the box gets the ABS status bar, the `PreToolUse` Bash
  guard, the `ABS STOP`/`EXIT`/`MUTE` remote controls, and a `session.pid` that
  `abs exit` can signal (**`ABS EXIT` from the phone now ends an in-box session**).
  `abs` is on `PATH` in the box; the orchestration verbs (`abs sandbox|daemon|
  restricted`) refuse in-box, since sandboxes are managed from outside. abs.sh and
  `absd/` are copied into `/opt/abs` at container start and before every session, so
  a host-side fix reaches a box without an image rebuild. Existing boxes must be
  re-created to pick up v4 (the host workdir is kept).

## [2.6.0] — 2026-07-22

### Added
- **`abs voice setup` — voice is now installable by everyone, not just a dev
  checkout.** Voice previously worked only where `abs` was a symlink into a repo
  clone with the venvs built by hand; a `curl | bash` install never shipped the
  scripts or built the engines, so `abs say` and voice transcription failed on
  every real install. `abs voice setup` fixes that end to end: it checks
  `ffmpeg`, installs [`uv`](https://docs.astral.sh/uv/) if missing, has `uv` fetch
  the Python versions Whisper (3.13) and Chatterbox (3.11) each need, downloads
  `transcribe.py`/`speak.py`, and builds both venvs. Idempotent; `--force`
  rebuilds.
- **`abs voice status`** — a green/red check of every voice piece (scripts, both
  venvs, `ffmpeg`, `uv`) so a broken install is legible at a glance instead of
  only surfacing as a mid-task failure.
- **The installer offers voice as an opt-in step.** After the base install it
  asks whether to set voice up now and hands off to `abs voice setup` (skipped
  cleanly on non-interactive installs).

### Changed
- **Voice engines for an installed `abs` now live in `~/.abs/voice`** rather than
  beside the command, keeping multi-GB venvs out of `~/.local/bin` and letting
  `abs uninstall` remove them with the rest of the state. A dev checkout is
  unchanged — its scripts and venvs stay next to `abs.sh`.
- **The Telegram system prompt's VOICE section is now conditional on voice
  actually being installed.** When the venvs are absent it tells the agent plainly
  that voice isn't set up and to point the user at `abs voice setup`, instead of
  asserting a working pipeline and sending the agent down dead paths.

## [2.5.1] — 2026-07-20

### Fixed
- **Status-bar Voice dot now reflects real activity, not just capability.** It was
  green whenever `.venv-tts` was installed, which said nothing about whether voice
  was actually flowing. Now it's green only when a voice note was genuinely sent
  within a recency window (`ABS_VOICE_ACTIVE_SECS`, default 120s) and dim
  otherwise — parallel to how the Text dot means "reports are flowing." `abs say`
  stamps `.last_voice_ts` on each successful send. Updates on Claude Code's next
  status-line render.

## [2.5.0] — 2026-07-19

### Added
- **On-launch update prompt.** Every launch now checks GitHub for a newer release
  and, on an interactive terminal, asks `Update now and relaunch? [y/N]` (default
  No). Yes updates abs in place and re-execs the new version into the same session
  (same profile and passthrough flags); No launches the current version. The check
  is synchronous but tightly timed out (≤3s) with an offline fallback, so it never
  hangs a launch; non-interactive sessions (systemd/nohup/CI) print a one-line
  banner instead of prompting. `abs config update-check off` suppresses it.
- **`abs update`.** Update abs in place to the latest release on demand. Detects
  how abs was installed — a git checkout fast-forwards (`git pull --ff-only`); a
  standalone copy re-runs the official installer over the same file — and verifies
  the on-disk version actually advanced before reporting success.

### Changed
- **Update check is now on-launch, not once-a-day.** The previous daily,
  background, one-launch-behind cache meant a fresh release could stay invisible
  for up to ~24h plus a launch. The cache is now an offline fallback only.

## [2.4.0] — 2026-07-19

### Added
- **Voice model selector.** `abs config voice standard|turbo` picks the default
  TTS model — standard keeps the emotion/pacing dials (`--exag`/`--cfg`); turbo
  (ChatterboxTurboTTS, bundled in chatterbox-tts) generates ~1.8× faster on GPU
  (measured) with no dials. Per-call override: `abs say --turbo` / `--standard`.
- **Voice cloning / selectable voice.** `abs config voice-sample <file>` clones a
  voice from any short reference clip (normalised to a wav in the profile dir) and
  applies it to every spoken reply, both models; `--clear` reverts to the built-in
  voice. Per call: `abs say --audio-prompt <wav>` / `--default-voice`.
- **`abs say` flag pass-through** — `--turbo`, `--standard`, `--device`,
  `--audio-prompt`, `--exag`, `--cfg`, etc. reach `speak.py` so you can A/B models
  and voices from the CLI and send either as a real voice note.

### Changed
- **Faster, more accurate transcription.** Greedy decode (`beam_size=1`),
  `condition_on_previous_text=False`, adaptive CPU threads, and a project-vocabulary
  `initial_prompt` — measured ~12% faster and 87%→100% word accuracy on a sample
  (project names like "Agent"/"git" stop getting mangled). Language auto-detects by
  default; `ABS_STT_LANG=en` pins it for a further speed win.
- **Cross-platform voice devices.** `speak.py` auto-selects `cuda` if present, else
  `cpu`; loudness-normalised, VoIP-tuned Opus output. Apple MPS is opt-in
  (`--device mps`), not the default: benchmarked on an M-series Mac, chatterbox TTS
  runs ~1.6-1.9× *slower* on MPS than CPU (small-batch autoregressive loop + MPS
  op-fallback copies), so auto stays on CPU there. STT (`small`) stays on CPU on Mac
  regardless (CTranslate2 has no Metal backend). Ships `voicelab.sh` to benchmark
  STT+TTS on any machine and `docs/VOICE_MAC_TESTING.md` for the Mac setup.

## [2.3.0] — 2026-07-18

### Added
- **Remote control ladder — hook-enforced kill switches.** Five phrases you send
  from Telegram as a whole message, acted on by the session hook *itself* — so
  they work even if the model is compromised (it never runs them):
  - `ABS MUTE` / `ABS UNMUTE` — mute / resume proactive reports (catch-up on resume).
  - `ABS OFF` — cut inbound *and* outbound Telegram; the session keeps working. Terminal-only to re-enable.
  - `ABS STOP` — halt the current plan at the next step and wait for a new instruction.
  - `ABS EXIT` — close the session (asks to confirm if mid-task); restart with `abs`.
  - `ABS BLOCK` — lock the bot out entirely until a deliberate `abs setup`.
- **Destructive-command guard.** A `PreToolUse` hook blocks a small, high-confidence
  set of destructive Bash commands (`rm -rf`, `git push --force`, `reset --hard`,
  `DROP`/`TRUNCATE`, `DELETE`-without-`WHERE`, reading `.env`/keys, …) when the turn
  was **driven from Telegram** — a remote message is lower-trust than the operator
  at the desk. From the terminal, nothing is blocked. Opt out: `abs config guard off`.
- `abs exit` ends the running session; `abs config guard on|off` toggles the guard.

### Security
- These turn the previously *advisory* prompt rules into *enforced* ones for the
  obvious high-damage cases, and add a kill switch that doesn't depend on trusting
  the model. Honest limit: defense-in-depth, not a sandbox (a determined
  compromised model could obfuscate a command) — Claude Code's own permission
  system stays the real boundary. Documented in README, SECURITY.md, and the site.

## [2.2.2] — 2026-07-18

### Fixed
- **Conversation-log secret scrubbing hardened** (from a security audit). Now also
  catches JWTs, PEM private keys, passwords embedded in URLs, lower/mixed-case
  `key=` and `password=` pairs, Slack/Google keys, and Telegram tokens with short
  bot IDs. Control characters are stripped before writing, so a logged escape
  sequence can't replay in the terminal when you view the log with `abs log`.
- **`abs say` works on macOS** — replaced the GNU-only `mktemp --suffix` with a
  portable temp file, so voice-out no longer dies on macOS.
- **Installer no longer over-claims ownership** — it only trusts a bare `abs.sh`
  filename for a *dangling* symlink (a deleted checkout); a live symlink to an
  unrelated `abs.sh` is left alone rather than silently overwritten.

### Security
- SECURITY.md and the website Security page now document the conversation log
  (what's scrubbed, that it's best-effort and local, how to disable/clear it),
  the trust-by-HTTPS install chain, and the daily update check.

## [2.2.1] — 2026-07-18

### Changed
- **Status-bar restyle.** The bottom-bar indicator now reads `abs:@bot · ● Text ·
  ● Voice · Fable 2% · Week 12% (resets on Thu) · 5H 22% (resets in 1h)`: the
  label is coloured (theme violet `abs:` + Telegram-blue `@bot`); two channel
  dots show whether **Text** (proactive reports) and **Voice** (local TTS) can
  reach Telegram right now; and each usage percentage is threshold-coloured —
  green under 60, amber to 80, coral to 90, muted brick above. Reset times moved
  into per-limit parentheses (weekly as a weekday, 5-hour as a countdown). All
  muted 256-colour tones. The Telegram usage footer stays plain text.

## [2.2.0] — 2026-07-18

### Added
- **Conversation backup.** abs keeps a local, date-segregated log of the session —
  your messages, its Telegram replies, and the tools it ran — under
  `~/.abs/<profile>/log/`, owner-only and never uploaded. Read it with `abs log`
  (`--list` for the days on record, `--date <day>` for one), delete it with
  `abs log --clear`. Anything shaped like a secret (bot tokens, `sk-…`, `ghp_…`,
  AWS keys, `KEY=…`) is scrubbed before writing — best-effort, since the log is
  local and owner-only anyway. Turn it off with `abs config log off`; when off,
  the per-tool hook cost isn't paid at all.

## [2.1.6] — 2026-07-18

### Added
- **Instant acknowledgment on inbound.** The moment a Telegram message lands, abs
  drops a 👀 reaction on it straight from the session hook — guaranteed and before
  any work starts, so you know it was received. Never double-messages the way a
  text ack would. Opt out with `abs config ack off`.
- **Voice transcript echo.** When you send a voice note, abs replies with
  `Heard: "…"` before acting on it, so you can verify the transcription and correct
  or stop it mid-task instead of finding out at the end.

## [2.1.5] — 2026-07-18

### Fixed
- **Re-running the installer now updates an existing install** instead of
  refusing it. It recognizes any prior Agent Babysitter — the git symlink *or* a
  curl/pipx static copy — by its version constant and replaces it in place, while
  still refusing to clobber the unrelated v1 Python namesake. This is how every
  user updates: just re-run the one-line installer (or `git pull`).
- **The update banner now shows on the first run after a release.** The version
  check fetches synchronously on a cold cache instead of only in the background,
  so a newer version is flagged immediately rather than on the second launch.

### Added
- **The installer offers to install Claude Code** if it's missing, the same way
  it offers Bun — `curl -fsSL https://claude.ai/install.sh | bash`, into
  `~/.local/bin`, no sudo.

## [2.1.4] — 2026-07-18

### Changed
- **Usage glance reordered and relabeled** — now reads `Fable 0 · Week 9% · 5H
  15% resets in 2m`: Fable first, then the weekly all-models limit, then the
  5-hour window with its next-reset time tucked onto the same segment.

## [2.1.3] — 2026-07-18

### Changed
- **Usage glance always shows the Fable weekly limit**, including at 0% — reverts
  the 2.1.2 hide-at-0 behavior. The `/usage` output omits the Fable line until
  the model is used this week, so whenever it's present we surface it.

## [2.1.2] — 2026-07-18

### Changed
- **Status bar shows the bot handle** — the indicator now reads `abs@yourbot`
  instead of `abs:default`. One bot per profile means the handle identifies the
  session just as uniquely, and it's what you actually recognize.
- **Usage glance drops `Fable 0%`** — a per-model weekly limit at 0% is noise in
  a bar that's fighting for width; it reappears once that model has real usage.

## [2.1.1] — 2026-07-18

### Fixed
- **Usage glance reset time** — the "resets in …" readout could show a nonsense
  window (e.g. `resets in 8755h 18m`) right after a 5-hour session rolled over.
  A cached reset stamp that had just passed was mistaken for a Dec→Jan year-wrap
  and pushed a full year out. It now only rolls the year forward for stamps more
  than 300 days past (the real wrap case) and shows `now` for a just-passed
  window until the next refresh.

## [2.1.0] — 2026-07-18

### Added
- **Launch defaults per profile** — `abs config model <name>` (`--clear` to unset)
  and `abs config silent on|off`, stored in `rc.json` and applied at launch. An
  explicit `abs --model …` on the command line still wins.
- **Smart auto-silent** — after 3 consecutive terminal prompts, proactive reports
  mute automatically (you're clearly at the desk). A Telegram message — or
  `abs quiet off` — resumes them. No idle timer, so reading at your desk never
  starts a buzz. Wired as a session hook via `--settings` (merges with your own).
- **Status-bar indicator** — a small dot in Claude Code's bottom bar shows the
  live state: green = reports flowing, gray = silent/auto-silent, hollow = inbound
  off. `abs config statusline off` opts out (e.g. if you run your own statusLine).
- **Usage glance** — your 5-hour, weekly, and per-model (Fable) limits, plus the
  next reset, show in the terminal status bar (`● abs:default · 5h 5% · week 7% ·
  Fable 0% · resets in 3h`) and as a footer on Telegram reports. The numbers are
  cached from `/usage` (token-free) and refreshed lazily; tune the interval with
  `abs config usage-refresh <minutes>` (default 5).
- **Startup flood control** — on start, `abs` drains any Telegram backlog older
  than the launch and asks at the terminal what to do (default: discard), so a
  new session no longer opens buried under old messages.
- **Version + update check** — `abs version` prints the installed version, and
  the installer reports what it installed. Once a day (backgrounded, no tokens)
  abs checks the `VERSION` file on `main`; if a newer release exists it shows a
  one-line banner at launch with the right update command for your install (`git
  pull` vs the curl one-liner). Opt out with `abs config update-check off`.

### Changed
- Inbound Telegram messages are now always replied to, even while reports are
  muted — a reply is never a "proactive send."

## [2.0.0] — 2026-07-16

The project was renamed from **Claude RC** to **Agent Babysitter**, and the
command from `crc` to `abs`. This release also supersedes an earlier, unrelated
tool that briefly held the `agent-babysitter` name (a tmux + local-LLM approach);
that version is preserved on the `v1` branch and the `v1.0.5` tag.

### Changed
- Command is now `abs`; state lives in `~/.abs`. Existing Claude RC profiles and
  pairings migrate automatically on first run (non-destructive).
- `usage` progress bars use `●`/`○` (the old `░` rendered as broken glyphs on
  phones); overridable via `ABS_BAR_FULL` / `ABS_BAR_EMPTY`.
- Documentation restructured: a shorter, feature-first README, with the full
  reference in [`docs/GUIDE.md`](docs/GUIDE.md) and the threat model in
  [`SECURITY.md`](SECURITY.md).

### Added
- First-run setup opens with a welcome banner and a guided BotFather walkthrough.
- Inbound screenshots/photos: attach an image in Telegram and Claude reads it.
- `install.sh` refuses to overwrite an unrelated `abs` on `PATH` (`ABS_FORCE=1`
  to override).
- PyPI packaging (`pip install agent-babysitter`) as a thin launcher for the
  bundled `abs.sh`.

### Fixed
- `usage` no longer prints a garbled reset line for a limit at 0% (e.g. an unused
  weekly model); it inherits the shared weekly reset window instead.

## [1.0.5] — 2026-05-30

Final release of the original tool (tmux monitor + local-LLM policy engine).
Preserved on the [`v1`](https://github.com/Pranjalab/AgentBabysitter/tree/v1)
branch; not compatible with 2.x.
