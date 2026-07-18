# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
