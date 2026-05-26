"""Interactive ``cldx setup`` wizards for Anthropic + Telegram credentials.

All wizards take an injectable ``input_fn`` so tests can drive them
without real stdin, and an injectable ``http_fn`` for the bits that
talk to ``api.telegram.org``.

Design notes:

- Anthropic key validation is opt-in (a tiny ``messages.create`` call).
- Telegram chat ID is **auto-discovered** by polling ``getUpdates`` after
  the user sends any message to the new bot. No copy-pasting numbers.
- All file writes go through ``cldx.secrets.save_secret``, which is
  atomic and chmods the file to ``0600``.
- The default ``input_fn`` is paste-tolerant: on a real terminal we use
  prompt_toolkit (bracketed-paste mode, no 1KB limit), only falling back
  to plain ``input()`` when prompt_toolkit isn't usable (non-TTY, piped
  stdin, missing dependency).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from cldx.secrets import (
    clear_secret,
    env_file_path,
    have_anthropic_key,
    have_telegram_config,
    mask_secret,
    save_secret,
)


HttpFn = Callable[[str, bytes | None, float], dict[str, Any]]


# --- paste-tolerant input -------------------------------------------------

def paste_friendly_input(prompt: str) -> str:
    """Read one line, handling pastes longer than 1KB on macOS terminals.

    Plain ``input()`` invokes the terminal driver in canonical mode, which
    truncates pasted lines at ``MAX_CANON`` (1024 bytes on macOS). Long
    Bedrock bearer tokens get cut off mid-paste.

    prompt_toolkit's ``prompt()`` switches the terminal to a mode that
    honors bracketed paste, so multi-kilobyte pastes arrive intact. We
    fall back to ``input()`` when prompt_toolkit isn't usable (no TTY,
    redirected stdin, missing dep) so this still works in pipes and tests.
    """
    try:
        import sys
        from prompt_toolkit import prompt as ptk_prompt  # type: ignore[import-not-found]

        if not sys.stdin.isatty() or not sys.stdout.isatty():
            return input(prompt)
        return ptk_prompt(prompt)
    except (ImportError, EOFError, KeyboardInterrupt):
        raise
    except Exception:  # noqa: BLE001 — any prompt_toolkit failure → fallback
        return input(prompt)


# --- helpers --------------------------------------------------------------


def _confirm(prompt: str, input_fn: Callable[[str], str], default: bool = True) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        raw = input_fn(f"{prompt} [{suffix}]: ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False


def _http_get(url: str, _data: bytes | None = None, timeout: float = 10.0) -> dict[str, Any]:
    """Default HTTP fetcher — used by both Anthropic and Telegram wizards."""
    req = urllib.request.Request(url, data=_data, method="POST" if _data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# --- Anthropic ------------------------------------------------------------


def run_anthropic_setup(
    console: Console | None = None,
    input_fn: Callable[[str], str] = paste_friendly_input,
    test_key_fn: Callable[[str], tuple[bool, str]] | None = None,
) -> bool:
    """Walk the user through saving an ``ANTHROPIC_API_KEY``.

    Returns True if a (possibly already-present) key is now configured,
    False if the user skipped or aborted.
    """
    console = console or Console()
    console.print(Panel(
        "[bold]Anthropic Claude API key[/bold]\n\n"
        "Used by cldx to summarize Claude Code activity before sending\n"
        "to Telegram. Costs about [cyan]$0.0001 per summary[/cyan] with Haiku.\n\n"
        "If you don't have a key:\n"
        "  1. Go to [cyan]https://console.anthropic.com/settings/keys[/cyan]\n"
        "  2. Create a new key\n"
        "  3. Copy it (starts with [cyan]sk-ant-[/cyan])",
        title="[bold cyan]Anthropic setup[/bold cyan]",
        border_style="cyan",
    ))

    current = os.environ.get("ANTHROPIC_API_KEY")
    if current:
        console.print(f"[yellow]Existing key: {mask_secret(current)}[/yellow]")
        if not _confirm("Replace it?", input_fn, default=False):
            console.print("[dim]Keeping existing key.[/dim]")
            return True

    while True:
        raw = input_fn("Paste your API key (empty to skip): ").strip()
        if not raw:
            console.print("[dim]Skipped Anthropic setup.[/dim]")
            return False
        if not raw.startswith("sk-ant-"):
            console.print("[yellow]Doesn't look like an Anthropic key (should start with 'sk-ant-'). Try again.[/yellow]")
            continue
        break

    path = save_secret("anthropic", "ANTHROPIC_API_KEY", raw)
    os.environ["ANTHROPIC_API_KEY"] = raw
    console.print(f"[green]✓ Saved to {path} (mode 600)[/green]")

    if _confirm("Test the key with a tiny API call?", input_fn, default=True):
        ok, msg = (test_key_fn or _default_anthropic_test)(raw)
        if ok:
            console.print(f"[green]✓ {msg}[/green]")
        else:
            console.print(f"[red]Test failed: {msg}[/red]")
            console.print("[yellow]Key was saved anyway — re-run `cldx setup anthropic` to replace it.[/yellow]")

    return True


def _default_anthropic_test(api_key: str) -> tuple[bool, str]:
    """Make a 10-token Anthropic call. Returns (ok, message)."""
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=10,
            messages=[{"role": "user", "content": "Reply with one word: ok"}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content).strip()
        return True, f"API responded: {text!r}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


# --- Bedrock --------------------------------------------------------------

DEFAULT_BEDROCK_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_BEDROCK_REGION = "us-east-1"


def run_bedrock_setup(
    console: Console | None = None,
    input_fn: Callable[[str], str] = paste_friendly_input,
    test_fn: Callable[[str, str, str], tuple[bool, str]] | None = None,
) -> bool:
    """Walk the user through saving an AWS Bedrock bearer token.

    Sets ``AWS_BEARER_TOKEN_BEDROCK`` and an ``AWS_REGION`` for the
    Bedrock client. Optionally tests with a tiny ``invoke_model`` call.
    On success, asks the user whether to make Bedrock the active LLM
    backend (rewrites ``~/.cldx/config/agent_name.yml``).
    """
    console = console or Console()
    console.print(Panel(
        "[bold]AWS Bedrock setup[/bold]\n\n"
        "Lets cldx run Claude (or other models) through your AWS account\n"
        "instead of using a direct Anthropic API key.\n\n"
        "If you don't have a Bedrock API key:\n"
        "  1. Sign in to the [cyan]AWS Console[/cyan]\n"
        "  2. Go to [cyan]Amazon Bedrock → API keys[/cyan]\n"
        "  3. Generate a long-lived [cyan]API key (bearer token)[/cyan]\n"
        "  4. Make sure your account has access to Claude models in the\n"
        "     region you plan to use (Model access → Manage model access)",
        title="[bold cyan]Bedrock setup[/bold cyan]",
        border_style="cyan",
    ))

    current = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
    if current:
        console.print(f"[yellow]Existing bearer token: {mask_secret(current)}[/yellow]")
        if not _confirm("Replace it?", input_fn, default=False):
            console.print("[dim]Keeping existing token.[/dim]")
        else:
            current = None  # fall through to re-collect

    if not current:
        while True:
            token = input_fn("Paste your Bedrock bearer token (empty to skip): ").strip()
            if not token:
                console.print("[dim]Skipped Bedrock setup.[/dim]")
                return False
            if not token.startswith("bedrock-api-key-") and len(token) < 30:
                console.print(
                    "[yellow]That doesn't look like a Bedrock bearer token. Try again.[/yellow]"
                )
                continue
            break
    else:
        token = current

    default_region = (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or DEFAULT_BEDROCK_REGION
    )
    region_raw = input_fn(f"AWS region [{default_region}]: ").strip()
    region = region_raw or default_region

    model_raw = input_fn(
        f"Bedrock model ID [{DEFAULT_BEDROCK_MODEL}]: "
    ).strip()
    model_id = model_raw or DEFAULT_BEDROCK_MODEL

    save_secret("bedrock", "AWS_BEARER_TOKEN_BEDROCK", token)
    save_secret("bedrock", "AWS_REGION", region)
    os.environ["AWS_BEARER_TOKEN_BEDROCK"] = token
    os.environ["AWS_REGION"] = region
    console.print(f"[green]✓ Saved to {env_file_path('bedrock')}[/green]")

    if _confirm("Test with a tiny Bedrock invoke_model call?", input_fn, default=True):
        ok, msg = (test_fn or _default_bedrock_test)(token, region, model_id)
        if ok:
            console.print(f"[green]✓ {msg}[/green]")
        else:
            console.print(f"[red]Test failed: {msg}[/red]")
            console.print(
                "[yellow]Saved anyway — re-run `cldx setup bedrock` to fix.[/yellow]"
            )

    if _confirm("Make Bedrock the active LLM backend in agent_name.yml?",
                input_fn, default=True):
        _set_agent_model(
            f"bedrock:{model_id}", aws_region=region, console=console
        )
    return True


def _default_bedrock_test(token: str, region: str, model_id: str) -> tuple[bool, str]:
    """Minimal Bedrock call to verify creds + region + model access."""
    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError:
        return False, "boto3 not installed (pip install 'cldx[bedrock]')"
    try:
        # boto3 picks up AWS_BEARER_TOKEN_BEDROCK automatically.
        client = boto3.client("bedrock-runtime", region_name=region)
        resp = client.invoke_model(
            modelId=model_id,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 10,
                "messages": [{"role": "user", "content": "Reply with one word: ok"}],
            }),
            contentType="application/json",
            accept="application/json",
        )
        data = json.loads(resp["body"].read())
        text = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        ).strip()
        return True, f"Bedrock responded: {text!r}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


# --- Google Gemini --------------------------------------------------------

DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"


def run_gemini_setup(
    console: Console | None = None,
    input_fn: Callable[[str], str] = paste_friendly_input,
    test_fn: Callable[[str, str], tuple[bool, str]] | None = None,
) -> bool:
    """Walk the user through saving a Google Gemini API key."""
    console = console or Console()
    console.print(Panel(
        "[bold]Google Gemini setup[/bold]\n\n"
        "Use Google's Gemini models (e.g. [cyan]gemini-2.0-flash[/cyan])\n"
        "to summarize Claude Code activity for Telegram.\n\n"
        "Get a key:\n"
        "  1. Visit [cyan]https://aistudio.google.com/apikey[/cyan]\n"
        "  2. Create an API key (free tier is plenty for cldx summaries)\n"
        "  3. Copy it",
        title="[bold cyan]Gemini setup[/bold cyan]",
        border_style="cyan",
    ))

    current = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if current:
        console.print(f"[yellow]Existing key: {mask_secret(current)}[/yellow]")
        if not _confirm("Replace it?", input_fn, default=False):
            console.print("[dim]Keeping existing key.[/dim]")
            key = current
        else:
            current = None
    if not current:
        while True:
            raw = input_fn("Paste your Gemini API key (empty to skip): ").strip()
            if not raw:
                console.print("[dim]Skipped Gemini setup.[/dim]")
                return False
            if len(raw) < 20:
                console.print("[yellow]Doesn't look like a Gemini key (too short). Try again.[/yellow]")
                continue
            key = raw
            break

    model_raw = input_fn(f"Gemini model [{DEFAULT_GEMINI_MODEL}]: ").strip()
    model_id = model_raw or DEFAULT_GEMINI_MODEL

    save_secret("gemini", "GEMINI_API_KEY", key)
    os.environ["GEMINI_API_KEY"] = key
    console.print(f"[green]✓ Saved to {env_file_path('gemini')}[/green]")

    if _confirm("Test the key with a tiny generate_content call?", input_fn, default=True):
        ok, msg = (test_fn or _default_gemini_test)(key, model_id)
        if ok:
            console.print(f"[green]✓ {msg}[/green]")
        else:
            console.print(f"[red]Test failed: {msg}[/red]")
            console.print(
                "[yellow]Saved anyway — re-run `cldx setup gemini` to fix.[/yellow]"
            )

    if _confirm("Make Gemini the active LLM backend in agent_name.yml?",
                input_fn, default=True):
        _set_agent_model(f"gemini:{model_id}", console=console)
    return True


def _default_gemini_test(api_key: str, model_id: str) -> tuple[bool, str]:
    try:
        from google import genai  # type: ignore[import-not-found]
        from google.genai import types  # type: ignore[import-not-found]
    except ImportError:
        return False, "google-genai not installed (pip install 'cldx[gemini]')"
    try:
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=model_id,
            contents="Reply with one word: ok",
            config=types.GenerateContentConfig(max_output_tokens=10),
        )
        text = (getattr(resp, "text", "") or "").strip()
        return True, f"Gemini responded: {text!r}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


# --- LLM picker (which backend?) -----------------------------------------

def run_llm_setup(
    console: Console | None = None,
    input_fn: Callable[[str], str] = paste_friendly_input,
) -> bool:
    """Interactive 'which LLM do you want?' picker."""
    console = console or Console()
    console.print(Panel(
        "Pick an LLM backend for cldx's Telegram summaries:\n\n"
        "  [cyan]1.[/cyan] Anthropic (direct API key)         — easiest, best quality\n"
        "  [cyan]2.[/cyan] AWS Bedrock (bearer token + boto3) — use your AWS account\n"
        "  [cyan]3.[/cyan] Google Gemini (gemini-2.0-flash)   — free tier available\n"
        "  [cyan]4.[/cyan] Skip — Telegram messages will use naive truncation",
        title="[bold cyan]LLM backend[/bold cyan]",
        border_style="cyan",
    ))
    while True:
        choice = input_fn("Choice [1-4]: ").strip()
        if choice == "1":
            return run_anthropic_setup(console=console, input_fn=input_fn)
        if choice == "2":
            return run_bedrock_setup(console=console, input_fn=input_fn)
        if choice == "3":
            return run_gemini_setup(console=console, input_fn=input_fn)
        if choice == "4" or choice == "":
            console.print("[dim]No LLM backend configured. Telegram will use truncation.[/dim]")
            return False
        console.print("[yellow]Pick 1, 2, 3, or 4.[/yellow]")


# --- Agent model update ---------------------------------------------------


def _set_agent_model(
    model: str,
    aws_region: str | None = None,
    console: Console | None = None,
) -> None:
    """Rewrite ``~/.cldx/config/agent_name.yml`` with a new ``model:`` field.

    Preserves ``name``, ``persona``, ``api_key_env``, and ``limits`` if they
    were set; pulls any missing fields from the bundled default agent.
    Comments will be lost — the file is small and rewritten in canonical
    form by ``yaml.safe_dump``.
    """
    import yaml as _yaml
    from cldx.agent import Agent

    console = console or Console()
    agent = Agent.load()  # current state (file or bundled default)

    new_data: dict[str, Any] = {
        "name": agent.name,
        "persona": agent.persona,
        "model": model,
        "api_key_env": agent.api_key_env,
        "limits": agent.limits,
    }
    if aws_region or agent.aws_region != "us-east-1":
        new_data["aws_region"] = aws_region or agent.aws_region

    target = env_file_path("agent_name").parent / "agent_name.yml"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".yml.tmp")
    tmp.write_text(_yaml.safe_dump(new_data, sort_keys=False, default_flow_style=False))
    tmp.replace(target)
    console.print(f"[green]✓ Active LLM is now {model} ({target})[/green]")


# --- Telegram -------------------------------------------------------------


def run_telegram_setup(
    console: Console | None = None,
    input_fn: Callable[[str], str] = paste_friendly_input,
    http_fn: HttpFn = _http_get,
) -> bool:
    """Walk the user through bot creation + chat ID auto-discovery."""
    console = console or Console()
    console.print(Panel(
        "[bold]Telegram bot setup[/bold]\n\n"
        "Lets cldx ask for approvals when you're away from your laptop.\n\n"
        "[bold]Step 1 — create a bot:[/bold]\n"
        "  1. Open Telegram and message [cyan]@BotFather[/cyan]\n"
        "  2. Send [cyan]/newbot[/cyan]\n"
        "  3. Pick a name and a username (must end in 'bot')\n"
        "  4. Copy the token BotFather gives you ([cyan]12345:AAE...[/cyan])",
        title="[bold cyan]Telegram setup[/bold cyan]",
        border_style="cyan",
    ))

    current_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    current_chat = os.environ.get("TELEGRAM_CHAT_ID")
    if current_token and current_chat:
        console.print(
            f"[yellow]Existing config: bot={mask_secret(current_token)}, "
            f"chat={current_chat}[/yellow]"
        )
        if not _confirm("Replace it?", input_fn, default=False):
            console.print("[dim]Keeping existing config.[/dim]")
            return True

    while True:
        token = input_fn("Paste your bot token (empty to skip): ").strip()
        if not token:
            console.print("[dim]Skipped Telegram setup.[/dim]")
            return False
        if ":" not in token or len(token) < 20:
            console.print("[yellow]Doesn't look like a Telegram token. Try again.[/yellow]")
            continue
        break

    bot_info = _telegram_get_me(console, token, http_fn)
    if not bot_info:
        console.print("[red]Token didn't validate via the Telegram API. Aborting.[/red]")
        return False
    bot_username = bot_info.get("username", "your_bot")
    console.print(f"[green]✓ Authenticated as @{bot_username}[/green]")

    console.print(Panel(
        f"[bold]Step 2 — find your chat ID[/bold]\n\n"
        f"On Telegram, open [cyan]@{bot_username}[/cyan] and send it any message\n"
        f"(e.g. [cyan]/start[/cyan]). Then come back here and press Enter.",
        border_style="cyan",
    ))
    input_fn("Press Enter once you've messaged the bot... ")

    chat_id = _telegram_discover_chat_id(console, token, http_fn)
    if chat_id is None:
        console.print(
            "[yellow]No recent message found. You can enter the chat ID manually.[/yellow]\n"
            "[dim]Find it at https://api.telegram.org/bot<TOKEN>/getUpdates[/dim]"
        )
        while True:
            raw = input_fn("Chat ID (numeric, empty to abort): ").strip()
            if not raw:
                console.print("[red]Aborted — token saved but chat_id is missing.[/red]")
                save_secret("telegram", "TELEGRAM_BOT_TOKEN", token)
                os.environ["TELEGRAM_BOT_TOKEN"] = token
                return False
            try:
                int(raw)
                chat_id = raw
                break
            except ValueError:
                console.print("[yellow]Chat IDs are numbers. Try again.[/yellow]")
    else:
        console.print(f"[green]✓ Found chat ID {chat_id}[/green]")

    save_secret("telegram", "TELEGRAM_BOT_TOKEN", token)
    save_secret("telegram", "TELEGRAM_CHAT_ID", str(chat_id))
    os.environ["TELEGRAM_BOT_TOKEN"] = token
    os.environ["TELEGRAM_CHAT_ID"] = str(chat_id)
    console.print(f"[green]✓ Wrote {env_file_path('telegram')}[/green]")

    if _confirm("Send a test message to verify the bridge?", input_fn, default=True):
        ok = _telegram_send_test(console, token, str(chat_id), bot_username, http_fn)
        if not ok:
            console.print(
                "[yellow]Send failed — check the chat ID. Saved anyway.[/yellow]"
            )

    return True


def _telegram_get_me(console: Console, token: str, http_fn: HttpFn) -> dict[str, Any] | None:
    try:
        data = http_fn(f"https://api.telegram.org/bot{token}/getMe", None, 10.0)
    except urllib.error.HTTPError as e:
        console.print(f"[red]HTTP {e.code}: {e.reason}[/red]")
        return None
    except (urllib.error.URLError, OSError) as e:
        console.print(f"[red]Network error: {e}[/red]")
        return None
    if not data.get("ok"):
        console.print(f"[red]Telegram: {data.get('description', 'unknown error')}[/red]")
        return None
    return data.get("result", {})


def _telegram_discover_chat_id(
    console: Console, token: str, http_fn: HttpFn
) -> str | None:
    try:
        data = http_fn(f"https://api.telegram.org/bot{token}/getUpdates", None, 10.0)
    except (urllib.error.URLError, OSError) as e:
        console.print(f"[red]Network error: {e}[/red]")
        return None
    if not data.get("ok") or not data.get("result"):
        return None
    for update in reversed(data["result"]):
        msg = update.get("message") or update.get("edited_message")
        if msg:
            cid = msg.get("chat", {}).get("id")
            if cid is not None:
                return str(cid)
    return None


def _telegram_send_test(
    console: Console,
    token: str,
    chat_id: str,
    bot_username: str,
    http_fn: HttpFn,
) -> bool:
    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": "✓ cldx is connected. You'll get approval prompts here.",
    }).encode()
    try:
        data = http_fn(f"https://api.telegram.org/bot{token}/sendMessage", body, 10.0)
    except (urllib.error.URLError, OSError) as e:
        console.print(f"[red]Network error: {e}[/red]")
        return False
    if data.get("ok"):
        console.print(f"[green]✓ Sent test message — check @{bot_username} on Telegram.[/green]")
        return True
    console.print(f"[red]Send failed: {data.get('description', 'unknown')}[/red]")
    return False


# --- combined + show ------------------------------------------------------


def run_full_setup(
    console: Console | None = None,
    input_fn: Callable[[str], str] = paste_friendly_input,
) -> None:
    """Run LLM-picker + Telegram in sequence, then show the final config."""
    console = console or Console()
    console.print(Panel(
        "Let's set up the optional integrations cldx can use:\n\n"
        "  • [cyan]LLM backend[/cyan] — Anthropic / AWS Bedrock / Google Gemini\n"
        "                  (used to summarize Claude activity for Telegram)\n"
        "  • [cyan]Telegram bot[/cyan] — for remote approvals from your phone",
        title="[bold cyan]cldx setup[/bold cyan]",
        border_style="cyan",
    ))
    run_llm_setup(console=console, input_fn=input_fn)
    console.print()
    run_telegram_setup(console=console, input_fn=input_fn)
    console.print()
    show_config(console)


def show_config(console: Console | None = None) -> None:
    """Print a masked view of every secret cldx currently knows about."""
    from cldx.agent import Agent

    console = console or Console()

    # Top: which LLM backend is active.
    try:
        agent = Agent.load()
        backend_summary = (
            f"[bold]{agent.backend}[/bold] · model=[cyan]{agent.bare_model_id}[/cyan]"
        )
        if agent.backend == "bedrock":
            backend_summary += f" · region=[cyan]{agent.aws_region}[/cyan]"
        console.print(Panel(
            f"agent: {agent.name}\nLLM backend: {backend_summary}",
            title="[bold]agent[/bold]", border_style="cyan",
        ))
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]agent_name.yml unreadable: {e}[/red]")

    # Secrets table.
    t = Table(title="secrets", show_header=True, header_style="bold")
    t.add_column("setting", style="bold")
    t.add_column("value")
    t.add_column("source", style="dim")

    plain_value_vars = {"TELEGRAM_CHAT_ID", "AWS_REGION"}
    for var in (
        "ANTHROPIC_API_KEY",
        "AWS_BEARER_TOKEN_BEDROCK",
        "AWS_REGION",
        "GEMINI_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    ):
        v = os.environ.get(var)
        if var in plain_value_vars:
            display = v if v else mask_secret(None)
        else:
            display = mask_secret(v)
        t.add_row(var, display, _source_for(var))
    console.print(t)

    config_dir = env_file_path("anthropic").parent
    console.print(f"[dim]Config dir: {config_dir}[/dim]")
    console.print(
        "[dim]Re-run any wizard with: cldx setup [llm|anthropic|bedrock|gemini|telegram|all][/dim]"
    )


def _source_for(var: str) -> str:
    """Where did this variable's value come from? (best-effort)"""
    if var == "ANTHROPIC_API_KEY":
        path = env_file_path("anthropic")
    elif var.startswith("AWS_") or var == "AWS_REGION":
        path = env_file_path("bedrock")
    elif var == "GEMINI_API_KEY" or var == "GOOGLE_API_KEY":
        path = env_file_path("gemini")
    elif var.startswith("TELEGRAM_"):
        path = env_file_path("telegram")
    else:
        return "?"
    if path.exists():
        return path.name
    if os.environ.get(var):
        return "shell env"
    return "—"
