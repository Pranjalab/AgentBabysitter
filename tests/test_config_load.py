"""config.load: defaults-on-missing, validation, 0600 enforcement."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from absd.config import ConfigError, DaemonConfig, load, save, validate


def test_missing_file_returns_defaults(tmp_path: Path) -> None:
    cfg = load(tmp_path / "config.json")
    assert cfg == DaemonConfig()


def test_load_valid_file(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"engine": "tmux", "max_sessions": 2, "poll_timeout_s": 30}))
    cfg = load(p)
    assert cfg.engine == "tmux"
    assert cfg.max_sessions == 2
    assert cfg.poll_timeout_s == 30
    # Untouched fields keep defaults.
    assert cfg.workspace_root == DaemonConfig().workspace_root


def test_load_enforces_0600(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"engine": "auto"}))
    os.chmod(p, 0o644)
    load(p)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_load_unknown_keys_ignored(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"engine": "herdr", "future_thing": 99}))
    assert load(p).engine == "herdr"


@pytest.mark.parametrize(
    "payload",
    [
        {"engine": "bogus"},
        {"max_sessions": 0},
        {"max_sessions": -1},
        {"poll_timeout_s": 301},
        {"poll_timeout_s": -1},
        {"poll_stagger_s": -0.5},
        {"reclaim_grace_s": -1},
        {"reclaim_backoff_max_s": 1.0, "reclaim_grace_s": 5.0},  # cap < grace
    ],
)
def test_load_rejects_invalid(tmp_path: Path, payload: dict) -> None:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(payload))
    with pytest.raises(ConfigError):
        load(p)


def test_load_rejects_non_object(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(ConfigError):
        load(p)


def test_load_rejects_bad_json(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text("{not json")
    with pytest.raises(ConfigError):
        load(p)


def test_validate_returns_config() -> None:
    cfg = DaemonConfig(engine="tmux")
    assert validate(cfg) is cfg


def test_save_round_trip_0600(tmp_path: Path) -> None:
    p = tmp_path / "sub" / "config.json"
    cfg = DaemonConfig(engine="tmux", max_sessions=4)
    save(p, cfg)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert load(p) == cfg
