# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
