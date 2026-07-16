# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
