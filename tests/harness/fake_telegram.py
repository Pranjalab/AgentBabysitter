"""FakeTelegram — a local stand-in for the Telegram Bot API.

Implements exactly the surface the daemon uses (PLAN.md 4.5): ``getUpdates`` with
long-poll + offset semantics, ``sendMessage`` / ``editMessageText`` /
``answerCallbackQuery`` (captured for assertions), and ``setMyCommands``. Plus
the fault injection the tests need: HTTP 409 on demand (a second poller) and a
configurable response delay.

Binds to 127.0.0.1 on an ephemeral port only — never a public interface, never
real Telegram (PLAN.md section 10).

Offset model (matches real Bot API, so the daemon's real client can drive it
unchanged later):

  - Updates live in ``_pending`` until *confirmed*.
  - A ``getUpdates`` call with ``offset=N`` confirms (drops) every pending update
    with ``update_id < N`` and returns the rest with ``update_id >= N``.
  - Calling ``getUpdates`` again WITHOUT advancing the offset re-delivers the
    same updates (real Bot API behavior) — this is what lets a test prove
    "nothing replayed once confirmed, nothing lost before confirmation".

Usage in a test::

    fake = FakeTelegram()
    base = await fake.start()
    fake.queue_message("hello", from_id=42)
    ...  # drive an aiohttp client at fake.method_url("getUpdates")
    await fake.stop()
"""

from __future__ import annotations

import asyncio
import itertools
import json
from typing import Any

from aiohttp import web

# The five methods the daemon is allowed to use (PLAN.md 4.5). Anything else 404s.
_SUPPORTED = ("getUpdates", "sendMessage", "editMessageText", "answerCallbackQuery", "setMyCommands")


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _maybe_json(value: Any) -> Any:
    """Parse a value that may arrive as a JSON string (form encoding) or already
    as a Python object (JSON body). Leaves non-JSON strings untouched."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


class FakeTelegram:
    """Controllable fake Bot API server. One instance == one bot token."""

    def __init__(self, token: str = "TEST", response_delay: float = 0.0) -> None:
        self.token = token
        self.response_delay = response_delay

        # Pending (unconfirmed) updates, kept sorted by update_id.
        self._pending: list[dict[str, Any]] = []
        self._update_seq = itertools.count(1)  # auto update_id
        self._message_seq = itertools.count(1000)  # auto message_id
        self._new_update = asyncio.Event()

        # Fault injection.
        self.always_409 = False
        self._409_pulses = 0

        # Captured outbound calls (assertion surface).
        self.sent_messages: list[dict[str, Any]] = []
        self.edited_messages: list[dict[str, Any]] = []
        self.answered_callbacks: list[dict[str, Any]] = []
        self.commands: list[Any] = []

        # Bookkeeping for offset/invariant assertions.
        self.getupdates_calls = 0
        self.confirmed_offset: int | None = None
        self.delivered_ids: list[int] = []  # every update_id ever returned
        #: monotonic timestamp of the FIRST getUpdates this fake ever saw — lets a
        #: test assert staggered poller start spacing (Step 1.4) without long real
        #: sleeps. None until the first call.
        self.first_getupdates_at: float | None = None

        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._port: int | None = None

    # ---- lifecycle -------------------------------------------------------

    async def start(self) -> str:
        """Bind on 127.0.0.1:<ephemeral> and return the base URL."""
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", self._dispatch)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await self._site.start()
        # addresses is populated after the site starts: [(host, port), ...]
        host, port = list(self._runner.addresses)[0][:2]
        self._port = int(port)
        return self.base_url

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    @property
    def port(self) -> int:
        assert self._port is not None, "server not started"
        return self._port

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def method_url(self, method: str) -> str:
        """URL for a Bot API method, mirroring api.telegram.org/bot<token>/<m>."""
        return f"{self.base_url}/bot{self.token}/{method}"

    # ---- test-side controls: queue updates -------------------------------

    def queue_update(self, update: dict[str, Any]) -> int:
        """Queue a raw update dict; assigns update_id if absent. Returns its id."""
        upd = dict(update)
        if "update_id" not in upd:
            upd["update_id"] = next(self._update_seq)
        self._pending.append(upd)
        self._pending.sort(key=lambda u: u["update_id"])
        self._new_update.set()
        return upd["update_id"]

    def queue_message(
        self,
        text: str,
        from_id: int,
        chat_id: int | None = None,
        update_id: int | None = None,
    ) -> int:
        """Queue a plain text message update."""
        chat_id = from_id if chat_id is None else chat_id
        upd: dict[str, Any] = {
            "message": {
                "message_id": next(self._message_seq),
                "from": {"id": from_id},
                "chat": {"id": chat_id},
                "text": text,
            }
        }
        if update_id is not None:
            upd["update_id"] = update_id
        return self.queue_update(upd)

    def queue_callback_query(
        self,
        data: str,
        from_id: int,
        message_id: int = 1,
        chat_id: int | None = None,
        update_id: int | None = None,
    ) -> int:
        """Queue an inline-keyboard callback_query update (the ABS START flow)."""
        chat_id = from_id if chat_id is None else chat_id
        upd: dict[str, Any] = {
            "callback_query": {
                "id": f"cbq-{from_id}-{data}",
                "from": {"id": from_id},
                "data": data,
                "message": {"message_id": message_id, "chat": {"id": chat_id}},
            }
        }
        if update_id is not None:
            upd["update_id"] = update_id
        return self.queue_update(upd)

    # ---- test-side controls: fault injection -----------------------------

    def inject_409(self, times: int = 1) -> None:
        """Make the next ``times`` getUpdates calls return HTTP 409."""
        self._409_pulses += times

    def set_always_409(self, on: bool = True) -> None:
        """Persistently return 409 for getUpdates (a stuck second poller)."""
        self.always_409 = on

    def set_delay(self, seconds: float) -> None:
        self.response_delay = seconds

    # ---- request handling ------------------------------------------------

    async def _dispatch(self, request: web.Request) -> web.Response:
        method = request.match_info["path"].rsplit("/", 1)[-1]
        params = await self._params(request)
        if method not in _SUPPORTED:
            return web.json_response(
                {"ok": False, "error_code": 404, "description": f"unknown method {method}"},
                status=404,
            )
        if self.response_delay:
            await asyncio.sleep(self.response_delay)
        handler = getattr(self, f"_m_{method}")
        return await handler(params)

    async def _params(self, request: web.Request) -> dict[str, Any]:
        params: dict[str, Any] = dict(request.query)
        if request.body_exists:
            try:
                body = await request.json()
                if isinstance(body, dict):
                    params.update(body)
            except (ValueError, TypeError):
                try:
                    form = await request.post()
                    params.update({k: v for k, v in form.items()})
                except Exception:  # pragma: no cover - defensive
                    pass
        return params

    def _should_409(self) -> bool:
        if self._409_pulses > 0:
            self._409_pulses -= 1
            return True
        return self.always_409

    def _collect(self, offset: int | None, limit: int) -> list[dict[str, Any]]:
        items = [u for u in self._pending if offset is None or u["update_id"] >= offset]
        return items[:limit]

    # ---- Bot API methods -------------------------------------------------

    async def _m_getUpdates(self, params: dict[str, Any]) -> web.Response:
        self.getupdates_calls += 1
        if self.first_getupdates_at is None:
            import time

            self.first_getupdates_at = time.monotonic()
        if self._should_409():
            return web.json_response(
                {
                    "ok": False,
                    "error_code": 409,
                    "description": "Conflict: terminated by other getUpdates request",
                },
                status=409,
            )

        raw_offset = params.get("offset")
        offset = _to_int(raw_offset) if raw_offset not in (None, "") else None
        timeout = float(params.get("timeout", 0) or 0)
        limit = _to_int(params.get("limit")) or 100

        if offset is not None:
            self.confirmed_offset = (
                offset if self.confirmed_offset is None else max(self.confirmed_offset, offset)
            )
            # Confirm: drop everything the client has acked by advancing offset.
            self._pending = [u for u in self._pending if u["update_id"] >= offset]

        result = self._collect(offset, limit)
        if not result and timeout > 0:
            self._new_update.clear()
            try:
                await asyncio.wait_for(self._new_update.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
            result = self._collect(offset, limit)

        self.delivered_ids.extend(u["update_id"] for u in result)
        return web.json_response({"ok": True, "result": result})

    async def _m_sendMessage(self, params: dict[str, Any]) -> web.Response:
        message_id = next(self._message_seq)
        record = {
            "chat_id": _to_int(params.get("chat_id")),
            "text": params.get("text"),
            "reply_markup": _maybe_json(params.get("reply_markup")),
            "message_id": message_id,
        }
        self.sent_messages.append(record)
        return web.json_response(
            {
                "ok": True,
                "result": {
                    "message_id": message_id,
                    "chat": {"id": _to_int(params.get("chat_id"))},
                    "text": params.get("text"),
                },
            }
        )

    async def _m_editMessageText(self, params: dict[str, Any]) -> web.Response:
        record = {
            "chat_id": _to_int(params.get("chat_id")),
            "message_id": _to_int(params.get("message_id")),
            "text": params.get("text"),
            "reply_markup": _maybe_json(params.get("reply_markup")),
        }
        self.edited_messages.append(record)
        return web.json_response({"ok": True, "result": {**record, "edited": True}})

    async def _m_answerCallbackQuery(self, params: dict[str, Any]) -> web.Response:
        record = {
            "callback_query_id": params.get("callback_query_id"),
            "text": params.get("text"),
        }
        self.answered_callbacks.append(record)
        return web.json_response({"ok": True, "result": True})

    async def _m_setMyCommands(self, params: dict[str, Any]) -> web.Response:
        self.commands.append(_maybe_json(params.get("commands")))
        return web.json_response({"ok": True, "result": True})


async def _demo() -> None:  # pragma: no cover - manual poking only
    """Run the fake standalone (manual use); prints its base URL and idles."""
    fake = FakeTelegram()
    base = await fake.start()
    print(f"fake-telegram listening at {base} (bot token '{fake.token}')")
    print(f"  getUpdates: {fake.method_url('getUpdates')}")
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        await fake.stop()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_demo())
