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
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
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
    input_fn: Callable[[str], str] = input,
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


# --- Telegram -------------------------------------------------------------


def run_telegram_setup(
    console: Console | None = None,
    input_fn: Callable[[str], str] = input,
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
    input_fn: Callable[[str], str] = input,
) -> None:
    """Run both wizards in sequence, then show the final config."""
    console = console or Console()
    console.print(Panel(
        "Let's set up the optional integrations cldx can use:\n\n"
        "  • [cyan]Anthropic API[/cyan] — for Telegram message summarization\n"
        "  • [cyan]Telegram bot[/cyan] — for remote approvals from your phone",
        title="[bold cyan]cldx setup[/bold cyan]",
        border_style="cyan",
    ))
    run_anthropic_setup(console=console, input_fn=input_fn)
    console.print()
    run_telegram_setup(console=console, input_fn=input_fn)
    console.print()
    show_config(console)


def show_config(console: Console | None = None) -> None:
    """Print a masked view of every secret cldx currently knows about."""
    console = console or Console()
    t = Table(title="cldx config", show_header=True, header_style="bold")
    t.add_column("setting", style="bold")
    t.add_column("value")
    t.add_column("source", style="dim")

    for var in ("ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        v = os.environ.get(var)
        if var == "TELEGRAM_CHAT_ID":
            display = v if v else mask_secret(None)
        else:
            display = mask_secret(v)
        t.add_row(var, display, _source_for(var))
    console.print(t)

    config_dir = env_file_path("anthropic").parent
    console.print(f"[dim]Config dir: {config_dir}[/dim]")
    console.print(
        "[dim]Re-run any wizard with: cldx setup [anthropic|telegram|all][/dim]"
    )


def _source_for(var: str) -> str:
    """Where did this variable's value come from? (best-effort)"""
    if var == "ANTHROPIC_API_KEY":
        path = env_file_path("anthropic")
    elif var.startswith("TELEGRAM_"):
        path = env_file_path("telegram")
    else:
        return "?"
    if path.exists():
        return path.name
    if os.environ.get(var):
        return "shell env"
    return "—"
