"""Pure ABS START flow helpers (absd/flow.py) — no poller, no network."""

from __future__ import annotations

from pathlib import Path

import pytest

from absd import flow


# ---- D6 folder-name jail -----------------------------------------------------


@pytest.mark.parametrize(
    "name,ok",
    [
        ("web", True),
        ("my-project_2.0", True),
        ("a", True),
        ("A1._-", True),
        ("x" * 64, True),
        ("x" * 65, False),  # over 64
        ("", False),
        ("   ", False),
        (".", False),
        ("..", False),
        ("../evil", False),  # path separator + traversal
        ("/abs/path", False),  # absolute / separator
        ("a/b", False),  # separator
        ("a\\b", False),  # backslash separator
        ("space name", False),  # space
        ("café", False),  # unicode
        ("na\x00me", False),  # NUL
        ("-ok", True),  # leading hyphen is allowed by the class
    ],
)
def test_validate_folder_name_matrix(name: str, ok: bool) -> None:
    got, msg = flow.validate_folder_name(name)
    assert got is ok
    if not ok:
        assert msg  # a rejection always explains itself


# ---- structural path jail (belt-and-suspenders over the regex) ---------------


def test_safe_join_under_root_valid(tmp_path: Path) -> None:
    target = flow.safe_join_under_root(tmp_path, "web")
    assert target == (tmp_path / "web").resolve()


def test_safe_join_under_root_rejects_escape(tmp_path: Path) -> None:
    # These never pass validate_folder_name, but the join is the second gate.
    assert flow.safe_join_under_root(tmp_path, "..") is None
    assert flow.safe_join_under_root(tmp_path, "../sibling") is None
    assert flow.safe_join_under_root(tmp_path, "a/b") is None


# ---- project enumeration -----------------------------------------------------


def test_enumerate_registered_then_children_then_newfolder(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    (root / "alpha").mkdir(parents=True)
    (root / "beta").mkdir()
    (root / ".hidden").mkdir()  # skipped
    reg_dir = tmp_path / "elsewhere" / "reg"
    reg_dir.mkdir(parents=True)

    options = flow.enumerate_project_options(
        [(str(reg_dir), "reg")], root
    )
    kinds = [(o.kind, o.label) for o in options]
    # registered first, then sorted workspace children, then the sentinel; hidden
    # dirs excluded.
    assert kinds[0] == ("project", "reg")
    labels = [o.label for o in options if o.kind == "project"]
    assert "alpha" in labels and "beta" in labels and ".hidden" not in labels
    assert options[-1].kind == "newfolder"


def test_enumerate_no_root_has_no_newfolder(tmp_path: Path) -> None:
    reg_dir = tmp_path / "reg"
    reg_dir.mkdir()
    options = flow.enumerate_project_options([(str(reg_dir), "reg")], None)
    assert [o.kind for o in options] == ["project"]  # no newfolder without a root


def test_enumerate_dedups_and_drops_missing(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    shared = root / "shared"
    shared.mkdir(parents=True)
    missing = tmp_path / "gone"
    options = flow.enumerate_project_options(
        [(str(shared), "shared"), (str(missing), "gone")], root
    )
    project_paths = [o.path for o in options if o.kind == "project"]
    # 'shared' registered AND a workspace child, but appears once; 'gone' dropped.
    assert project_paths.count(str(shared.resolve())) == 1
    assert str(missing) not in project_paths


# ---- choice parsing (callback + numbered fallback) ---------------------------


def _opts() -> list[flow.ProjectOption]:
    return [
        flow.ProjectOption(kind="project", label="one", path="/p/one"),
        flow.ProjectOption(kind="project", label="two", path="/p/two"),
        flow.ProjectOption(kind="newfolder", label="➕ New folder", path=None),
    ]


def test_choose_project_callback() -> None:
    opts = _opts()
    assert flow.choose_project("as:p:0", "", opts) is opts[0]
    assert flow.choose_project("as:p:1", "", opts) is opts[1]
    assert flow.choose_project("as:nf", "", opts) is opts[2]
    assert flow.choose_project("as:p:9", "", opts) is None  # out of range
    assert flow.choose_project("garbage", "", opts) is None


def test_choose_project_numbered_fallback() -> None:
    opts = _opts()
    assert flow.choose_project(None, "1", opts) is opts[0]
    assert flow.choose_project(None, "3", opts) is opts[2]
    assert flow.choose_project(None, "0", opts) is None
    assert flow.choose_project(None, "4", opts) is None
    assert flow.choose_project(None, "two", opts) is None  # not a number


def test_choose_mode() -> None:
    assert flow.choose_mode(flow.CB_MODE_NORMAL, "") == flow.MODE_NORMAL
    assert flow.choose_mode(flow.CB_MODE_AWAY, "") == flow.MODE_AWAY
    assert flow.choose_mode(None, "1") == flow.MODE_NORMAL
    assert flow.choose_mode(None, "2") == flow.MODE_AWAY
    assert flow.choose_mode(None, "3") is None
    assert flow.choose_mode("nope", "") is None


# ---- keyboards ---------------------------------------------------------------


def test_project_keyboard_shape() -> None:
    opts = _opts()
    kb = flow.build_project_keyboard(opts)
    rows = kb["inline_keyboard"]
    assert rows[0][0]["callback_data"] == "as:p:0"
    assert rows[2][0]["callback_data"] == flow.CB_NEWFOLDER
    # every callback data is well under Telegram's 64-byte limit
    for row in rows:
        assert len(row[0]["callback_data"].encode()) <= 64


def test_mode_keyboard_shape() -> None:
    kb = flow.build_mode_keyboard()
    datas = [b[0]["callback_data"] for b in kb["inline_keyboard"]]
    assert datas == [flow.CB_MODE_NORMAL, flow.CB_MODE_AWAY]


# ---- launcher argv (4.2) -----------------------------------------------------


def test_build_launcher_argv() -> None:
    argv = flow.build_launcher_argv("/opt/abs.sh", "work", away=False)
    assert argv == ["bash", "/opt/abs.sh", "--profile", "work", "--daemon-start"]
    argv_away = flow.build_launcher_argv("/opt/abs.sh", "work", away=True)
    assert argv_away[-1] == "--away"
