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
#: added bun + the `absd-session` launcher; v3 (Stage 3) bakes in the restricted
#: system prompt (``/usr/local/share/absd/restricted-prompt.txt``) and extends
#: `absd-session` to forward ``--model`` / ``--append-system-prompt`` / select the
#: restricted prompt for ``--restricted``. An old box lacks the bundled prompt, so a
#: rebuild is required for RESTRICTED sessions. v4 puts **ABS itself** in the box
#: (``/opt/abs`` + its venv + ``abs`` on PATH) so an in-box session is a real ABS
#: session — status bar, Bash guard, ABS remote controls, ``abs exit``. Migration:
#: `abs sandbox build --rebuild` then re-create sandboxes to pick up v4; a v3 box
#: still runs, it just falls back to the old bare-claude launch.
IMAGE_TAG = "absd-sandbox:v4"
#: Container name = this prefix + the sandbox name.
CONTAINER_PREFIX = "absd-sbx-"
#: The single bind-mount target inside the container.
WORKDIR = "/home/dev/workspace"
#: The container user's home. The copied credentials are re-homed onto this prefix
#: (see ``_rehome_plugin_metadata``) because host-absolute paths are unresolvable
#: inside the box.
BOX_HOME = "/home/dev"
#: Where copied credentials land inside the container (D8).
CRED_DIR = f"{BOX_HOME}/.claude"
#: Plugin metadata that records an absolute ``installLocation`` under the host home.
#: Left unrewritten, the marketplace fails to load in the box ("cache-miss") and a
#: sandbox session starts with no Telegram channel at all.
PLUGIN_META_FILES = ("known_marketplaces.json", "installed_plugins.json")
#: The in-container launcher (baked into the image) that runs claude (3.2).
SESSION_LAUNCHER = "absd-session"
#: Where ABS itself is synced inside the box (v4). The image creates the skeleton
#: and the venv; the CODE is copied in at start / before each session so editing
#: abs.sh on the host never needs an image rebuild.
BOX_ABS_DIR = "/opt/abs"
#: Repo entries copied into :data:`BOX_ABS_DIR`. Files land as-is; a directory is
#: copied CONTENTS-first (``src/.`` → ``dest``), which is idempotent — plain
#: ``docker cp src dest`` would nest ``dest/src`` on the second run.
BOX_ABS_FILES = ("abs.sh", "VERSION")
BOX_ABS_DIRS = ("absd", "agent_babysitter")
#: The in-box launcher, refreshed alongside the code so a box created from an older
#: image still picks up launcher fixes without a rebuild.
BOX_LAUNCHER_PATH = f"/usr/local/bin/{SESSION_LAUNCHER}"
#: ABS's own home inside the box: profile state (``profiles/<p>/rc.json``,
#: ``session.pid``, ``hooks.json``) that abs.sh reads and writes there.
BOX_ABS_HOME = f"{BOX_HOME}/.abs"

_MODE = 0o600
_DEFAULT_TIMEOUT = 30.0
#: `claude auth status` spawns node inside the container — slower than the plain
#: `test`/`pgrep` probes. Timing out is harmless here: it yields "unknown".
_LOGIN_PROBE_TIMEOUT = 45.0
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
        # `claude auth status` starts a node process, so it is slower than the
        # `test -s`/`pgrep` probes the default covers. Generous, because the
        # penalty for timing out is a None ("unknown") that fails open.
        self._login_probe_timeout = max(timeout, _LOGIN_PROBE_TIMEOUT)
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

    def creds_present(self, name: str) -> bool:
        """True if Claude Code is logged in INSIDE the box — the credentials file
        ``/home/dev/.claude/.credentials.json`` exists and is non-empty (Stage 3
        restricted keep-alive: gate a relaunch on the box being logged in, so the
        daemon doesn't relaunch-loop a not-logged-in session). Presence + size only
        via ``test -s`` — the file is NEVER read, so no credential leaves the box.
        A stopped/missing container (or docker error) reads as 'not logged in'."""
        proc = self._run(
            [
                self._docker, "exec", self._container(name),
                "test", "-s", f"{CRED_DIR}/.credentials.json",
            ],
            check=False,
        )
        return proc.returncode == 0

    def login_ok(self, name: str) -> bool | None:
        """Is Claude Code actually AUTHENTICATED inside the box?

        ``creds_present`` only proves the credentials file exists and is
        non-empty, and that is not the same question. A copied
        ``.credentials.json`` can be present, well-formed and still fail to
        authenticate — it is a frozen snapshot with an expiry, and the host
        keeps refreshing while the copy does not. When that happens the box
        starts, the Telegram plugin polls, the operator's message is delivered
        — and nothing ever answers. Silent, which is the worst failure we have.

        ``claude auth status`` reports it as JSON, so this is a real answer and
        costs no inference. Only the ``loggedIn`` flag is read; the account
        fields in that payload are ignored and never logged.

        Returns True or False, or **None when the probe could not run** — docker
        error, timeout, or output we cannot parse. None means *unknown*, and
        callers must fail open: a probe we were unable to run is not evidence of
        a broken login, and refusing to launch a working session would be a
        worse bug than the one this exists to catch.
        """
        try:
            proc = self._run(
                [
                    self._docker, "exec", self._container(name),
                    "claude", "auth", "status",
                ],
                check=False,
                timeout=self._login_probe_timeout,
            )
        except SandboxError:
            return None
        if proc.returncode != 0 and not proc.stdout.strip():
            return None
        # The payload may be wrapped in surrounding chatter; take the JSON object.
        text = proc.stdout
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except (ValueError, TypeError):
            return None
        value = data.get("loggedIn")
        return bool(value) if isinstance(value, bool) else None

    def kill_session(self, name: str, profile: str) -> bool:
        """Kill the ABS session running INSIDE the box. Best-effort; never raises.

        Killing the host-side ``docker exec`` client does **not** kill what it
        started in the container — verified directly, not assumed. So a sandbox
        session the daemon has given up on keeps running in there, and its
        Telegram plugin keeps polling. Telegram allows one ``getUpdates``
        consumer per bot token, so that orphan then splits every incoming update
        with whatever is started next: roughly half the operator's messages
        vanish, and nothing logs an error anywhere. That is the failure this
        exists to prevent.

        Prefers the in-box ``session.pid`` abs.sh writes (v4). Falls back to
        matching the launched command lines, for a pre-v4 box or a session that
        died before writing the file.

        Returns True if the box reported no surviving session afterwards.
        """
        # `profile` is interpolated into a shell string below. Nothing today can
        # get a metacharacter into a profile name — abs.sh enforces this same
        # character set before creating one, and no Telegram path creates
        # profiles at all — but a name is a name, and this is one grep away from
        # being wrong later.
        # Returning False is enough: the caller in daemon.py already warns that
        # the in-box session may have survived, which is exactly what happened.
        if not re.fullmatch(r"[A-Za-z0-9_-]+", profile):
            return False
        pid_file = f"{BOX_ABS_HOME}/profiles/{profile}/session.pid"
        # The bracket in `--channel[s]` stops the pattern matching THIS shell's
        # own command line — otherwise pkill kills the killer.
        # TERM, then wait, then KILL. The first version sent TERM and immediately
        # asked whether anything survived — which both reported healthy shutdowns
        # as survivors and, worse, let a claude that ignores TERM live on. A
        # survivor here is the orphan poller this whole method exists to prevent,
        # so it does not get to decline.
        script = (
            f'p="{pid_file}"; '
            '[ -f "$p" ] && kill -TERM "$(cat "$p" 2>/dev/null)" 2>/dev/null; '
            "pkill -TERM -f 'claude --channel[s]' 2>/dev/null; "
            "pkill -TERM -f 'claude-plugins-official/telegra[m]' 2>/dev/null; "
            "for _ in 1 2 3 4 5; do "
            "  pgrep -f 'claude --channel[s]' >/dev/null 2>&1 || break; "
            "  sleep 1; "
            "done; "
            "pkill -KILL -f 'claude --channel[s]' 2>/dev/null; "
            "pkill -KILL -f 'claude-plugins-official/telegra[m]' 2>/dev/null; "
            'rm -f "$p" 2>/dev/null; '
            "exit 0"
        )
        try:
            self._run(
                [self._docker, "exec", self._container(name), "sh", "-c", script],
                check=False,
            )
        except SandboxError:
            return False
        # Did it actually work? Asserting the kill without checking is how the
        # original bug survived this long.
        try:
            proc = self._run(
                [
                    self._docker, "exec", self._container(name),
                    "pgrep", "-f", "claude --channel[s]",
                ],
                check=False,
            )
        except SandboxError:
            return False
        return not proc.stdout.strip()

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
        no_creds: bool = False,
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
        #
        # Stage 3 — ``no_creds``: a RESTRICTED sandbox gets NO host credentials. The
        # box has no ``/home/dev/.claude`` at all, so it can never expose the host
        # bot token, and Claude Code logs in SEPARATELY inside it (`abs restricted
        # login`). This is the real containment layer for the restricted assistant.
        if not no_creds:
            src = Path(creds_src).expanduser() if creds_src else (Path.home() / ".claude")
            if src.is_dir():
                self._copy_credentials(name, src)

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

    def _copy_credentials(self, name: str, src: Path) -> None:
        """Copy a working, self-consistent Claude Code login into the box (D8).

        A naive ``docker cp ~/.claude`` is not enough, and here is why each extra
        step exists — all learned from a live sandbox that booted broken:

        1. **The dir itself** → ``/home/dev/.claude`` (credentials + ``channels/``
           for the in-box Telegram poller + the installed ``plugins/``). Kept.
        2. **``~/.claude.json``** → ``/home/dev/.claude.json``. Claude Code's MAIN
           config lives at the HOME ROOT, a sibling of the ``.claude`` dir — so the
           dir copy never includes it. Without it the box prints "configuration
           file not found" and re-runs first-run onboarding. It holds account
           metadata, not the auth token (that is in ``.credentials.json``).
        3. **Strip host-only hooks** from the copied ``settings*.json``. The host
           wires hooks at absolute host paths (e.g. ``…/.ccgram/*.js``) that do not
           exist in the container, so every hook fires "Cannot find module". We drop
           the ``hooks`` block and re-copy the file; everything else is preserved.
        4. **Re-home the plugin metadata.** ``plugins/known_marketplaces.json`` and
           ``plugins/installed_plugins.json`` record an absolute ``installLocation``
           under the HOST home (``/home/pranjal/.claude/plugins/…``). That path does
           not exist in the box (home is ``/home/dev``), so the marketplace fails to
           load with **"cache-miss"**, ``plugin:telegram@claude-plugins-official``
           cannot be resolved, and ``claude --channels …`` starts with NO Telegram
           channel — the box runs but is deaf: no status line, no messages, ever.
           Rewriting the host home prefix to ``/home/dev`` fixes resolution.
        Stale runtime state (``channels/*/bot.pid``, ``channels/*/inbox``) is NOT
        handled here: removing files inside the box needs ``docker exec``, which only
        works on a RUNNING container, and ``create`` deliberately leaves it stopped.
        That cleanup rides on :meth:`start` instead — see
        :meth:`_clear_stale_runtime_state`.

        All copies are best-effort (``check=False``): a partial login still lets the
        operator finish ``claude`` login by hand inside the box.
        """
        container = self._container(name)
        # 1. The credential directory (creds, channels, plugins, settings…).
        self._run([self._docker, "cp", str(src), f"{container}:{CRED_DIR}"], check=False)
        # 2. The HOME-root main config, which sits BESIDE the .claude dir.
        self._copy_home_config(container, src.parent / ".claude.json")
        # 3. Neutralise host-only hooks in each settings file we copied in.
        for base in ("settings.json", "settings.local.json"):
            self._strip_hooks_into_box(container, src / base, f"{CRED_DIR}/{base}")
        # 4. Make the plugin marketplace resolvable inside the box (see docstring).
        self._rehome_plugin_metadata(container, src)

    # -- credential-copy helpers ----------------------------------------------

    def _cp_text(self, container: str, text: str, box_path: str) -> None:
        """Write ``text`` to a temp file and ``docker cp`` it to ``box_path``.

        Shared by the sanitising steps: they all rewrite a JSON file host-side and
        drop the result over the copy inside the box. Best-effort by design."""
        import tempfile

        fd, tmp = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            self._run([self._docker, "cp", tmp, f"{container}:{box_path}"], check=False)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _copy_home_config(self, container: str, home_json: Path) -> None:
        """Copy ``~/.claude.json`` in, with the host's project map replaced by a single
        PRE-TRUSTED entry for the box workspace.

        Two things are going on:

        - The file is **required**: without it Claude Code reports "configuration file
          not found" and re-runs first-run onboarding. It lives at the HOME ROOT, so
          the ``.claude`` dir copy never includes it.
        - Its ``projects`` map is host paths and host per-project state — meaningless
          in the box, and needless host detail to hand a sandbox. Dropped.
        - In its place we mark :data:`WORKDIR` as trusted. Claude Code otherwise opens
          with *"Quick safety check: Is this a project you created or one you trust?"*
          and **blocks on that prompt forever** — which is a deadlock for a
          daemon-launched session: nobody is at a keyboard to answer, so the Telegram
          channel never starts and the box sits there deaf (live-test finding; this is
          what made a sandbox session look alive but never receive a message).

          Pre-trusting is sound here: the container IS the trust boundary — its only
          host mount is the operator's own dedicated sandbox folder, and launching a
          session in it is an explicit operator action. Only the trust flags are set;
          no other approval is granted on the operator's behalf.

        If the JSON can't be parsed we copy it verbatim rather than skip it: a working
        login matters more than the tidy-up."""
        if not home_json.is_file():
            return
        try:
            data = json.loads(home_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._run(
                [self._docker, "cp", str(home_json), f"{container}:{BOX_HOME}/.claude.json"],
                check=False,
            )
            return
        if isinstance(data, dict):
            data["projects"] = {
                WORKDIR: {
                    "hasTrustDialogAccepted": True,
                    "projectOnboardingSeenCount": 1,
                }
            }
        self._cp_text(container, json.dumps(data, indent=2), f"{BOX_HOME}/.claude.json")

    def _strip_hooks_into_box(self, container: str, host_settings: Path, box_path: str) -> None:
        """If ``host_settings`` has a ``hooks`` block, copy a hooks-free version over
        the box's copy at ``box_path``. No-op if the file is absent, unparseable, or
        has no hooks (so a clean settings file is left exactly as copied)."""
        if not host_settings.is_file():
            return
        try:
            data = json.loads(host_settings.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or "hooks" not in data:
            return
        data.pop("hooks", None)
        self._cp_text(container, json.dumps(data, indent=2), box_path)

    def _rehome_plugin_metadata(self, container: str, src: Path) -> None:
        """Rewrite the HOST home prefix to :data:`BOX_HOME` in the plugin metadata.

        Without this the marketplace is unresolvable in the box ("cache-miss") and a
        sandbox session silently runs with no Telegram channel — see step 4 of
        :meth:`_copy_credentials`. Only the recorded paths change; the JSON shape is
        untouched, and a rewrite that would not re-parse is discarded (we would
        rather leave the original than install a corrupt file)."""
        host_home = str(src.parent)
        if not host_home or host_home == BOX_HOME:
            return
        for base in PLUGIN_META_FILES:
            host_file = src / "plugins" / base
            if not host_file.is_file():
                continue
            try:
                text = host_file.read_text(encoding="utf-8")
            except OSError:
                continue
            if host_home not in text:
                continue
            fixed = text.replace(host_home, BOX_HOME)
            try:
                json.loads(fixed)
            except ValueError:
                continue
            self._cp_text(container, fixed, f"{CRED_DIR}/plugins/{base}")

    def _clear_stale_runtime_state(self, container: str) -> None:
        """Delete runtime leftovers under ``channels/``: ``bot.pid`` (a HOST pid,
        meaningless in the box's PID namespace) and any pending ``inbox`` files (host
        messages that would otherwise be replayed inside the box).

        Called from :meth:`start` — NOT from ``create`` — because ``docker exec`` only
        works on a running container, and ``create`` leaves the box stopped. Running it
        at start also keeps it correct for a box whose previous session died and left
        its own leftovers behind. Never runs against an already-running box, so a LIVE
        session's own ``bot.pid`` is left alone.

        ``find -delete`` with an explicit argv — precise, no shell, and idempotent."""
        channels = f"{CRED_DIR}/channels"
        self._run(
            [self._docker, "exec", container, "find", channels, "-name", "bot.pid", "-delete"],
            check=False,
        )
        self._run(
            [
                self._docker, "exec", container, "find", channels,
                "-path", "*/inbox/*", "-type", "f", "-delete",
            ],
            check=False,
        )

    # -- ABS inside the box (v4) ----------------------------------------------

    def _repo_root(self) -> Path:
        """The ABS checkout this daemon is running from — the source of the code
        synced into a box."""
        return Path(__file__).resolve().parents[1]

    def _box_has_abs_skeleton(self, container: str) -> bool:
        """True if the box was built from a v4+ image (``/opt/abs`` exists).

        A pre-v4 box has no skeleton and no venv; copying code into it would create
        a half-installed tree that ``abs`` could not run anyway, so the sync is
        skipped and the launcher keeps its bare-claude fallback."""
        proc = self._run(
            [self._docker, "exec", container, "test", "-d", BOX_ABS_DIR], check=False
        )
        return proc.returncode == 0

    def _install_abs_into_box(self, name: str) -> bool:
        """Sync abs.sh + the Python packages + the in-box launcher into a RUNNING box.

        This is what makes the sandbox a real ABS environment rather than a bare
        claude shell: with abs.sh present, :data:`SESSION_LAUNCHER` runs the session
        through it, so the box gets the status bar, the Bash guard, the ABS remote
        controls (``ABS STOP`` / ``ABS EXIT`` / ``ABS MUTE``) and a ``session.pid``
        that ``abs exit`` can signal — none of which a bare ``claude`` has.

        Copied at container START and again before every session (rather than baked
        into the image) so the box always runs the same launcher as the host, and a
        one-line abs.sh fix never needs a 4-minute rebuild. Returns False if the box
        predates v4 (no skeleton) — the caller carries on; the launcher falls back.

        Best-effort per file (``check=False``): a sandbox that is merely missing the
        in-box ``abs`` is far better than a handoff that refuses to launch."""
        container = self._container(name)
        if not self._box_has_abs_skeleton(container):
            return False
        root = self._repo_root()
        for base in BOX_ABS_FILES:
            src = root / base
            if src.is_file():
                self._run(
                    [self._docker, "cp", str(src), f"{container}:{BOX_ABS_DIR}/{base}"],
                    check=False,
                )
        for base in BOX_ABS_DIRS:
            src = root / base
            if src.is_dir():
                # `src/.` copies the CONTENTS into an existing dest — idempotent.
                # Plain `cp src dest` would nest dest/src on the second sync.
                self._run(
                    [self._docker, "cp", f"{src}{os.sep}.", f"{container}:{BOX_ABS_DIR}/{base}"],
                    check=False,
                )
        # The launcher lives on PATH, not in /opt/abs, and predates this sync — ship
        # it too so launcher fixes reach a box without an image rebuild.
        launcher = root / "docker" / "sandbox" / SESSION_LAUNCHER
        if launcher.is_file():
            self._run(
                [self._docker, "cp", str(launcher), f"{container}:{BOX_LAUNCHER_PATH}"],
                check=False,
            )
            self._run(
                [self._docker, "exec", "--user", "root", container,
                 "chmod", "0755", BOX_LAUNCHER_PATH],
                check=False,
            )
        self._run(
            [self._docker, "exec", "--user", "root", container,
             "chmod", "0755", f"{BOX_ABS_DIR}/abs.sh"],
            check=False,
        )
        return True

    def _seed_profile_state(self, name: str, profile: str) -> None:
        """Copy the profile's ``rc.json`` into the box so abs.sh sees it as PAIRED.

        ``cmd_run`` refuses to launch a daemon-started session for an unpaired
        profile — and it decides that from ``$ABS_HOME/profiles/<p>/rc.json``, which
        lives under ``~/.abs`` and is therefore NOT part of the ``~/.claude``
        credential copy. Without this the in-box session would die with "profile is
        not paired" the moment we route it through abs.sh.

        Two fields are deliberately dropped:

        - ``tg_dir`` — a HOST absolute path (``/home/pranjal/.claude/channels/…``).
          Same bug class as the plugin metadata: unresolvable in the box. The
          launcher exports ``TELEGRAM_STATE_DIR`` explicitly and ``use_profile``
          prefers that, but leaving a host path in the file would still be a trap
          for any code that reads it directly.
        - ``voice_sample`` — a host path to a recording the box cannot see.

        The file carries a chat id and the bot's @name, not the token (the token is
        in ``channels/…/.env``, already copied). Best-effort: a failure here surfaces
        as the launcher's own clear "not paired" error, not a silent deaf box."""
        rc = self.abs_home / "profiles" / profile / "rc.json"
        if not rc.is_file():
            return
        try:
            data = json.loads(rc.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        for host_only in ("tg_dir", "voice_sample"):
            data.pop(host_only, None)
        container = self._container(name)
        box_dir = f"{BOX_ABS_HOME}/profiles/{profile}"
        self._run([self._docker, "exec", container, "mkdir", "-p", box_dir], check=False)
        self._cp_text(container, json.dumps(data, indent=2), f"{box_dir}/rc.json")

    def prepare_session(self, name: str, profile: str) -> None:
        """Get a RUNNING box ready to host a session for ``profile``.

        Called from both launch paths (the daemon's handoff and the terminal's
        ``abs start sandbox``) immediately after :meth:`ensure_running`, so the box
        is guaranteed up — ``docker exec`` needs that, and putting this in ``create``
        is exactly the mistake that made an earlier fix fail silently.

        Idempotent and best-effort by design: it re-syncs the ABS code (so a box that
        has been up for days runs the current launcher) and seeds the profile state."""
        if self._install_abs_into_box(name):
            self._seed_profile_state(name, profile)

    def host_workdir(self, name: str) -> str | None:
        """The dedicated host folder for ``name`` (the pane cwd + start target)."""
        row = self._read_meta().get(name)
        return str(row.get("host_workdir")) if isinstance(row, dict) else None

    def start(self, name: str) -> None:
        """Start the container, then clear runtime leftovers and sync ABS into it.

        The cleanup lives here (not in ``create``) because it needs ``docker exec``,
        which requires a running container. Anything that starts a box — the CLI,
        ``ensure_running`` before a handoff — therefore gets a box with no stale
        ``bot.pid``/``inbox`` to confuse the in-box Telegram poller.

        The ABS sync rides along for the same reason, and so that ``docker exec -it
        <box> bash`` has a working ``abs`` even when no session was ever launched —
        "abs: command not found" in the box was a real operator complaint."""
        self._run([self._docker, "start", self._container(name)])
        self._clear_stale_runtime_state(self._container(name))
        self._install_abs_into_box(name)

    def ensure_running(self, name: str) -> None:
        """Start the container if it isn't already (before a session handoff).

        A box that is ALREADY running is left completely untouched — it may be hosting
        a live session whose own ``bot.pid`` must not be deleted."""
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
    p_c.add_argument(
        "--no-creds",
        action="store_true",
        help="do NOT copy host credentials in (restricted box logs in separately)",
    )
    sub.add_parser("list")
    for verb in ("start", "stop"):
        sub.add_parser(verb).add_argument("name")
    p_d = sub.add_parser("destroy")
    p_d.add_argument("name")
    p_d.add_argument("--purge", action="store_true", help="also delete the host workdir (USER DATA)")
    # v4: sync ABS + the profile pairing into a running box, right before a session
    # launches. abs.sh's terminal sandbox path shells into this; the daemon calls
    # SandboxManager.prepare_session directly.
    p_p = sub.add_parser("prepare")
    p_p.add_argument("name")
    p_p.add_argument("--profile", default="default")

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
                              creds_src=Path(args.creds) if args.creds else None,
                              no_creds=args.no_creds)
            print(f"Created sandbox '{info.name}'.")
            print(f"  workdir : {info.host_workdir}")
            if info.ports:
                print(f"  ports   : {', '.join(info.ports)}")
            if args.no_creds:
                print(
                    "  no host credentials copied — Claude Code logs in separately "
                    "inside this box."
                )
            else:
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
        if args.command == "prepare":
            mgr.ensure_running(args.name)
            mgr.prepare_session(args.name, args.profile)
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
