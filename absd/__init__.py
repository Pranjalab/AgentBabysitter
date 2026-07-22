"""absd — the Agent Babysitter always-on daemon (ABS v3).

A single background daemon that manages every ABS profile/bot on the system.
When no Claude Code session is live for a bot, ``absd`` long-polls that bot's
Telegram token, enforces the profile allowlist, answers a small fixed command
grammar, and pools everything else. When a session is live, the in-session
telegram plugin owns the token and the daemon only watches liveness.

See ``PLAN.md`` (sections 3–5) for the locked decisions, target architecture,
and security model. This package is deliberately stdlib-first (asyncio,
dataclasses, json, pathlib; aiohttp for HTTP) so it stays cheap to port to
Rust or TS/Bun later — see PLAN.md section 4.4.

Nothing in this package implements daemon behavior yet: Step 0.1 lays down the
skeleton, protocol, and types only. The state machine, pollers, engines, and
Telegram client arrive in later steps.
"""

__all__ = ["__version__"]

# Mirrors ABS_VERSION in abs.sh / the repo-root VERSION file. absd ships as part
# of the same product; keep this in lockstep on release.
__version__ = "2.6.0"
