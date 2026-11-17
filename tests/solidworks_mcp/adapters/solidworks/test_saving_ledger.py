"""The save guard after a server restart: the ledger remembers the server's files.

The Dipper build (SOLIDWORKS 2026 SP4.1): after a context compaction the
MCP server had restarted, and ``save_assembly`` asked about a part the agent
had made itself, about six times.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import io as io_mod
from solidworks_mcp.adapters.solidworks import saving, sessions
from solidworks_mcp.adapters.solidworks.file_ledger import FileLedger
from solidworks_mcp.adapters.solidworks.saving import NeedsConfirmation

from .test_saving import _Adapter, _assembly, _Doc, _file

SW = 9396
STARTED = 133_000_000


class _StampedDoc(_Doc):
    """A document whose ``GetUpdateStamp`` counts its changes."""

    def __init__(self, *args: Any, stamp: int | None = 10, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.stamp = stamp

    def GetUpdateStamp(self) -> int | None:  # noqa: N802
        return self.stamp


@pytest.fixture(autouse=True)
def _plain_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(io_mod, "_dynamic", SimpleNamespace(Dispatch=lambda obj: obj))


def _server(ledger: FileLedger, sw: int = SW, started: int = STARTED) -> _Adapter:
    """One server process: its own memory, the ledger on disk, a SolidWorks."""
    adapter = _Adapter()
    adapter._file_ledger = ledger  # type: ignore[attr-defined]
    adapter._sw_process_id = sw  # type: ignore[attr-defined]
    adapter._sw_process_started = (sw, started)  # type: ignore[attr-defined]
    return adapter


@pytest.fixture
def ledger(tmp_path: Path) -> FileLedger:
    return FileLedger(tmp_path / "ledger.json")


def _made_then_restarted(tmp_path: Path, ledger: FileLedger) -> _StampedDoc:
    """The server saves a new part as a file, then restarts."""
    part = _StampedDoc()
    target = str(tmp_path / "Cage.SLDPRT")
    saving.save_document(_server(ledger), part, target)
    part.path, part.dirty = target, True  # only a rebuild flag since
    return part


def test_a_part_the_server_made_is_its_own_after_a_restart(
    tmp_path: Path, ledger: FileLedger
) -> None:
    part = _made_then_restarted(tmp_path, ledger)
    assembly = _assembly(tmp_path, part)

    outcome = saving.save_document(_server(ledger), assembly)

    assert not isinstance(outcome, NeedsConfirmation)
    assert outcome["saved_references"] == [part.path]


@pytest.mark.parametrize(
    "change",
    ["another_solidworks", "restarted_solidworks", "saved_since", "no_ledger"],
)
def test_anything_unsure_still_asks(
    tmp_path: Path, ledger: FileLedger, change: str
) -> None:
    part = _made_then_restarted(tmp_path, ledger)
    server = _server(ledger)
    if change == "another_solidworks":
        server = _server(ledger, sw=1234)
    elif change == "restarted_solidworks":
        server = _server(ledger, started=STARTED + 1)
    elif change == "saved_since":
        stat = os.stat(part.path)
        os.utime(part.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
    else:
        server._file_ledger = None  # type: ignore[attr-defined]

    outcome = saving.save_document(server, _assembly(tmp_path, part))

    assert outcome == NeedsConfirmation([part.path])


@pytest.mark.parametrize(
    ("stamp_now", "asks"),
    [(10, False), (11, True), (None, True)],
    ids=["unchanged-model", "changed-model", "unreadable"],
)
def test_a_users_part_the_server_saved_counts_only_while_its_model_is_unchanged(
    tmp_path: Path, ledger: FileLedger, stamp_now: int | None, asks: bool
) -> None:
    user_part = _StampedDoc(_file(tmp_path, "Bracket.SLDPRT"), dirty=True)
    saving.save_document(_server(ledger), _assembly(tmp_path, user_part), None, True)
    user_part.dirty, user_part.stamp = True, stamp_now  # after a restart

    outcome = saving.save_document(_server(ledger), _assembly(tmp_path, user_part))

    assert isinstance(outcome, NeedsConfirmation) is asks


def test_no_solidworks_process_is_known_without_its_window(
    ledger: FileLedger, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server(ledger, sw=0)
    assert saving.solidworks_process(server) is None

    calls: list[int] = []
    monkeypatch.setattr(
        sessions, "process_started", lambda pid: calls.append(pid) or STARTED
    )
    fresh = _Adapter()
    fresh._sw_process_id = SW  # type: ignore[attr-defined]
    assert saving.solidworks_process(fresh) == (SW, STARTED)
    assert saving.solidworks_process(fresh) == (SW, STARTED)
    assert calls == [SW]  # read once
    monkeypatch.setattr(sessions, "process_started", lambda pid: None)
    other = _Adapter()
    other._sw_process_id = 77  # type: ignore[attr-defined]
    assert saving.solidworks_process(other) is None


def test_a_stamp_that_is_not_a_number_is_unknown() -> None:
    adapter = _Adapter()
    assert saving.update_stamp(adapter, None) is None
    assert saving.update_stamp(adapter, SimpleNamespace(GetUpdateStamp=True)) is None
    assert saving.update_stamp(adapter, SimpleNamespace(GetUpdateStamp=3.0)) == 3


def test_a_move_carries_the_ledger_entry(tmp_path: Path, ledger: FileLedger) -> None:
    part = _made_then_restarted(tmp_path, ledger)
    server = _server(ledger)
    moved = str(tmp_path / "moved" / "Cage.SLDPRT")
    os.makedirs(os.path.dirname(moved))
    os.replace(part.path, moved)

    saving.follow_move(server, part.path, moved)

    assert ledger.unchanged_entry(moved) is not None
    saving.follow_move(_Adapter(), moved, part.path)  # no ledger: nothing to carry
