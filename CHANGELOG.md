# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
