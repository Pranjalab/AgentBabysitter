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

As of Step 1.3 the daemon core is live: the Telegram client (:mod:`absd.telegram`),
per-profile pool (:mod:`absd.pool`), read-only profile discovery
(:mod:`absd.profiles`), the IDLE_POLLING poller (:mod:`absd.daemon`), and the
``python -m absd`` entry point (:mod:`absd.__main__`). The ABS START/handoff
flow, multi-profile stagger tests, and sandbox layers arrive in later steps.
"""

__all__ = ["__version__"]

# Mirrors ABS_VERSION in abs.sh / the repo-root VERSION file. absd ships as part
# of the same product; keep this in lockstep on release.
__version__ = "3.0.3"
