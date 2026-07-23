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

Test seam (NOT for production use): ``ABS_TELEGRAM_BASE_URL`` overrides the Bot
API base URL for every client so the suite points at the local ``fake_telegram``
server. Unset in production, every client talks to https://api.telegram.org.
Automated tests ALWAYS set it (plus a temp ABS_HOME and fake tokens) so no test
ever reaches real Telegram (PLAN.md section 10 / the Step 1.3 safety rule).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from absd import __version__
from absd import config as config_mod
from absd.daemon import Poller
from absd.profiles import Profile, discover
from absd.telegram import TelegramClient

log = logging.getLogger("absd")

# daemon.log size cap before a single ".old" roll (real rotation is Step 1.8).
_LOG_MAX_BYTES = 2 * 1024 * 1024


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


def _build_pollers(
    profiles: list[Profile],
    cfg: config_mod.DaemonConfig,
    daemon_dir: Path,
    base_url: str,
) -> list[tuple[Poller, TelegramClient]]:
    """One (poller, client) per profile that has a usable token."""
    built: list[tuple[Poller, TelegramClient]] = []
    for profile in profiles:
        token = profile.load_token()
        if not token:
            log.warning("profile %s has no token — skipping", profile.name)
            continue
        client = TelegramClient(token, base_url=base_url)
        poller = Poller(profile, client, cfg, state_dir=daemon_dir)
        built.append((poller, client))
        log.info("profile %s: poller ready (tg_dir=%s)", profile.name, profile.tg_dir)
    return built


async def _run_once(pollers: list[tuple[Poller, TelegramClient]]) -> None:
    for poller, _ in pollers:
        try:
            await poller.poll_once()
        except Exception:  # one profile's failure never aborts the sweep
            log.exception("profile %s: --once poll failed", poller.profile.name)


async def _staggered(poller: Poller, delay: float, stop: asyncio.Event) -> None:
    """Start a poller after ``delay`` seconds (R10 thundering-herd stagger),
    then run until ``stop`` is set."""
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    poller_task = asyncio.ensure_future(poller.run())
    stop_task = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({poller_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        poller.stop()
        poller_task.cancel()
        stop_task.cancel()
        for t in (poller_task, stop_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


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
        asyncio.ensure_future(_staggered(poller, cfg.poll_stagger_s * i, stop))
        for i, (poller, _) in enumerate(pollers)
    ]
    if not tasks:
        log.warning("no pollers to run — idling until signalled")
        await stop.wait()
        return
    await stop.wait()
    log.info("shutdown signal received — cancelling %d poller(s)", len(tasks))
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

    pollers = _build_pollers(profiles, cfg, daemon_dir, base_url)
    try:
        if args.once:
            await _run_once(pollers)
        else:
            await _run_forever(pollers, cfg)
    finally:
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
