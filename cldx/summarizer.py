"""Summarize Claude Code activity for remote review (Telegram, in Phase 7).

Three modes with hard char budgets, defined per-agent:

- ``prompt_summary``     — "Claude wants X. Approve?" (≤200)
- ``escalation_summary`` — boiled-down pane context for remote review (≤500)
- ``completion_summary`` — what Claude did while you were away (≤500)

Uses the Anthropic SDK with prompt caching: the persona system message +
mode instruction prefix is cached so each summary is effectively a
single-token-prefix lookahead, which keeps latency and cost low.

When no API key is available, ``summarize()`` degrades gracefully to a
naive truncation prefixed with ``[unsummarized]`` so the rest of the
pipeline keeps working.
"""

from __future__ import annotations

import os
from typing import Literal

from cldx.agent import Agent


SummaryMode = Literal["prompt_summary", "escalation_summary", "completion_summary"]


MODE_INSTRUCTIONS: dict[str, str] = {
    "prompt_summary": (
        "Claude Code is asking the developer for approval. Summarize what "
        "Claude wants to do in ONE short sentence so the developer can decide "
        "via Telegram. Include the tool name and the key argument (file path "
        "or command). End with '— approve?'."
    ),
    "escalation_summary": (
        "Claude Code paused mid-task. Summarize the situation: what Claude "
        "was trying to do, what it just produced, and what specific decision "
        "the developer needs to make. Bullet points are fine."
    ),
    "completion_summary": (
        "Claude Code finished a task. Summarize what it actually did: files "
        "touched, commands run, key outcomes, and anything that needs the "
        "developer's follow-up. Be concrete."
    ),
}


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


async def summarize(
    mode: SummaryMode,
    context: str,
    agent: Agent | None = None,
) -> str:
    """Return a short human-readable summary of `context` per the `mode`.

    Always returns a string. If the API call fails or no key is set, returns
    a truncated copy of the raw context prefixed with ``[unsummarized]``.
    """
    agent = agent or Agent.load()
    limit = agent.limit_for(mode)
    if mode not in MODE_INSTRUCTIONS:
        raise ValueError(f"unknown summary mode: {mode}")

    api_key = os.environ.get(agent.api_key_env)
    if not api_key:
        return _fallback(context, limit, "no API key")

    if agent.model.startswith("ollama:"):
        # Ollama adapter would land here. For now, fall back.
        return _fallback(context, limit, "ollama backend not yet implemented")

    try:
        return await _summarize_with_anthropic(mode, context, agent, limit, api_key)
    except Exception as e:  # noqa: BLE001 — fail open, not closed
        return _fallback(context, limit, f"summarizer error: {e}")


async def _summarize_with_anthropic(
    mode: SummaryMode,
    context: str,
    agent: Agent,
    limit: int,
    api_key: str,
) -> str:
    """Real Anthropic-SDK call. Mocked out in tests."""
    from anthropic import AsyncAnthropic  # imported lazily so tests can run
                                          # without the SDK installed

    client = AsyncAnthropic(api_key=api_key)

    # Prompt cache the persona + instructions block — they're identical
    # for every summary, so this becomes a one-shot cache hit.
    system_blocks = [
        {
            "type": "text",
            "text": agent.persona,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": (
                f"Mode: {mode}. Char budget: {limit}. "
                f"Instruction: {MODE_INSTRUCTIONS[mode]} "
                "Never invent details that aren't in the source."
            ),
            "cache_control": {"type": "ephemeral"},
        },
    ]

    response = await client.messages.create(
        model=agent.model,
        max_tokens=min(1024, limit + 200),
        system=system_blocks,
        messages=[{"role": "user", "content": context}],
    )

    parts = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    summary = "".join(parts).strip()
    return _truncate(summary or _fallback(context, limit, "empty response"), limit)


def _fallback(context: str, limit: int, reason: str) -> str:
    return "[unsummarized: " + reason + "] " + _truncate(context, max(0, limit - 30))
