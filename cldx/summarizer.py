"""Summarize Claude Code activity for remote review (Telegram, in Phase 7).

Three modes with hard char budgets, defined per-agent:

- ``prompt_summary``     — "Claude wants X. Approve?" (≤200)
- ``escalation_summary`` — boiled-down pane context for remote review (≤500)
- ``completion_summary`` — what Claude did while you were away (≤500)

Backends are pluggable, selected by the prefix on ``agent.model``:

==================================  ==========================
``claude-haiku-4-5`` (etc.)         Anthropic API direct
``bedrock:<modelId>``               AWS Bedrock (via boto3)
``gemini:<modelId>``                Google Gemini (via google-genai)
``ollama:<model:tag>``              local Ollama (stub for now)
==================================  ==========================

When the chosen backend's SDK / key is missing, ``summarize()`` degrades
gracefully to a naive truncation prefixed with ``[unsummarized: <reason>]``
so the rest of the pipeline keeps working.
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal

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


def _fallback(context: str, limit: int, reason: str) -> str:
    return "[unsummarized: " + reason + "] " + _truncate(context, max(0, limit - 30))


def _system_prompt_for(mode: SummaryMode, agent: Agent, limit: int) -> str:
    """Combined system instruction used by every backend.

    Returned as plain text; backends that support prompt caching wrap it in
    a list of cache_control blocks themselves.
    """
    return (
        f"{agent.persona}\n\n"
        f"Mode: {mode}. Char budget: {limit}. "
        f"Instruction: {MODE_INSTRUCTIONS[mode]} "
        "Never invent details that aren't in the source."
    )


# --- Top-level dispatch ---------------------------------------------------


async def summarize(
    mode: SummaryMode,
    context: str,
    agent: Agent | None = None,
) -> str:
    """Return a short human-readable summary of `context` per the `mode`.

    Always returns a string. If the configured backend isn't available
    (missing key, missing SDK, or call fails), returns a fallback with
    a truncated copy of the raw context.
    """
    agent = agent or Agent.load()
    if mode not in MODE_INSTRUCTIONS:
        raise ValueError(f"unknown summary mode: {mode}")
    limit = agent.limit_for(mode)

    backend = agent.backend
    try:
        if backend == "anthropic":
            return await _summarize_with_anthropic(mode, context, agent, limit)
        if backend == "bedrock":
            return await _summarize_with_bedrock(mode, context, agent, limit)
        if backend == "gemini":
            return await _summarize_with_gemini(mode, context, agent, limit)
        if backend == "ollama":
            return _fallback(context, limit, "ollama backend not yet implemented")
    except Exception as e:  # noqa: BLE001 — fail open, not closed
        return _fallback(context, limit, f"summarizer error: {e}")
    return _fallback(context, limit, f"unknown backend: {backend}")


# --- Anthropic direct -----------------------------------------------------


async def _summarize_with_anthropic(
    mode: SummaryMode, context: str, agent: Agent, limit: int,
) -> str:
    api_key = os.environ.get(agent.api_key_env or "ANTHROPIC_API_KEY")
    if not api_key:
        return _fallback(context, limit, "no Anthropic API key (run `cldx setup anthropic`)")

    try:
        from anthropic import AsyncAnthropic  # type: ignore[import-not-found]
    except ImportError:
        return _fallback(context, limit, "anthropic SDK not installed (pip install anthropic)")

    client = AsyncAnthropic(api_key=api_key)
    # Prompt cache the persona + instructions block (one-shot cache hit).
    system_blocks = [
        {"type": "text", "text": agent.persona,
         "cache_control": {"type": "ephemeral"}},
        {"type": "text",
         "text": (f"Mode: {mode}. Char budget: {limit}. "
                  f"Instruction: {MODE_INSTRUCTIONS[mode]} "
                  "Never invent details that aren't in the source."),
         "cache_control": {"type": "ephemeral"}},
    ]
    response = await client.messages.create(
        model=agent.bare_model_id,
        max_tokens=min(1024, limit + 200),
        system=system_blocks,
        messages=[{"role": "user", "content": context}],
    )
    parts = [getattr(b, "text", "") for b in response.content]
    summary = "".join(parts).strip()
    return _truncate(summary or _fallback(context, limit, "empty response"), limit)


# --- AWS Bedrock ----------------------------------------------------------


async def _summarize_with_bedrock(
    mode: SummaryMode, context: str, agent: Agent, limit: int,
) -> str:
    """Run the same prompt through Bedrock's Anthropic-Claude models.

    Uses ``boto3.client("bedrock-runtime").invoke_model``. The Bedrock
    request body has ``anthropic_version`` and the same ``messages`` /
    ``system`` shape as the direct Anthropic API.

    Auth: boto3 picks up ``AWS_BEARER_TOKEN_BEDROCK`` automatically when
    set, or falls back to standard AWS credentials. Region is taken from
    ``agent.aws_region`` (defaults to ``us-east-1``).
    """
    has_bearer = bool(os.environ.get("AWS_BEARER_TOKEN_BEDROCK"))
    has_standard = bool(
        os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE")
    )
    if not (has_bearer or has_standard):
        return _fallback(
            context, limit,
            "no AWS credentials (run `cldx setup bedrock` or `aws configure`)",
        )

    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError:
        return _fallback(
            context, limit,
            "boto3 not installed (pip install 'cldx[bedrock]' or pip install boto3)",
        )

    import asyncio
    region = agent.aws_region or os.environ.get("AWS_REGION") or "us-east-1"
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": min(1024, limit + 200),
        "system": _system_prompt_for(mode, agent, limit),
        "messages": [{"role": "user", "content": context}],
    }

    def _call() -> dict[str, Any]:
        client = boto3.client("bedrock-runtime", region_name=region)
        resp = client.invoke_model(
            modelId=agent.bare_model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        # invoke_model returns a streaming body even in non-stream mode.
        payload = resp["body"].read()
        return json.loads(payload)

    data = await asyncio.to_thread(_call)
    # Bedrock returns the same shape as Anthropic: { content: [ {type: text, text: "..."} ] }
    parts: list[str] = []
    for block in data.get("content", []) or []:
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
    summary = "".join(parts).strip()
    return _truncate(summary or _fallback(context, limit, "empty Bedrock response"), limit)


# --- Google Gemini --------------------------------------------------------


async def _summarize_with_gemini(
    mode: SummaryMode, context: str, agent: Agent, limit: int,
) -> str:
    """Summarize via Google's Gemini API (e.g. ``gemini-2.0-flash``)."""
    api_key = (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
    )
    if not api_key:
        return _fallback(
            context, limit,
            "no Gemini API key (run `cldx setup gemini` or set GEMINI_API_KEY)",
        )

    try:
        from google import genai  # type: ignore[import-not-found]
        from google.genai import types  # type: ignore[import-not-found]
    except ImportError:
        return _fallback(
            context, limit,
            "google-genai not installed (pip install 'cldx[gemini]' or pip install google-genai)",
        )

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        system_instruction=_system_prompt_for(mode, agent, limit),
        max_output_tokens=min(1024, limit + 200),
        temperature=0.2,
    )
    response = await client.aio.models.generate_content(
        model=agent.bare_model_id,
        contents=context,
        config=config,
    )
    summary = (getattr(response, "text", "") or "").strip()
    return _truncate(summary or _fallback(context, limit, "empty Gemini response"), limit)
