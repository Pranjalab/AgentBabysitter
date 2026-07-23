"""Docker sandbox lifecycle (Phase 3 / PLAN.md 9, 5.6, D7/D8, G7).

``abs sandbox`` runs Claude Code inside a disposable Ubuntu container. The one host
path a sandbox can see is a **dedicated folder** ``<sandbox_root>/<name>`` (0700),
bind-mounted at ``/home/dev/workspace`` — a user-requested change from the plan's
"nothing shared / project inside a named volume": the operator wants work synced
live to a local dir, so the jail boundary is "exactly one dedicated folder shared"
rather than "nothing shared". Everything else on the host stays invisible.

Security (5.6): non-root ``dev`` user, no ``--privileged``, no docker-socket mount,
no host mount other than the one workdir, only explicit port publishes. The one
host secret inside is a **copy** (``docker cp``, D8) of ``~/.claude`` — copies
diverge independently and the host copy is never touched; the tradeoff is printed
at ``create``.

All docker calls use ``subprocess`` with explicit argv lists (never ``shell=True``)
and timeouts. Argv-building is factored into pure methods (``create_argv`` /
``exec_argv``) so the security shape is unit-testable without running docker.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from absd import flow as flow_mod

#: Pinned image tag (D13-style: upgrades are explicit via --rebuild). v2 (3.2)
#: adds bun (the Telegram plugin's MCP-server runtime) + the in-container
#: `absd-session` launcher — an old v1 box lacks both, so a rebuild is required
#: for sandbox SESSIONS (3.1 sandboxes on v1 still shell in fine). Migration:
#: `abs sandbox build --rebuild` then re-create sandboxes to pick up v2.
IMAGE_TAG = "absd-sandbox:v2"
#: Container name = this prefix + the sandbox name.
CONTAINER_PREFIX = "absd-sbx-"
#: The single bind-mount target inside the container.
WORKDIR = "/home/dev/workspace"
#: Where copied credentials land inside the container (D8).
CRED_DIR = "/home/dev/.claude"
#: The in-container launcher (baked into the image) that runs claude (3.2).
SESSION_LAUNCHER = "absd-session"

_MODE = 0o600
_DEFAULT_TIMEOUT = 30.0
_BUILD_TIMEOUT = 900.0  # first build pulls ~1GB (node + claude); cached after
_PORT_RE = re.compile(r"^\d{1,5}:\d{1,5}$")


class SandboxError(RuntimeError):
    """A sandbox operation failed (bad name/ports, docker error, missing image)."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_abs_home() -> Path:
    return Path(os.environ.get("ABS_HOME") or (Path.home() / ".abs"))


def parse_ports(spec: str | None) -> list[str]:
    """Parse ``"3000:3000,8080:80"`` → ``["3000:3000", "8080:80"]`` (validated).

    Only ``<digits>:<digits>`` (each 1..65535) is accepted — the regex admits no
    shell metacharacters, so a hostile spec can never inject an argument. Raises
    :class:`SandboxError` on anything else; empty/None → ``[]``."""
    if not spec:
        return []
    out: list[str] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if not _PORT_RE.match(part):
            raise SandboxError(f"invalid port mapping: {part!r} (want HOST:CONTAINER)")
        host, cont = part.split(":")
        if not (1 <= int(host) <= 65535 and 1 <= int(cont) <= 65535):
            raise SandboxError(f"port out of range: {part!r}")
        out.append(part)
    return out


@dataclass
class SandboxInfo:
    name: str
    host_workdir: str
    ports: list[str]
    created_at: str
    image_tag: str
    state: str = "unknown"  # running | stopped | missing | unknown

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "host_workdir": self.host_workdir,
            "ports": self.ports,
            "created_at": self.created_at,
            "image_tag": self.image_tag,
        }


class SandboxManager:
    def __init__(
        self,
        abs_home: Path | None = None,
        docker_bin: str | None = None,
        sandbox_root: Path | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.abs_home = Path(abs_home) if abs_home else default_abs_home()
        self._docker = docker_bin or shutil.which("docker") or "docker"
        self.sandbox_root = Path(sandbox_root) if sandbox_root else self._configured_root()
        self.meta_path = self.abs_home / "daemon" / "sandboxes.json"
        self._timeout = timeout
        self._image_tag = IMAGE_TAG

    def _configured_root(self) -> Path:
        from absd import config as config_mod

        try:
            cfg = config_mod.load(self.abs_home / "daemon" / "config.json")
            return Path(cfg.sandbox_root).expanduser()
        except Exception:
            return Path("~/Projects/sandboxes").expanduser()

    # -- naming ---------------------------------------------------------------

    def _container(self, name: str) -> str:
        return f"{CONTAINER_PREFIX}{name}"

    def _dockerfile(self) -> Path:
        return Path(__file__).resolve().parents[1] / "docker" / "sandbox" / "Dockerfile"

    # -- subprocess -----------------------------------------------------------

    def _run(
        self, argv: list[str], check: bool = True, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        try:
            proc = subprocess.run(  # noqa: S603 - explicit argv, never shell
                argv,
                capture_output=True,
                text=True,
                timeout=timeout or self._timeout,
            )
        except FileNotFoundError as exc:
            raise SandboxError(f"docker binary not found: {self._docker!r}") from exc
        except subprocess.TimeoutExpired as exc:
            raise SandboxError(f"docker timed out: {' '.join(argv[:3])}…") from exc
        if check and proc.returncode != 0:
            raise SandboxError(
                f"docker {' '.join(argv[1:3])} failed (exit {proc.returncode}): "
                f"{proc.stderr.strip()}"
            )
        return proc

    def docker_available(self) -> bool:
        try:
            return self._run([self._docker, "version", "--format", "{{.Server.Version}}"],
                             check=False, timeout=10).returncode == 0
        except SandboxError:
            return False

    # -- pure argv builders (security shape — unit-tested) --------------------

    def build_argv(self, rebuild: bool = False) -> list[str]:
        argv = [
            self._docker, "build",
            "-t", self._image_tag,
            "--build-arg", f"UID={os.getuid()}",
            "-f", str(self._dockerfile()),
        ]
        if rebuild:
            argv.append("--no-cache")
        argv.append(str(self._dockerfile().parent))
        return argv

    def create_argv(self, name: str, host_workdir: str, port_maps: list[str]) -> list[str]:
        """The ``docker create`` argv — the security shape (5.6): non-root ``dev``,
        exactly ONE ``-v`` (the workdir), NO ``--privileged``, NO socket mount,
        ``--restart no``, only explicit ``-p`` publishes."""
        argv = [
            self._docker, "create",
            "--name", self._container(name),
            "--user", "dev",
            "--restart", "no",
            "-w", WORKDIR,
            "-v", f"{host_workdir}:{WORKDIR}",
        ]
        for pm in port_maps:
            argv += ["-p", pm]
        argv.append(self._image_tag)  # no command → image CMD keeps it alive
        return argv

    def exec_argv(self, name: str, command: list[str]) -> list[str]:
        """The ``docker exec -it`` argv to run ``command`` inside the sandbox
        (used by 3.2 to launch a claude session; defined + tested now)."""
        return [self._docker, "exec", "-it", self._container(name), *command]

    def session_exec_argv(self, name: str, launcher_args: list[str]) -> list[str]:
        """The engine PANE command for a sandbox session (3.2): ``docker exec -it``
        into the box running the in-container launcher. The docker-exec CLIENT runs
        on the HOST (in the pane) — that host process is what the engine's liveness
        sees. ``launcher_args`` is ``[<profile>, <claude flags…>]`` handed to
        :data:`SESSION_LAUNCHER`. No host mounts are added here — the only bind is
        the one from ``create`` (5.6)."""
        return [
            self._docker, "exec", "-it", self._container(name),
            SESSION_LAUNCHER, *launcher_args,
        ]

    def process_alive(self, name: str, pattern: str) -> bool:
        """Best-effort in-container liveness cross-check (3.2): ``docker exec …
        pgrep -f <pattern>``. The daemon PREFERS the engine-pane signal; this is the
        documented fallback if the pane signal ever proves flaky."""
        proc = self._run(
            [self._docker, "exec", self._container(name), "pgrep", "-f", pattern],
            check=False,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())

    # -- image ----------------------------------------------------------------

    def image_present(self) -> bool:
        proc = self._run([self._docker, "images", "-q", self._image_tag], check=False)
        return proc.returncode == 0 and bool(proc.stdout.strip())

    def build(self, rebuild: bool = False) -> bool:
        """Build the image (idempotent — skip if present unless ``rebuild``).
        Returns True if a build ran, False if skipped."""
        if self.image_present() and not rebuild:
            return False
        self._run(self.build_argv(rebuild), timeout=_BUILD_TIMEOUT)
        return True

    # -- metadata -------------------------------------------------------------

    def _read_meta(self) -> dict[str, Any]:
        try:
            data = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_meta(self, meta: dict[str, Any]) -> None:
        self.meta_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.meta_path.with_suffix(".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _MODE)
        try:
            os.write(fd, (json.dumps(meta, indent=2) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(self.meta_path))
        os.chmod(self.meta_path, _MODE)

    # -- state ----------------------------------------------------------------

    def is_created(self, name: str) -> bool:
        return self._run([self._docker, "inspect", self._container(name)], check=False).returncode == 0

    def is_running(self, name: str) -> bool:
        proc = self._run(
            [self._docker, "inspect", "-f", "{{.State.Running}}", self._container(name)],
            check=False,
        )
        return proc.returncode == 0 and proc.stdout.strip() == "true"

    def status(self, name: str) -> str:
        if not self.is_created(name):
            return "missing"
        return "running" if self.is_running(name) else "stopped"

    # -- lifecycle ------------------------------------------------------------

    def create(
        self,
        name: str,
        ports: str | None = None,
        creds_src: Path | None = None,
    ) -> SandboxInfo:
        ok, err = flow_mod.validate_folder_name(name)
        if not ok:
            raise SandboxError(f"bad sandbox name: {err}")
        if self.is_created(name):
            raise SandboxError(f"sandbox {name!r} already exists")
        if not self.image_present():
            raise SandboxError("sandbox image not built — run: abs sandbox build")
        port_maps = parse_ports(ports)

        workdir = self.sandbox_root / name
        workdir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(workdir, 0o700)
        except OSError:
            pass

        self._run(self.create_argv(name, str(workdir.resolve()), port_maps))

        # D8: COPY (not mount) the host credentials in, so they diverge and the host
        # copy is never touched. Targeting the full CRED_DIR (which does not exist in
        # the image) makes docker cp create it from the SOURCE'S CONTENTS — so the
        # copy lands at /home/dev/.claude regardless of the source dir's basename.
        # docker cp preserves uid, and `dev` matches the host uid (build arg), so the
        # copy is owned by dev.
        src = Path(creds_src).expanduser() if creds_src else (Path.home() / ".claude")
        if src.is_dir():
            self._run(
                [self._docker, "cp", str(src), f"{self._container(name)}:{CRED_DIR}"],
                check=False,
            )

        info = SandboxInfo(
            name=name,
            host_workdir=str(workdir.resolve()),
            ports=port_maps,
            created_at=_utc_now_iso(),
            image_tag=self._image_tag,
        )
        meta = self._read_meta()
        meta[name] = info.to_dict()
        self._write_meta(meta)
        return info

    def host_workdir(self, name: str) -> str | None:
        """The dedicated host folder for ``name`` (the pane cwd + start target)."""
        row = self._read_meta().get(name)
        return str(row.get("host_workdir")) if isinstance(row, dict) else None

    def start(self, name: str) -> None:
        self._run([self._docker, "start", self._container(name)])

    def ensure_running(self, name: str) -> None:
        """Start the container if it isn't already (before a session handoff)."""
        if not self.is_running(name):
            self.start(name)

    def stop(self, name: str) -> None:
        self._run([self._docker, "stop", self._container(name)], check=False)

    def destroy(self, name: str, purge: bool = False) -> str | None:
        """Remove the container + its metadata. The host workdir is USER DATA:
        kept by default (returns its path so the caller can report it); removed only
        with ``purge`` (never silently). Returns the kept workdir path, or None."""
        self._run([self._docker, "rm", "-f", self._container(name)], check=False)
        meta = self._read_meta()
        workdir = meta.get(name, {}).get("host_workdir")
        meta.pop(name, None)
        self._write_meta(meta)
        if purge and workdir:
            try:
                shutil.rmtree(workdir)
            except OSError:
                pass
            return None
        return workdir

    def list(self) -> list[SandboxInfo]:
        out: list[SandboxInfo] = []
        for name, row in sorted(self._read_meta().items()):
            if not isinstance(row, dict):
                continue
            info = SandboxInfo(
                name=name,
                host_workdir=str(row.get("host_workdir", "")),
                ports=list(row.get("ports", []) or []),
                created_at=str(row.get("created_at", "")),
                image_tag=str(row.get("image_tag", IMAGE_TAG)),
                state=self.status(name),
            )
            out.append(info)
        return out


# --------------------------------------------------------------------------- #
# CLI — `python -m absd.sandbox` (abs.sh shells into this)
# --------------------------------------------------------------------------- #

_CRED_WARNING = (
    "⚠ A COPY of your ~/.claude credentials is placed inside this sandbox so Claude\n"
    "  Code is logged in there. The copy diverges from the host (edits inside stay\n"
    "  inside); still, treat the sandbox as holding a secret. For real isolation use\n"
    "  a dedicated bot/credentials per sandbox."
)


def _cmd(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m absd.sandbox")
    parser.add_argument("--abs-home", type=Path, default=default_abs_home())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build").add_argument("--rebuild", action="store_true")
    p_c = sub.add_parser("create")
    p_c.add_argument("name")
    p_c.add_argument("--ports", default=None)
    p_c.add_argument("--creds", default=None, help="credentials source dir (default ~/.claude)")
    sub.add_parser("list")
    for verb in ("start", "stop"):
        sub.add_parser(verb).add_argument("name")
    p_d = sub.add_parser("destroy")
    p_d.add_argument("name")
    p_d.add_argument("--purge", action="store_true", help="also delete the host workdir (USER DATA)")

    args = parser.parse_args(argv)
    mgr = SandboxManager(abs_home=args.abs_home)

    try:
        if args.command == "build":
            print("Building sandbox image (first build pulls ~1GB; cached after)…")
            ran = mgr.build(rebuild=args.rebuild)
            print(f"Image {IMAGE_TAG} {'built' if ran else 'already present'}.")
            return 0
        if args.command == "create":
            info = mgr.create(args.name, ports=args.ports,
                              creds_src=Path(args.creds) if args.creds else None)
            print(f"Created sandbox '{info.name}'.")
            print(f"  workdir : {info.host_workdir}")
            if info.ports:
                print(f"  ports   : {', '.join(info.ports)}")
            print(_CRED_WARNING)
            print(f"Start it:  abs sandbox start {info.name}")
            return 0
        if args.command == "list":
            rows = mgr.list()
            if not rows:
                print("No sandboxes. Create one: abs sandbox create <name>")
                return 0
            for r in rows:
                p = f"  ports={','.join(r.ports)}" if r.ports else ""
                print(f"{r.name}\t{r.state}\t{r.host_workdir}{p}")
            return 0
        if args.command == "start":
            mgr.start(args.name)
            print(f"Started '{args.name}'. Shell in:  docker exec -it {CONTAINER_PREFIX}{args.name} bash")
            return 0
        if args.command == "stop":
            mgr.stop(args.name)
            print(f"Stopped '{args.name}'.")
            return 0
        if args.command == "destroy":
            kept = mgr.destroy(args.name, purge=args.purge)
            print(f"Destroyed sandbox '{args.name}'.")
            if kept:
                print(f"  host folder KEPT (user data): {kept}")
                print(f"  remove it too: abs sandbox destroy {args.name} --purge  (or delete by hand)")
            return 0
    except SandboxError as exc:
        import sys

        print(f"abs sandbox: {exc}", file=sys.stderr)
        return 1
    return 2


def main(argv: list[str] | None = None) -> int:
    return _cmd(argv)


if __name__ == "__main__":
    raise SystemExit(main())
