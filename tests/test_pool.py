"""Pool store: 0600 perms, jsonl round-trip, corruption tolerance, dedupe, D14."""

from __future__ import annotations

import json
import stat
from pathlib import Path

from absd.pool import Pool, PooledMessage, utc_now_iso


def _msg(update_id: int, text: str = "hi", from_id: int = 42) -> PooledMessage:
    return PooledMessage(update_id, from_id, text, utc_now_iso())


def test_append_creates_file_0600(tmp_path: Path) -> None:
    pool = Pool(tmp_path / "pool.jsonl")
    count = pool.append(_msg(1))
    assert count == 1
    mode = stat.S_IMODE(pool.path.stat().st_mode)
    assert mode == 0o600, oct(mode)


def test_append_tightens_loose_perms(tmp_path: Path) -> None:
    p = tmp_path / "pool.jsonl"
    p.write_text("")  # created with default (looser) perms
    import os

    os.chmod(p, 0o644)
    Pool(p).append(_msg(1))
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_jsonl_round_trip(tmp_path: Path) -> None:
    pool = Pool(tmp_path / "pool.jsonl")
    pool.append(_msg(1, "first"))
    pool.append(_msg(2, "second"))
    recs = pool.read_all()
    assert [r.update_id for r in recs] == [1, 2]
    assert [r.text for r in recs] == ["first", "second"]
    # Each line is a standalone JSON object with the stable field set.
    lines = pool.path.read_text().strip().splitlines()
    assert len(lines) == 2
    obj = json.loads(lines[0])
    assert set(obj) == {"update_id", "from_id", "text", "received_at", "forwarded_at"}
    assert obj["forwarded_at"] is None


def test_corrupted_line_tolerated(tmp_path: Path) -> None:
    p = tmp_path / "pool.jsonl"
    pool = Pool(p)
    pool.append(_msg(1, "good"))
    # Simulate a torn/garbage final line (e.g. crash mid-write) + a valid one.
    with p.open("a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")
    pool.append(_msg(2, "also good"))
    recs = pool.read_all()
    assert [r.update_id for r in recs] == [1, 2]  # garbage skipped, none lost
    assert pool.last_read_skipped == 1


def test_missing_field_line_skipped(tmp_path: Path) -> None:
    p = tmp_path / "pool.jsonl"
    p.write_text(json.dumps({"update_id": 1}) + "\n")  # missing required fields
    pool = Pool(p)
    assert pool.read_all() == []
    assert pool.last_read_skipped == 1


def test_existing_update_ids_for_dedupe(tmp_path: Path) -> None:
    pool = Pool(tmp_path / "pool.jsonl")
    pool.append(_msg(5))
    pool.append(_msg(7))
    assert pool.existing_update_ids() == {5, 7}


def test_read_missing_is_empty(tmp_path: Path) -> None:
    pool = Pool(tmp_path / "nope.jsonl")
    assert pool.read_all() == []
    assert pool.count() == 0
    assert pool.existing_update_ids() == set()


def test_mark_forwarded_keeps_records_d14(tmp_path: Path) -> None:
    pool = Pool(tmp_path / "pool.jsonl")
    pool.append(_msg(1))
    pool.append(_msg(2))
    pool.mark_forwarded([1], forwarded_at="2026-07-23T10:00:00Z")
    recs = pool.read_all()
    assert len(recs) == 2  # nothing dropped (D14)
    by_id = {r.update_id: r for r in recs}
    assert by_id[1].forwarded_at == "2026-07-23T10:00:00Z"
    assert by_id[2].forwarded_at is None
    assert pool.unforwarded() == [by_id[2]]
    assert stat.S_IMODE(pool.path.stat().st_mode) == 0o600


def test_clear(tmp_path: Path) -> None:
    pool = Pool(tmp_path / "pool.jsonl")
    pool.append(_msg(1))
    pool.clear()
    assert pool.read_all() == []
    assert not pool.path.exists()


def test_timestamp_is_iso_utc() -> None:
    ts = utc_now_iso()
    assert ts.endswith("Z")
    # Parseable ISO-8601.
    from datetime import datetime

    datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
