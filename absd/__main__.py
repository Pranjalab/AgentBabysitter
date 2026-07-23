"""Daemon entry point: ``python -m absd``.

This is the process the systemd user unit launches (PLAN.md Step 1.3 / 1.8). It
is deliberately thin (PLAN.md 4.4): it only wires pieces together — config load,
profile discovery, one poller task per profile, signal-driven shutdown, logging.
All behavior lives in :mod:`absd.daemon`, :mod:`absd.profiles`, and the pure
helpers, which unit-test without a running process.

Command line::

    python -m absd [--abs-home DIR] [--config FILE] [--once] [--log-level LVL]

  --abs-home   Root of the ABS home (default: $ABS_HOME or ~/.abs).
  --config     config.json path (default: <abs-home>/daemon/config.json).
  --once       Run exactly one poll cycle per profile, then exit. Invaluable in
               tests and for debugging — no long-poll loop, no signal wait.
  --log-level  DEBUG/INFO/WARNING/… (default: INFO).

Test seams (NOT for production use), both localhost-guarded so they can never
silently redirect a real deployment:

  * ``ABS_TELEGRAM_BASE_URL`` (env, global) — overrides the Bot API base URL for
    every client. Unset in production, every client talks to
    https://api.telegram.org.
  * ``<profile_dir>/.telegram-base-url`` (file, per-profile) — points ONE profile
    at ONE FakeTelegram instance, so a multi-profile test can give each fake bot
    its own server (Step 1.4). Read only through :func:`_resolve_base_url`, which
    **refuses any non-localhost value** (127.0.0.1 / localhost / ::1 only) and
    falls back to the global base — a stray or hostile file can never redirect a
    real deployment. Absent in production.

Automated tests ALWAYS use one of these (plus a temp ABS_HOME and fake tokens)
so no test ever reaches real Telegram (PLAN.md section 10 / the Step 1.3 safety
rule).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from urllib.parse import urlparse

from absd import __version__
from absd import config as config_mod
from absd.daemon import Poller, read_status_files, render_daemon_status
from absd.engines import get_engine
from absd.engines.base import Engine
from absd.events import (
    EVENT_DAEMON_START,
    EVENT_DAEMON_STOP,
    EventLog,
)
from absd.profiles import Profile, discover
from absd.telegram import TelegramClient

log = logging.getLogger("absd")

# daemon.log size cap before a single ".old" roll (real rotation is Step 1.8).
_LOG_MAX_BYTES = 2 * 1024 * 1024

# Per-profile supervisor restart backoff (Step 1.4): a poller task that dies
# unexpectedly is restarted after this delay, doubling up to the cap. A dead
# poller is a deaf bot — the daemon's job is to never be silently deaf.
_RESTART_BACKOFF_INITIAL_S = 1.0
_RESTART_BACKOFF_MAX_S = 30.0

# Hosts the per-profile TEST base-URL override is allowed to point at. Anything
# else is refused so the seam cannot redirect a real deployment (Step 1.4).
_LOCALHOST_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _is_localhost_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname
    except ValueError:
        return False
    return host in _LOCALHOST_HOSTS


def _resolve_base_url(profile: Profile, global_base_url: str) -> str:
    """Resolve the Bot API base URL for one profile.

    Honors the per-profile ``.telegram-base-url`` TEST seam ONLY when it points
    at localhost; a non-localhost (or unreadable) value is refused with a loud
    log and the global base is used instead, so the seam can never silently
    redirect a real deployment (Step 1.4 safety guard)."""
    try:
        raw = profile.test_base_url_path.read_text(encoding="utf-8").strip()
    except OSError:
        return global_base_url
    if not raw:
        return global_base_url
    if not _is_localhost_url(raw):
        log.error(
            "profile %s: REFUSING non-localhost .telegram-base-url override %r "
            "— using default base URL",
            profile.name,
            raw,
        )
        return global_base_url
    log.warning(
        "profile %s: using TEST base_url override %s (test-only, localhost)",
        profile.name,
        raw,
    )
    return raw


def _default_abs_home() -> Path:
    return Path(os.environ.get("ABS_HOME") or (Path.home() / ".abs"))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="absd", description="Agent Babysitter always-on daemon (ABS v3)."
    )
    parser.add_argument("--abs-home", type=Path, default=None, help="ABS home root")
    parser.add_argument("--config", type=Path, default=None, help="config.json path")
    parser.add_argument(
        "--once", action="store_true", help="one poll cycle per profile, then exit"
    )
    parser.add_argument(
        "--print-status",
        action="store_true",
        help="print the per-profile status block (from persisted status files) and exit",
    )
    parser.add_argument("--log-level", default="INFO", help="log level (default INFO)")
    parser.add_argument(
        "--version", action="version", version=f"absd {__version__}"
    )
    return parser.parse_args(argv)


def _setup_logging(daemon_dir: Path, level: str) -> None:
    """Structured logging to stderr + <daemon_dir>/daemon.log (append).

    Applies a crude size cap with one ``.old`` roll before opening the file
    (full rotation is Step 1.8). Never logs tokens (the client redacts them).
    """
    daemon_dir.mkdir(parents=True, exist_ok=True)
    log_path = daemon_dir / "daemon.log"
    try:
        if log_path.exists() and log_path.stat().st_size > _LOG_MAX_BYTES:
            old = log_path.with_suffix(".log.old")
            os.replace(str(log_path), str(old))
    except OSError:
        pass

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S"
    )
    root = logging.getLogger("absd")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    stderr_h = logging.StreamHandler(sys.stderr)
    stderr_h.setFormatter(fmt)
    root.addHandler(stderr_h)

    try:
        file_h = logging.FileHandler(str(log_path), encoding="utf-8")
        file_h.setFormatter(fmt)
        root.addHandler(file_h)
        try:
            os.chmod(log_path, 0o600)
        except OSError:
            pass
    except OSError:
        log.warning("could not open daemon.log for writing")


def _default_script_path() -> str:
    """Absolute path to abs.sh (the launcher the engine runs at HANDOFF, 4.2).

    Beside the ``absd`` package (repo root) in a dev checkout / the daemon's own
    tree. Overridable by ``ABS_SCRIPT_PATH`` (tests point it at a stub launcher)."""
    override = os.environ.get("ABS_SCRIPT_PATH")
    if override:
        return override
    return str(Path(__file__).resolve().parents[1] / "abs.sh")


def _build_pollers(
    profiles: list[Profile],
    cfg: config_mod.DaemonConfig,
    daemon_dir: Path,
    base_url: str,
    engine: "Engine | None" = None,
    script_path: str | None = None,
    events: "EventLog | None" = None,
) -> list[tuple[Poller, TelegramClient]]:
    """One (poller, client) per profile that has a usable token.

    Each poller is fully independent (own client/token/base_url, own pool, own
    offset + status files, own backoff, own ``poller[<name>]`` log prefix) — the
    isolation G5 requires. ``base_url`` is the global default; a per-profile
    localhost override may replace it via :func:`_resolve_base_url`.

    ``engine`` is shared across pollers so ``list_sessions`` sees every profile's
    session (the max_sessions cap counts across all profiles, G5). ``session_count``
    on each poller therefore reports the daemon-wide live-session total."""
    built: list[tuple[Poller, TelegramClient]] = []
    script_path = script_path or _default_script_path()

    def _live_session_count() -> int:
        if engine is None:
            return 0
        try:
            return sum(1 for s in engine.list_sessions() if s.alive)
        except Exception:  # engine hiccup must never block a start decision
            return 0

    for profile in profiles:
        token = profile.load_token()
        if not token:
            log.warning("profile %s has no token — skipping", profile.name)
            continue
        profile_base_url = _resolve_base_url(profile, base_url)
        client = TelegramClient(token, base_url=profile_base_url)
        poller = Poller(
            profile,
            client,
            cfg,
            state_dir=daemon_dir,
            engine=engine,
            script_path=script_path,
            session_count=_live_session_count,
            events=events,
        )
        built.append((poller, client))
        log.info("profile %s: poller ready (tg_dir=%s)", profile.name, profile.tg_dir)
    return built


def _log_boot_state(profiles: list[Profile]) -> None:
    """Boot-time state detection (PLAN.md 4.1): classify and LOUDLY log each
    profile's initial state so the operator can see, at startup, which bots are
    yielding to a live session and which are polling. A stale ``session.pid``
    (process dead) is logged as such and treated as idle — but the pid file is
    left untouched (deleting it is the launcher's / Step 1.5's job)."""
    for p in profiles:
        live = p.live_session_pid()
        if live is not None:
            log.info(
                "boot: profile %s — LIVE session (pid %d); starting in yielding state",
                p.name,
                live,
            )
        elif p.has_stale_session_pid():
            log.warning(
                "boot: profile %s — STALE session.pid (pid %s not alive); "
                "treating as idle, pid file left intact",
                p.name,
                p.session_pid_on_disk(),
            )
        elif p.is_blocked():
            log.info("boot: profile %s — BLOCKED (ABS BLOCK); not polling", p.name)
        elif p.is_off():
            log.info("boot: profile %s — OFF (inbound disabled); not polling", p.name)
        else:
            log.info("boot: profile %s — idle; polling", p.name)


async def _run_once(pollers: list[tuple[Poller, TelegramClient]]) -> None:
    for poller, _ in pollers:
        try:
            await poller.poll_once()
        except Exception:  # one profile's failure never aborts the sweep
            log.exception("profile %s: --once poll failed", poller.profile.name)
        finally:
            poller.write_status()


async def _cancel_and_wait(*tasks: "asyncio.Future") -> None:
    """Cancel each pending task and await it, swallowing the fallout. Ensures a
    supervisor never orphans a child poller task (which would leak its client
    session)."""
    for t in tasks:
        if not t.done():
            t.cancel()
    for t in tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass


async def _sleep_or_stop(delay: float, stop: asyncio.Event, sleep) -> bool:
    """Sleep ``delay`` seconds but wake early if ``stop`` is set. Returns True if
    ``stop`` fired during the wait. Used for the first-cycle stagger so shutdown
    during startup is immediate."""
    if delay <= 0:
        return stop.is_set()
    sleep_task = asyncio.ensure_future(sleep(delay))
    stop_task = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({sleep_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        sleep_task.cancel()
        stop_task.cancel()
        for t in (sleep_task, stop_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
    return stop.is_set()


async def _run_profile(
    poller: Poller,
    stagger_delay: float,
    stop: asyncio.Event,
    stagger_sleep=asyncio.sleep,
    supervise_sleep=asyncio.sleep,
    poller_sleep=asyncio.sleep,
) -> None:
    """Run ONE profile's poller as an independent, supervised task.

    Responsibilities (Step 1.4):
      * **Stagger** — wait ``stagger_delay`` before the first cycle only (R10
        thundering-herd avoidance); subsequent polls are not staggered.
      * **Supervision** — run ``poller.run()`` to completion; if it dies with an
        *unexpected* exception (not 409, not an operational Telegram error — those
        stay inside the loop), log it LOUDLY and restart with exponential backoff.
        A dead poller is a deaf bot; the daemon must never be silently deaf. One
        profile restarting never touches another's task.

    The three ``*_sleep`` seams let tests compress time without real waits.
    """
    name = poller.profile.name
    if await _sleep_or_stop(stagger_delay, stop, stagger_sleep):
        return
    poller.write_status()  # seed the status file at (staggered) startup

    backoff = _RESTART_BACKOFF_INITIAL_S
    while not stop.is_set():
        run_task = asyncio.ensure_future(poller.run(sleep=poller_sleep))
        stop_task = asyncio.ensure_future(stop.wait())
        try:
            await asyncio.wait({run_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            # This supervisor task was cancelled directly (shutdown). Never orphan
            # the child run_task — tear it down before re-raising, or its open
            # client session leaks.
            poller.stop()
            await _cancel_and_wait(run_task, stop_task)
            raise

        if stop.is_set():
            poller.stop()
            await _cancel_and_wait(run_task, stop_task)
            return

        # run_task finished on its own while we were NOT asked to stop: for a
        # long-poll loop that means it crashed (it otherwise runs forever). Keep
        # the poller runnable (do NOT call poller.stop) so it can restart.
        await _cancel_and_wait(stop_task)
        exc = None if run_task.cancelled() else run_task.exception()
        if exc is not None:
            log.error(
                "poller[%s] DIED unexpectedly — restarting in %.1fs",
                name,
                backoff,
                exc_info=exc,
            )
            poller.write_status()
            if await _sleep_or_stop(backoff, stop, supervise_sleep):
                return
            backoff = min(backoff * 2, _RESTART_BACKOFF_MAX_S)
        else:
            # Clean return without a stop (e.g. max_cycles in a test): done.
            return


async def _run_forever(
    pollers: list[tuple[Poller, TelegramClient]], cfg: config_mod.DaemonConfig
) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):  # e.g. non-main thread
            pass

    tasks = [
        asyncio.ensure_future(_run_profile(poller, cfg.poll_stagger_s * i, stop))
        for i, (poller, _) in enumerate(pollers)
    ]
    if not tasks:
        log.warning("no pollers to run — idling until signalled")
        await stop.wait()
        return
    await stop.wait()
    log.info("shutdown signal received — stopping %d poller(s)", len(tasks))
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass


async def _amain(args: argparse.Namespace) -> int:
    abs_home = Path(args.abs_home) if args.abs_home else _default_abs_home()
    daemon_dir = abs_home / "daemon"
    config_path = Path(args.config) if args.config else daemon_dir / "config.json"

    # --print-status is a pure read of persisted status files (for the bash
    # `abs daemon status`): no logging setup, no discovery, no network.
    if args.print_status:
        print(render_daemon_status(read_status_files(daemon_dir)))
        return 0

    _setup_logging(daemon_dir, args.log_level)
    log.info("absd %s starting (abs_home=%s, once=%s)", __version__, abs_home, args.once)

    try:
        cfg = config_mod.load(config_path)
    except config_mod.ConfigError as exc:
        log.error("invalid config: %s", exc)
        return 2

    base_url = os.environ.get("ABS_TELEGRAM_BASE_URL", "https://api.telegram.org")
    home = Path(os.environ.get("HOME") or Path.home())
    profiles = discover(abs_home, home=home)
    log.info("discovered %d profile(s): %s", len(profiles), [p.name for p in profiles])
    _log_boot_state(profiles)

    # One shared session engine for the whole daemon (PLAN.md 4.2): the ABS START
    # HANDOFF launches through it, and list_sessions counts sessions across all
    # profiles for the max_sessions cap (G5).
    try:
        engine = get_engine(cfg.engine)
        log.info("session engine: %s", engine.name)
    except Exception as exc:  # never let engine selection abort daemon startup
        log.warning("could not select session engine (%s); ABS START disabled", exc)
        engine = None

    # Structured event log (observability): one shared writer for the daemon,
    # alongside the human-readable daemon.log. Metadata only — never message text.
    events = EventLog(daemon_dir / "events.jsonl")
    events.emit(EVENT_DAEMON_START, version=__version__, profiles=[p.name for p in profiles])

    pollers = _build_pollers(
        profiles, cfg, daemon_dir, base_url, engine=engine, events=events
    )
    stop_reason = "signal"
    try:
        if args.once:
            await _run_once(pollers)
            stop_reason = "once"
        else:
            await _run_forever(pollers, cfg)
    finally:
        events.emit(EVENT_DAEMON_STOP, reason=stop_reason)
        for _, client in pollers:
            try:
                await client.close()
            except Exception:
                pass
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
