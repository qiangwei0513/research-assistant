from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from research_assistant.errors import PersistenceError
from research_assistant.runtime.persistence import write_bytes_verified, write_text_verified


def test_write_bytes_creates_missing_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "data" / "队伍 A+1" / "A-01.json"
    write_bytes_verified(target, b'{"case_id": "A-01"}')
    assert target.read_bytes() == b'{"case_id": "A-01"}'


def test_write_bytes_returns_the_exact_bytes_written(tmp_path: Path) -> None:
    data = '{"case_id": "A-01", "answer": "光照"}'.encode()
    returned = write_bytes_verified(tmp_path / "A-01.json", data)
    assert returned == data


def test_write_is_atomic_and_leaves_no_temp_file(tmp_path: Path) -> None:
    target = tmp_path / "A-01.json"
    write_bytes_verified(target, b"first")
    write_bytes_verified(target, b"second")
    assert target.read_bytes() == b"second"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["A-01.json"]


def test_existing_content_is_not_destroyed_when_write_fails(tmp_path: Path) -> None:
    target = tmp_path / "A-01.json"
    write_bytes_verified(target, b"good")
    blocker = tmp_path / "A-02.json"
    blocker.write_text("x", encoding="utf-8")
    blocker.chmod(0o444)
    os.chmod(tmp_path, 0o555)
    try:
        with pytest.raises(PersistenceError):
            write_bytes_verified(tmp_path / "new" / "A-03.json", b"data")
    finally:
        os.chmod(tmp_path, 0o755)
        blocker.chmod(0o644)
    assert target.read_bytes() == b"good"


def test_readback_mismatch_is_reported_as_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_read = Path.read_bytes

    def corrupted(self: Path, *args: object, **kwargs: object) -> bytes:
        return b"tampered" if self.name == "A-01.json" else real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", corrupted)
    with pytest.raises(PersistenceError, match="readback"):
        write_bytes_verified(tmp_path / "A-01.json", b"original")


def test_write_text_round_trips_utf8_verbatim(tmp_path: Path) -> None:
    body = "# 综述\n\n光照强度与光合速率正相关[1]。\n" * 100
    returned = write_text_verified(tmp_path / "C-01-综述-队.md", body)
    assert returned == body.encode("utf-8")
    assert (tmp_path / "C-01-综述-队.md").read_text(encoding="utf-8") == body


def test_write_text_rejects_non_string(tmp_path: Path) -> None:
    with pytest.raises(PersistenceError):
        write_text_verified(tmp_path / "x.md", 123)  # type: ignore[arg-type]


def test_unwritable_target_raises_persistence_error(tmp_path: Path) -> None:
    blocked = tmp_path / "readonly"
    blocked.mkdir()
    blocked.chmod(0o555)
    try:
        with pytest.raises(PersistenceError):
            write_bytes_verified(blocked / "A-01.json", b"data")
    finally:
        blocked.chmod(0o755)


def test_written_json_is_parseable(tmp_path: Path) -> None:
    target = tmp_path / "A-01.json"
    write_bytes_verified(
        target, json.dumps({"case_id": "A-01"}, ensure_ascii=False).encode("utf-8")
    )
    assert json.loads(target.read_text(encoding="utf-8")) == {"case_id": "A-01"}
