"""Daemon-side Telegram Bot API client.

Direct Bot API over HTTPS via aiohttp — no bot framework (PLAN.md 4.5 / 4.4).
The daemon needs exactly this surface and nothing more:

  - ``get_updates``   — long-poll for new updates (offset-committing)
  - ``send_message``  — outbound text (pool acks, notices)
  - ``edit_message_text`` — update an existing message (progress edits)
  - ``answer_callback_query`` — ack inline-keyboard taps (the ABS START flow)
  - ``set_my_commands`` — register the "/" command menu for daemon mode

Wire shapes are the spec (PLAN.md 4.4): every method maps 1:1 onto a Bot API
method at ``<base_url>/bot<token>/<method>``. ``base_url`` defaults to the real
``https://api.telegram.org`` but tests override it to point at the local
``fake_telegram`` server — the client is otherwise identical in tests and prod
(PLAN.md section 10 forbids real Telegram traffic in automated tests).

Security (PLAN.md 5.5): the bot token is passed in at construction and lives
only in the request URL. It is never logged, never placed in an exception
message, and never included in ``repr``. ``__repr__`` is overridden to redact it.

Resilience (Step 1.3 spec): every call has a timeout; a single retry covers a
transient network blip; an HTTP 409 (a second getUpdates consumer owns the
token — e.g. the in-session plugin) surfaces as the distinct ``Conflict409`` so
the poller can back off rather than treat it as a generic error.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

# Per-request ceiling above the long-poll timeout: getUpdates(timeout=50) must be
# allowed to sit for 50 s, so the socket read timeout is the poll timeout plus
# this slack. Applied in get_updates; the short methods use SHORT_TIMEOUT_S.
_POLL_SLACK_S = 10.0
SHORT_TIMEOUT_S = 15.0


class TelegramError(RuntimeError):
    """A Bot API call failed (network error, non-OK response, bad status).

    The token is never included in the message (PLAN.md 5.5).
    """


class Conflict409(TelegramError):
    """getUpdates returned HTTP 409 — another consumer owns this token.

    In ABS this means the in-session telegram plugin is polling: the daemon must
    yield and back off (PLAN.md 4.1 RECLAIM / IDLE_POLLING 409 handling), not
    treat it as a hard failure.
    """


class TelegramClient:
    """Minimal async Bot API client bound to a single bot token.

    Construct with the profile's token and, in tests, the fake server's
    ``base_url``. Reuses one ``aiohttp.ClientSession`` for the client's life;
    call :meth:`close` (or use as an async context manager) to release it.
    """

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.telegram.org",
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._token = token
        self.base_url = base_url.rstrip("/")
        self._session = session
        self._owns_session = session is None

    # ---- token hygiene ---------------------------------------------------

    def __repr__(self) -> str:
        # Never leak the token via repr/logging (PLAN.md 5.5).
        return f"TelegramClient(base_url={self.base_url!r}, token=<redacted>)"

    def _url(self, method: str) -> str:
        return f"{self.base_url}/bot{self._token}/{method}"

    # ---- lifecycle -------------------------------------------------------

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._session is not None and self._owns_session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def __aenter__(self) -> "TelegramClient":
        await self._ensure_session()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # ---- core request ----------------------------------------------------

    async def _request(
        self, method: str, params: dict[str, Any], timeout_s: float
    ) -> Any:
        """POST ``params`` to a Bot API ``method``; return its ``result``.

        One retry on a transient network/timeout error (not on a 409, which is a
        real protocol state we surface immediately). The token appears only in
        the URL and never in any raised message.
        """
        session = await self._ensure_session()
        url = self._url(method)
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        last_exc: Exception | None = None

        for attempt in range(2):  # initial try + one retry
            try:
                async with session.post(url, json=params, timeout=timeout) as resp:
                    if resp.status == 409:
                        # Distinct signal: another getUpdates consumer owns the
                        # token. Body is drained but never surfaced (may echo
                        # nothing sensitive, but keep the message generic).
                        await resp.read()
                        raise Conflict409(f"{method}: HTTP 409 (token owned elsewhere)")
                    if resp.status != 200:
                        text = await resp.text()
                        raise TelegramError(
                            f"{method}: HTTP {resp.status}: {text[:200]}"
                        )
                    body = await resp.json()
                    if not isinstance(body, dict) or not body.get("ok", False):
                        desc = (
                            body.get("description")
                            if isinstance(body, dict)
                            else "malformed response"
                        )
                        raise TelegramError(f"{method}: not ok: {desc}")
                    return body.get("result")
            except Conflict409:
                raise  # never retry a 409 — it is a stable state, not a blip
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                if attempt == 0:
                    # Brief pause before the single retry (transient blip).
                    await asyncio.sleep(0.2)
                    continue
                raise TelegramError(f"{method}: network error: {exc}") from exc

        # Unreachable (loop either returns or raises), but keeps type-checkers calm.
        raise TelegramError(f"{method}: network error: {last_exc}")

    # ---- Bot API methods -------------------------------------------------

    async def get_updates(
        self, offset: int | None = None, timeout: int = 50, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Long-poll ``getUpdates``; return the raw update objects (possibly []).

        ``offset`` both confirms every update below it (Bot API semantics) and
        selects the next batch. Raises :class:`Conflict409` on HTTP 409.
        """
        params: dict[str, Any] = {"timeout": timeout, "limit": limit}
        if offset is not None:
            params["offset"] = offset
        # Socket must outlive the server-side long poll.
        result = await self._request(
            "getUpdates", params, timeout_s=timeout + _POLL_SLACK_S
        )
        return result if isinstance(result, list) else []

    async def send_message(
        self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send a text message, optionally with an inline keyboard."""
        params: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        result = await self._request("sendMessage", params, timeout_s=SHORT_TIMEOUT_S)
        return result if isinstance(result, dict) else {}

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Edit an existing message's text/keyboard."""
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        result = await self._request(
            "editMessageText", params, timeout_s=SHORT_TIMEOUT_S
        )
        return result if isinstance(result, dict) else {}

    async def answer_callback_query(
        self, callback_query_id: str, text: str | None = None
    ) -> dict[str, Any]:
        """Acknowledge an inline-keyboard callback query (the ABS START flow)."""
        params: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text is not None:
            params["text"] = text
        result = await self._request(
            "answerCallbackQuery", params, timeout_s=SHORT_TIMEOUT_S
        )
        return result if isinstance(result, dict) else {"ok": True}

    async def set_my_commands(self, commands: list[dict[str, str]]) -> dict[str, Any]:
        """Register the daemon-mode "/" command menu."""
        params: dict[str, Any] = {"commands": commands}
        result = await self._request("setMyCommands", params, timeout_s=SHORT_TIMEOUT_S)
        return result if isinstance(result, dict) else {"ok": True}
