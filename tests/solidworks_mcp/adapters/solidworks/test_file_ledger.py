"""The file ledger: what this server wrote, kept across restarts for the save guard."""

from __future__ import annotations

import json
import os
from pathlib import Path

from solidworks_mcp.adapters.solidworks import file_ledger
from solidworks_mcp.adapters.solidworks.file_ledger import FileLedger, ledger_path

SW = (9396, 133_000_000)


def _ledger(tmp_path: Path, clock: float = 5.0) -> FileLedger:
    return FileLedger(tmp_path / "ledger" / "written_files.json", clock=lambda: clock)


def _part(tmp_path: Path, name: str = "Cradle.SLDPRT") -> str:
    path = tmp_path / name
    path.write_bytes(b"made by the server")
    return str(path)


def test_the_ledger_is_under_local_app_data_unless_set() -> None:
    assert ledger_path({"SOLIDWORKS_MCP_FILE_LEDGER": "D:\\l.json"}) == Path(
        "D:\\l.json"
    )
    assert ledger_path({"LOCALAPPDATA": "C:\\L"}) == Path(
        "C:\\L", "solidworks_mcp", "written_files.json"
    )
    assert ledger_path({}) == Path.home() / "solidworks_mcp" / "written_files.json"
    assert ledger_path() == Path(os.environ["SOLIDWORKS_MCP_FILE_LEDGER"])


def test_a_recorded_file_is_found_while_nobody_saves_it(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    part = _part(tmp_path)

    ledger.record(part, created=True, solidworks=SW, update_stamp=41)

    entry = ledger.unchanged_entry(part)
    assert entry is not None
    assert entry["created"] is True
    assert (entry["solidworks_pid"], entry["solidworks_started"]) == SW
    assert entry["update_stamp"] == 41
    assert entry["saved_at"] == 5.0
    # Another server process reads the same file: a restart remembers.
    assert _ledger(tmp_path).unchanged_entry(part) == entry

    stat = os.stat(part)
    os.utime(part, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
    assert ledger.unchanged_entry(part) is None  # someone saved it since


def test_created_stays_through_later_saves(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    part = _part(tmp_path)
    ledger.record(part, created=True, solidworks=None, update_stamp=None)

    ledger.record(part, created=False, solidworks=SW, update_stamp=7)

    entry = ledger.unchanged_entry(part)
    assert entry is not None and entry["created"] is True
    assert entry["solidworks_pid"] == SW[0]


def test_a_renamed_file_is_found_by_its_id(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    part = _part(tmp_path)
    ledger.record(part, created=True, solidworks=SW, update_stamp=1)
    renamed = str(tmp_path / "Renamed.SLDPRT")
    os.replace(part, renamed)

    entry = ledger.unchanged_entry(renamed)

    assert entry is not None and entry["created"] is True
    assert ledger.unchanged_entry(part) is None  # gone from there


def test_unknown_missing_and_malformed_ledgers_find_nothing(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    part = _part(tmp_path)
    assert ledger.unchanged_entry(part) is None  # no ledger yet
    assert ledger.unchanged_entry(str(tmp_path / "none.SLDPRT")) is None
    missing = str(tmp_path / "none.SLDPRT")
    ledger.record(missing, created=True, solidworks=SW, update_stamp=1)
    assert not ledger.path.exists()  # nothing on disk: nothing recorded

    ledger.path.parent.mkdir(parents=True)
    for text in ("not json", "[1, 2]", json.dumps({"x": 3})):
        ledger.path.write_text(text, encoding="utf-8")
        assert ledger.entries() == {}
        assert ledger.unchanged_entry(part) is None


def test_a_ledger_that_cannot_be_written_is_skipped(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    ledger = FileLedger(blocker / "written_files.json")

    ledger.record(_part(tmp_path), created=True, solidworks=SW, update_stamp=1)

    assert blocker.read_text() == "not a directory"


def test_only_the_newest_entries_are_kept(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(file_ledger, "MAX_ENTRIES", 2)
    for number in range(3):
        _ledger(tmp_path, clock=float(number)).record(
            _part(tmp_path, f"P{number}.SLDPRT"),
            created=True,
            solidworks=SW,
            update_stamp=number,
        )

    kept = sorted(e["update_stamp"] for e in _ledger(tmp_path).entries().values())
    assert kept == [1, 2]


def test_a_move_carries_the_entry_when_the_file_is_unchanged(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    part = _part(tmp_path)
    ledger.record(part, created=True, solidworks=SW, update_stamp=1)
    moved = str(tmp_path / "moved.SLDPRT")
    os.replace(part, moved)

    ledger.follow_move(part, moved)

    entries = ledger.entries()
    assert [e["path"] for e in entries.values()] == [os.path.abspath(moved)]
    assert ledger.unchanged_entry(moved) is not None

    ledger.follow_move(str(tmp_path / "never.SLDPRT"), moved)  # nothing recorded
    changed = str(tmp_path / "changed.SLDPRT")
    Path(changed).write_bytes(b"something else entirely")
    ledger.follow_move(moved, changed)
    assert ledger.entries() == {}  # not the file the server wrote
