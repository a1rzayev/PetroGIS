"""The per-user processing history file."""

import json

from petrogis.io import processing_history as ph
from petrogis.io.processing_history import HistoryEntry, ProcessingHistory


def _entry(i, status="ok"):
    return HistoryEntry("vector:clip", "Clip", f"2026-10-28T10:00:{i:02d}+04:00", 0.5, status,
                        f"run {i}", {"input": {"id": "a", "name": "wells"}})  # fmt: skip


def test_append_and_read_newest_first(tmp_path):
    history = ProcessingHistory(tmp_path / "sub" / "h.jsonl")
    assert history.entries() == []
    for i in range(3):
        history.append(_entry(i))
    entries = history.entries()
    assert [e.message for e in entries] == ["run 2", "run 1", "run 0"]
    assert entries[0].parameters["input"]["name"] == "wells"
    history.clear()
    assert history.entries() == []


def test_damaged_lines_are_skipped(tmp_path):
    path = tmp_path / "h.jsonl"
    good = json.dumps(_entry(1).__dict__)
    path.write_bytes(
        b"not json\n\xff\xfe\n[]\n"
        + json.dumps({"algorithm_id": "x"}).encode() + b"\n"
        + json.dumps({**_entry(2).__dict__, "status": "exploded"}).encode() + b"\n"
        + json.dumps({**_entry(3).__dict__, "parameters": [1]}).encode() + b"\n"
        + json.dumps({**_entry(4).__dict__, "duration": "slow"}).encode() + b"\n"
        + b"x" * (ph.MAX_LINE_BYTES + 1) + b"\n\n"
        + good.encode() + b"\n"
    )  # fmt: skip
    assert [e.message for e in ProcessingHistory(path).entries()] == ["run 1"]


def test_history_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(ph, "MAX_ENTRIES", 5)
    history = ProcessingHistory(tmp_path / "h.jsonl")
    for i in range(30):
        history.append(_entry(i, "failed" if i % 2 else "canceled"))
    entries = history.entries()
    assert len(entries) == 5 and entries[0].message == "run 29"
    assert len((tmp_path / "h.jsonl").read_text().splitlines()) <= 30


def test_unwritable_history_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    history = ProcessingHistory(blocker / "h.jsonl")  # parent is a file
    history.append(_entry(1))
    assert history.entries() == []
    history.clear()
    directory = ProcessingHistory(tmp_path)  # reading a directory
    assert directory.entries() == []
