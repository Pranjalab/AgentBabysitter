"""End-to-end smoke test for the configured LLM backend.

Exposed via ``cldx test llm``. Runs all three summary modes
(``prompt_summary`` / ``escalation_summary`` / ``completion_summary``)
against realistic Claude Code pane snapshots, times each call, and
reports any fallbacks (``[unsummarized: …]``) as failures.

Use this between ``cldx setup llm`` and ``cldx setup telegram`` to
verify the LLM half of the pipeline before adding Telegram on top.
"""

from __future__ import annotations

import asyncio
import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from cldx.agent import Agent
from cldx.summarizer import summarize


# Realistic Claude Code snapshots — what a real pane looks like in each mode.
SAMPLE_CONTEXTS: dict[str, str] = {
    "prompt_summary": (
        "⏺ Bash(npm install --save axios)\n"
        "  ⎿  Waiting…\n"
        "\n"
        "────────────────────────────────────────────────────────────────\n"
        " Bash command\n"
        "\n"
        "   npm install --save axios\n"
        "   Install HTTP client library for new API integration\n"
        "\n"
        " Do you want to proceed?\n"
        " ❯ 1. Yes\n"
        "   2. Yes, always for this project\n"
        "   3. No\n"
    ),
    "escalation_summary": (
        "⏺ Edit(src/api/auth.py)\n"
        "  ⎿  3 hunks ready\n"
        "\n"
        "────────────────────────────────────────────────────────────────\n"
        " Edit file: src/api/auth.py\n"
        "\n"
        "   - 12 lines removed (legacy session token validation)\n"
        "   + 18 lines added (JWT validation with refresh token flow)\n"
        "\n"
        " Pre-edit tests run: 23 passed, 2 FAILED\n"
        "   - test_session_token_expiry: expected 401, got 200\n"
        "   - test_refresh_token_flow: AttributeError no 'refresh_jti'\n"
        "\n"
        " Should I patch the failing tests and re-apply?\n"
    ),
    "completion_summary": (
        "Task complete. Changes made:\n"
        "  - Created  tests/test_api_auth.py  (23 test cases, all passing)\n"
        "  - Edited   src/api/auth.py          (session → JWT refactor)\n"
        "  - Edited   src/api/routes.py        (added /refresh endpoint)\n"
        "  - Ran      pytest tests/            → 23 passed in 1.2s\n"
        "  - Ran      ruff check .             → no errors\n"
        "  - Ran      mypy src/                → 0 errors in 47 files\n"
        "\n"
        "Files touched:\n"
        "  src/api/auth.py         +18 -12\n"
        "  src/api/routes.py       +7  -0\n"
        "  tests/test_api_auth.py  +145 -0\n"
    ),
}


def _print_header(console: Console, agent: Agent) -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("agent", agent.name)
    table.add_row("backend", f"[magenta]{agent.backend}[/magenta]")
    table.add_row("model", f"[cyan]{agent.bare_model_id}[/cyan]")
    if agent.backend == "bedrock":
        table.add_row("region", f"[cyan]{agent.aws_region}[/cyan]")
    console.print(Panel(
        table,
        title="[bold cyan]LLM smoke test[/bold cyan]",
        border_style="cyan",
    ))


async def run_llm_test(console: Console | None = None,
                        agent: Agent | None = None) -> int:
    """Run all three summary modes against the configured backend.

    Returns 0 on full success, 1 if any mode produced a fallback or raised.
    """
    console = console or Console()
    agent = agent or Agent.load()
    _print_header(console, agent)

    failures = 0
    for mode, context in SAMPLE_CONTEXTS.items():
        budget = agent.limit_for(mode)
        console.print(
            f"\n[bold]{mode}[/bold] "
            f"[dim](budget {budget} chars, source {len(context)} chars)[/dim]"
        )

        start = time.perf_counter()
        try:
            summary = await summarize(mode, context, agent)
        except Exception as e:  # noqa: BLE001 — show errors, don't crash
            elapsed = time.perf_counter() - start
            console.print(f"[red]✗ exception after {elapsed:.2f}s: {e}[/red]")
            failures += 1
            continue
        elapsed = time.perf_counter() - start

        if summary.startswith("[unsummarized"):
            console.print(f"[yellow]⚠ fallback after {elapsed:.2f}s[/yellow]")
            console.print(Panel(summary, border_style="yellow", expand=False))
            failures += 1
        else:
            console.print(
                f"[green]✓ summary in {elapsed:.2f}s "
                f"({len(summary)} chars):[/green]"
            )
            console.print(Panel(summary, border_style="green", expand=False))

    console.print()
    if failures:
        console.print(
            f"[red]{failures}/{len(SAMPLE_CONTEXTS)} modes failed.[/red]\n"
            "[dim]Inspect your config with `cldx config show`. "
            "If you see `[unsummarized: …]`, the message after the colon "
            "names the root cause (missing key, wrong region, missing SDK, "
            "etc.).[/dim]"
        )
        return 1
    console.print(
        f"[green]✓ All {len(SAMPLE_CONTEXTS)} modes worked end-to-end via "
        f"{agent.backend}. LLM half of the pipeline is ready.[/green]\n"
        "[dim]Next: run `cldx setup telegram` to wire up remote approvals.[/dim]"
    )
    return 0


def main() -> int:
    """Allow `python -m cldx.llm_test` as a one-off entry point."""
    from cldx.secrets import load_into_environ
    load_into_environ()
    return asyncio.run(run_llm_test())


if __name__ == "__main__":
    raise SystemExit(main())
