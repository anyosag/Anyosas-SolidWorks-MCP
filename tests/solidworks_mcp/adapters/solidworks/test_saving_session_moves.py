"""The save guard follows this session's files when they are renamed or moved.

The Dipper: a part made this session, then renamed on disk with ``mv``
(``move_document`` had failed), was asked about as "a modified file that
existed before this session", and the assembly save was refused.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from solidworks_mcp.adapters.solidworks import saving
from solidworks_mcp.adapters.solidworks.saving import NeedsConfirmation

from .test_saving import _Adapter, _assembly, _Doc, _file, _plain_dispatch  # noqa: F401


def _made_this_session(adapter: _Adapter, tmp_path: Path, name: str) -> _Doc:
    """A new part this session saved for the first time as ``name``."""
    part = _Doc("", dirty=True)
    saving.save_document(adapter, part, str(tmp_path / name))
    return part


def test_a_part_made_this_session_and_renamed_is_still_its_own(
    tmp_path: Path,
) -> None:
    adapter = _Adapter()
    _made_this_session(adapter, tmp_path, "handle.SLDPRT")
    os.rename(tmp_path / "handle.SLDPRT", tmp_path / "dipper_handle.SLDPRT")
    renamed = _Doc(str(tmp_path / "dipper_handle.SLDPRT"), dirty=True)

    outcome = saving.save_document(adapter, _assembly(tmp_path, renamed))

    assert not isinstance(outcome, NeedsConfirmation)
    assert outcome["saved_references"] == [renamed.path]
    assert saving.moved_session_file(adapter, renamed.path)


def test_a_renamed_part_someone_saved_since_is_the_users(tmp_path: Path) -> None:
    adapter = _Adapter()
    _made_this_session(adapter, tmp_path, "handle.SLDPRT")
    moved = tmp_path / "dipper_handle.SLDPRT"
    os.rename(tmp_path / "handle.SLDPRT", moved)
    moved.write_bytes(b"the user saved it again")
    renamed = _Doc(str(moved), dirty=True)

    outcome = saving.save_document(adapter, _assembly(tmp_path, renamed))

    assert outcome == NeedsConfirmation([renamed.path])


def test_the_users_own_file_renamed_is_still_asked_about(tmp_path: Path) -> None:
    adapter = _Adapter()
    os.rename(_file(tmp_path, "theirs.SLDPRT"), tmp_path / "renamed.SLDPRT")
    theirs = _Doc(str(tmp_path / "renamed.SLDPRT"), dirty=True)

    outcome = saving.save_document(adapter, _assembly(tmp_path, theirs))

    assert outcome == NeedsConfirmation([theirs.path])


def test_a_file_system_without_file_ids_tells_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _Adapter()
    path = _file(tmp_path, "x.SLDPRT")
    real = os.stat

    def no_ids(target: str, *args: object, **kwargs: object) -> object:
        status = real(target, *args, **kwargs)  # type: ignore[arg-type]
        if str(target) != path:
            return status
        return SimpleNamespace(
            st_dev=status.st_dev,
            st_ino=0,
            st_size=status.st_size,
            st_mtime=status.st_mtime,
            st_mtime_ns=status.st_mtime_ns,
        )

    monkeypatch.setattr(saving.os, "stat", no_ids)
    saving.record_save(adapter, path)

    assert saving.session_files(adapter) == {}
    assert saving.moved_session_file(adapter, path) is False
    assert saving.moved_session_file(adapter, str(tmp_path / "none.SLDPRT")) is False


def test_a_move_the_session_made_carries_its_records(tmp_path: Path) -> None:
    adapter = _Adapter()
    _made_this_session(adapter, tmp_path, "a.SLDPRT")
    (tmp_path / "parts").mkdir()
    new = tmp_path / "parts" / "b.SLDPRT"
    os.rename(tmp_path / "a.SLDPRT", new)

    saving.follow_move(adapter, str(tmp_path / "a.SLDPRT"), str(new))

    key = saving.normalize(str(new))
    assert key in saving.session_paths(adapter)
    assert saving.normalize(str(tmp_path / "a.SLDPRT")) not in saving.session_paths(
        adapter
    )
    assert saving.saved_by_session(adapter, str(new))


def test_a_copy_to_another_drive_is_recorded_again(tmp_path: Path) -> None:
    adapter = _Adapter()
    _made_this_session(adapter, tmp_path, "a.SLDPRT")
    saving.session_paths(adapter).clear()  # only its save record is left
    elsewhere = tmp_path / "b.SLDPRT"
    shutil.copy2(tmp_path / "a.SLDPRT", elsewhere)  # a new file id, same time
    os.remove(tmp_path / "a.SLDPRT")
    assert not saving.moved_session_file(adapter, str(elsewhere))

    saving.follow_move(adapter, str(tmp_path / "a.SLDPRT"), str(elsewhere))

    assert saving.moved_session_file(adapter, str(elsewhere))


def test_a_move_that_rewrote_the_file_is_not_the_sessions(tmp_path: Path) -> None:
    adapter = _Adapter()
    path = _file(tmp_path, "a.SLDPRT")
    saving.record_save(adapter, path)
    new = tmp_path / "b.SLDPRT"
    new.write_bytes(b"rewritten by the move")
    os.utime(new, (1, 1))
    os.remove(path)

    saving.follow_move(adapter, path, str(new))

    assert saving.session_saves(adapter)[saving.normalize(str(new))] is None
    assert not saving.saved_by_session(adapter, str(new))
    assert not saving.moved_session_file(adapter, str(new))


def test_moving_a_file_the_session_never_wrote_records_nothing(
    tmp_path: Path,
) -> None:
    adapter = _Adapter()
    path = _file(tmp_path, "theirs.SLDPRT")
    os.rename(path, tmp_path / "moved.SLDPRT")

    saving.follow_move(adapter, path, str(tmp_path / "moved.SLDPRT"))

    assert saving.session_paths(adapter) == set()
    assert saving.session_files(adapter) == {}
