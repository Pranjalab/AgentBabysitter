"""Project registry + workspace-root CLI (absd/registry.py)."""

from __future__ import annotations

import json
import stat
from pathlib import Path

from absd import config as config_mod
from absd.registry import Registry, main


def _reg(abs_home: Path) -> Registry:
    return Registry(abs_home / "daemon" / "registry.json")


def test_add_list_rm(tmp_path: Path) -> None:
    abs_home = tmp_path / "abs"
    (abs_home / "daemon").mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    reg = _reg(abs_home)

    changed, _ = reg.add(proj)
    assert changed
    entries = reg.read()
    assert len(entries) == 1
    assert entries[0].path == str(proj.resolve())
    assert entries[0].label == "proj"
    assert entries[0].added_at  # timestamped

    # idempotent: adding again does not duplicate
    changed2, msg2 = reg.add(proj)
    assert not changed2 and "already" in msg2
    assert len(reg.read()) == 1

    removed, _ = reg.remove(proj)
    assert removed
    assert reg.read() == []
    # removing again is a tolerant no-op
    removed2, _ = reg.remove(proj)
    assert not removed2


def test_add_rejects_non_directory(tmp_path: Path) -> None:
    abs_home = tmp_path / "abs"
    (abs_home / "daemon").mkdir(parents=True)
    reg = _reg(abs_home)
    changed, msg = reg.add(tmp_path / "does-not-exist")
    assert not changed and "not a directory" in msg
    assert not (abs_home / "daemon" / "registry.json").exists()


def test_registry_file_is_0600(tmp_path: Path) -> None:
    abs_home = tmp_path / "abs"
    (abs_home / "daemon").mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    reg = _reg(abs_home)
    reg.add(proj)
    mode = stat.S_IMODE((abs_home / "daemon" / "registry.json").stat().st_mode)
    assert mode == 0o600


def test_read_tolerates_garbage(tmp_path: Path) -> None:
    abs_home = tmp_path / "abs"
    (abs_home / "daemon").mkdir(parents=True)
    path = abs_home / "daemon" / "registry.json"
    path.write_text("{ not json ]")
    assert Registry(path).read() == []


# ---- CLI ---------------------------------------------------------------------


def test_cli_project_add_list_rm(tmp_path: Path, capsys) -> None:
    abs_home = tmp_path / "abs"
    (abs_home / "daemon").mkdir(parents=True)
    proj = tmp_path / "web"
    proj.mkdir()

    rc = main(["--abs-home", str(abs_home), "project", "add", str(proj)])
    assert rc == 0

    capsys.readouterr()
    rc = main(["--abs-home", str(abs_home), "project", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert str(proj.resolve()) in out

    rc = main(["--abs-home", str(abs_home), "project", "rm", str(proj)])
    assert rc == 0
    rc = main(["--abs-home", str(abs_home), "project", "list"])
    out = capsys.readouterr().out
    assert "No registered projects" in out


def test_cli_workspace_root_set_and_show(tmp_path: Path, capsys) -> None:
    abs_home = tmp_path / "abs"
    (abs_home / "daemon").mkdir(parents=True)
    ws = tmp_path / "Projects"
    ws.mkdir()

    rc = main(["--abs-home", str(abs_home), "workspace-root", str(ws)])
    assert rc == 0

    cfg = config_mod.load(abs_home / "daemon" / "config.json")
    assert cfg.workspace_root == str(ws.resolve())

    capsys.readouterr()
    rc = main(["--abs-home", str(abs_home), "workspace-root", "--show"])
    assert rc == 0
    assert str(ws.resolve()) in capsys.readouterr().out


def test_cli_workspace_root_rejects_non_dir(tmp_path: Path) -> None:
    abs_home = tmp_path / "abs"
    (abs_home / "daemon").mkdir(parents=True)
    rc = main(["--abs-home", str(abs_home), "workspace-root", str(tmp_path / "nope")])
    assert rc == 1


def test_cli_workspace_root_preserves_other_config(tmp_path: Path) -> None:
    abs_home = tmp_path / "abs"
    (abs_home / "daemon").mkdir(parents=True)
    cfg_path = abs_home / "daemon" / "config.json"
    config_mod.save(cfg_path, config_mod.DaemonConfig(engine="tmux", max_sessions=5))
    ws = tmp_path / "ws"
    ws.mkdir()
    assert main(["--abs-home", str(abs_home), "workspace-root", str(ws)]) == 0
    cfg = config_mod.load(cfg_path)
    assert cfg.engine == "tmux" and cfg.max_sessions == 5  # untouched
    assert cfg.workspace_root == str(ws.resolve())


# ---- targets (registered + workspace children, for the start menus) ----------


def test_cli_targets_json(tmp_path: Path, capsys) -> None:
    abs_home = tmp_path / "abs"
    (abs_home / "daemon").mkdir(parents=True)
    # a registered project + a workspace root with two children
    reg_proj = tmp_path / "reg"
    reg_proj.mkdir()
    main(["--abs-home", str(abs_home), "project", "add", str(reg_proj)])
    ws = tmp_path / "ws"
    (ws / "alpha").mkdir(parents=True)
    (ws / "beta").mkdir()
    config_mod.save(
        abs_home / "daemon" / "config.json",
        config_mod.DaemonConfig(workspace_root=str(ws)),
    )
    capsys.readouterr()
    rc = main(["--abs-home", str(abs_home), "targets", "--json"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    labels = [r["label"] for r in rows]
    assert "reg" in labels and "alpha" in labels and "beta" in labels
    # NO new-folder sentinel in the targets list
    assert all(r.get("path") for r in rows)


def test_cli_targets_json_empty(tmp_path: Path, capsys) -> None:
    abs_home = tmp_path / "abs"
    (abs_home / "daemon").mkdir(parents=True)
    # no registry, empty workspace root → no targets
    config_mod.save(
        abs_home / "daemon" / "config.json",
        config_mod.DaemonConfig(workspace_root=""),
    )
    rc = main(["--abs-home", str(abs_home), "targets", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []
