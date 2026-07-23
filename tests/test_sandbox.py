"""Docker sandbox lifecycle (absd/sandbox.py) — unit shape + gated integration."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import uuid
from pathlib import Path

import pytest

from absd.sandbox import (
    CONTAINER_PREFIX,
    IMAGE_TAG,
    WORKDIR,
    SandboxError,
    SandboxInfo,
    SandboxManager,
    parse_ports,
)


def _mgr(tmp_path: Path) -> SandboxManager:
    abs_home = tmp_path / "abs"
    (abs_home / "daemon").mkdir(parents=True)
    return SandboxManager(abs_home=abs_home, sandbox_root=tmp_path / "sandboxes")


# ---- name jail (reuses flow.validate_folder_name) ----------------------------


@pytest.mark.parametrize(
    "name",
    ["../evil", "/etc", "a b", "..", "", "café", "a/b"],
)
def test_create_rejects_bad_names(tmp_path: Path, name: str) -> None:
    # A bad name is rejected by the SAME jail as the ABS START flow, BEFORE any
    # docker call — so this never creates a container even when the image exists.
    mgr = _mgr(tmp_path)
    with pytest.raises(SandboxError) as e:
        mgr.create(name)
    assert "bad sandbox name" in str(e.value)


def test_valid_names_pass_the_jail() -> None:
    # Valid names pass the name jail (no docker call here — create() is real docker).
    from absd import flow

    for good in ("test1", "my-box_2", "a", "x" * 64):
        ok, _ = flow.validate_folder_name(good)
        assert ok, good


# ---- ports parser ------------------------------------------------------------


def test_parse_ports_valid() -> None:
    assert parse_ports("3000:3000,8080:80") == ["3000:3000", "8080:80"]
    assert parse_ports("3000:3000") == ["3000:3000"]
    assert parse_ports("") == []
    assert parse_ports(None) == []
    assert parse_ports(" 3000:3000 , 8080:80 ") == ["3000:3000", "8080:80"]


@pytest.mark.parametrize(
    "bad",
    ["3000", "3000:", ":80", "abc:80", "3000:80;rm", "$(x):80", "3000:80 8080:80",
     "99999999:80", "0:80", "3000:0"],
)
def test_parse_ports_rejects(bad: str) -> None:
    with pytest.raises(SandboxError):
        parse_ports(bad)


# ---- argv shape (the security surface — no docker) ---------------------------


def test_create_argv_security_shape(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    argv = mgr.create_argv("box", "/host/work", ["3000:3000"])
    assert "--privileged" not in argv                       # 5.6
    assert argv.count("-v") == 1                             # exactly one mount
    assert argv[argv.index("-v") + 1] == f"/host/work:{WORKDIR}"
    assert "--user" in argv and argv[argv.index("--user") + 1] == "dev"
    assert "/var/run/docker.sock" not in " ".join(argv)     # no socket mount
    assert "--restart" in argv and argv[argv.index("--restart") + 1] == "no"
    assert argv[-1] == IMAGE_TAG                             # no command → image CMD
    assert argv[2] == "--name" and argv[3] == f"{CONTAINER_PREFIX}box"
    # ports published
    assert argv[argv.index("-p") + 1] == "3000:3000"


def test_create_argv_no_ports_no_p(tmp_path: Path) -> None:
    argv = _mgr(tmp_path).create_argv("box", "/w", [])
    assert "-p" not in argv


def test_exec_argv_shape(tmp_path: Path) -> None:
    argv = _mgr(tmp_path).exec_argv("box", ["claude", "--help"])
    assert argv[:4] == [argv[0], "exec", "-it", f"{CONTAINER_PREFIX}box"]
    assert argv[-2:] == ["claude", "--help"]


def test_session_exec_argv_shape(tmp_path: Path) -> None:
    # 3.2: docker exec -it into the box running the in-container launcher; NO host
    # mounts added here (the only bind is from create).
    argv = _mgr(tmp_path).session_exec_argv("box", ["default", "--continue", "the prompt"])
    assert argv[:4] == [argv[0], "exec", "-it", f"{CONTAINER_PREFIX}box"]
    assert argv[4] == "absd-session"
    assert argv[5:] == ["default", "--continue", "the prompt"]
    assert "-v" not in argv and "--mount" not in argv  # no new mounts


def test_build_argv_shape(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    argv = mgr.build_argv()
    assert argv[1] == "build"
    assert "-t" in argv and argv[argv.index("-t") + 1] == IMAGE_TAG
    assert any(a.startswith("UID=") for a in argv)  # host uid build arg
    assert "--no-cache" not in argv
    assert "--no-cache" in mgr.build_argv(rebuild=True)


# ---- metadata ----------------------------------------------------------------


def test_metadata_roundtrip_and_0600(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    info = SandboxInfo(
        name="box", host_workdir="/w", ports=["3000:3000"],
        created_at="2026-07-23T00:00:00Z", image_tag=IMAGE_TAG,
    )
    mgr._write_meta({"box": info.to_dict()})
    assert stat.S_IMODE(mgr.meta_path.stat().st_mode) == 0o600
    got = mgr._read_meta()
    assert got["box"]["host_workdir"] == "/w"
    assert got["box"]["ports"] == ["3000:3000"]


def test_meta_corrupt_reads_empty(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    mgr.meta_path.write_text("{ not json ]")
    assert mgr._read_meta() == {}
    assert mgr.list() == []


# ---- gated real-docker integration ------------------------------------------


def _docker_ok() -> bool:
    try:
        return subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, timeout=10,
        ).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _inspect(container: str, fmt: str) -> str:
    return subprocess.run(
        ["docker", "inspect", "-f", fmt, container],
        capture_output=True, text=True, timeout=20,
    ).stdout.strip()


@pytest.mark.skipif(not _docker_ok(), reason="docker not available")
def test_integration_create_inspect_bind_creds(tmp_path: Path) -> None:
    """Real end-to-end: build (cached), create a throwaway sandbox, assert the 5.6
    security shape via `docker inspect`, the bind-mount roundtrip, net access, and
    the credential COPY-not-mount divergence. Full teardown even on failure. Uses a
    FIXTURE creds dir — never the real ~/.claude (kept safe; production copies
    ~/.claude, and this proves the same copy/divergence mechanism)."""
    name = f"absd-test-{uuid.uuid4().hex[:8]}"
    container = f"{CONTAINER_PREFIX}{name}"
    mgr = SandboxManager(
        abs_home=(tmp_path / "abs"), sandbox_root=(tmp_path / "sandboxes"),
    )
    (tmp_path / "abs" / "daemon").mkdir(parents=True)
    # fixture credentials (NOT the real ~/.claude)
    fake_claude = tmp_path / "fake-claude"
    fake_claude.mkdir()
    (fake_claude / ".credentials.json").write_text('{"token":"FAKE-SANDBOX-TEST"}')

    try:
        mgr.build()  # cached after the first run
        assert mgr.image_present()
        mgr.create(name, ports=None, creds_src=fake_claude)

        # --- 5.6 security shape via docker inspect ---
        assert _inspect(container, "{{.HostConfig.Privileged}}") == "false"
        assert _inspect(container, "{{.Config.User}}") == "dev"
        binds = json.loads(_inspect(container, "{{json .HostConfig.Binds}}"))
        workdir = str((tmp_path / "sandboxes" / name).resolve())
        assert binds == [f"{workdir}:{WORKDIR}"]  # exactly the one workdir

        mgr.start(name)
        assert mgr.is_running(name)

        # --- bind roundtrip (host <-> container) ---
        (Path(workdir) / "from_host.txt").write_text("hello-from-host")
        seen = subprocess.run(
            ["docker", "exec", container, "cat", f"{WORKDIR}/from_host.txt"],
            capture_output=True, text=True, timeout=20,
        )
        assert seen.stdout.strip() == "hello-from-host"
        subprocess.run(
            ["docker", "exec", container, "sh", "-c", f"echo hi > {WORKDIR}/from_container.txt"],
            capture_output=True, timeout=20,
        )
        assert (Path(workdir) / "from_container.txt").read_text().strip() == "hi"

        # --- credentials: exist inside, owned by dev, and a COPY (divergence) ---
        owner = subprocess.run(
            ["docker", "exec", container, "stat", "-c", "%U", "/home/dev/.claude/.credentials.json"],
            capture_output=True, text=True, timeout=20,
        )
        assert owner.returncode == 0, owner.stderr
        assert owner.stdout.strip() == "dev"
        # modify INSIDE → the fixture host copy is unchanged (proof of copy-not-mount)
        subprocess.run(
            ["docker", "exec", container, "sh", "-c",
             "echo MUTATED > /home/dev/.claude/.credentials.json"],
            capture_output=True, timeout=20,
        )
        assert (fake_claude / ".credentials.json").read_text() == '{"token":"FAKE-SANDBOX-TEST"}'

        # --- net access (default bridge) ---
        net = subprocess.run(
            ["docker", "exec", container, "curl", "-sI", "--max-time", "15", "https://example.com"],
            capture_output=True, text=True, timeout=25,
        )
        assert "HTTP" in net.stdout, f"no net status line: {net.stdout!r} {net.stderr!r}"
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=30)
