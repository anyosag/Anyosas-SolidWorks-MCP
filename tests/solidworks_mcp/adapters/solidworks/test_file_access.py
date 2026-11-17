"""Whether a file can be moved and a folder written (``file_access``)."""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import file_access

_WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="Win32 sharing")


class _Kernel32:
    """``CreateFileW`` that opens, or fails with a set error."""

    def __init__(self, handle: Any = 7) -> None:
        self.handle = handle
        self.opened: list[tuple[Any, ...]] = []
        self.closed: list[Any] = []
        self.CreateFileW = self._create  # noqa: N815
        self.CloseHandle = self.closed.append  # noqa: N815

    def _create(self, *args: Any) -> Any:
        self.opened.append(args)
        return self.handle


def test_a_file_that_opens_gives_no_error_and_is_closed() -> None:
    kernel32 = _Kernel32()

    code = file_access.open_error("C:/x.sldprt", file_access.DELETE, 0, kernel32)

    assert code == 0
    assert kernel32.opened == [("C:/x.sldprt", file_access.DELETE, 7, None, 3, 0, None)]
    assert kernel32.closed == [7]


@pytest.mark.parametrize("handle", [None, file_access._INVALID_HANDLE])
def test_a_file_that_does_not_open_gives_its_error(handle: Any) -> None:
    kernel32 = _Kernel32(handle)

    code = file_access.open_error(
        "C:/x.sldprt", file_access.DELETE, 0, kernel32, last_error=lambda: 32
    )

    assert code == 32
    assert kernel32.closed == []


def test_off_windows_nothing_is_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    assert file_access._kernel32("linux") is None

    monkeypatch.setattr(file_access, "_kernel32", lambda: None)

    assert file_access.open_error("/x.sldprt", file_access.DELETE) is None


def test_kernel32_gets_the_signatures_it_is_called_with() -> None:
    dll = SimpleNamespace(CreateFileW=SimpleNamespace(), CloseHandle=SimpleNamespace())

    loaded = file_access._kernel32("win32", loader=lambda *_a, **_k: dll)

    assert loaded is dll
    assert len(dll.CreateFileW.argtypes) == 7
    assert dll.CloseHandle.argtypes


@pytest.mark.parametrize(
    ("code", "held"),
    [(32, True), (33, True), (0, False), (5, False), (2, False), (None, False)],
)
def test_only_a_sharing_or_lock_violation_stops_a_move(code: Any, held: bool) -> None:
    asked: list[tuple[Any, ...]] = []

    def opener(*args: Any) -> Any:
        asked.append(args)
        return code

    reason = file_access.move_blocker("C:/x.sldprt", opener)

    assert asked == [("C:/x.sldprt", file_access.DELETE)]
    assert (reason is not None) is held
    if held:
        assert reason.startswith("is open in another program")
        assert "sync client" in reason


def test_a_folder_this_user_cannot_write_is_named(tmp_path: Path) -> None:
    asked: list[tuple[Any, ...]] = []

    def denied(*args: Any) -> int:
        asked.append(args)
        return file_access.ERROR_ACCESS_DENIED

    reason = file_access.unwritable_folder(str(tmp_path), denied, "win32")
    fine = file_access.unwritable_folder(str(tmp_path), lambda *_a: 0, "win32")

    assert reason == "can't be written by this user: access is denied"
    assert asked == [(str(tmp_path), file_access.FILE_ADD_FILE, 0x02000000)]
    assert fine is None


def test_a_folder_still_to_make_is_checked_where_it_would_be_made(
    tmp_path: Path,
) -> None:
    asked: list[str] = []

    def denied(path: str, *_a: Any) -> int:
        asked.append(path)
        return file_access.ERROR_ACCESS_DENIED

    reason = file_access.unwritable_folder(str(tmp_path / "a" / "b"), denied, "win32")

    assert asked == [str(tmp_path)]
    assert reason is not None and reason.endswith(f"(through {tmp_path})")


def test_off_windows_a_folder_is_asked_through_os_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert file_access.unwritable_folder(str(tmp_path), platform="linux") is None

    monkeypatch.setattr(file_access.os, "access", lambda *_a: False)

    assert (
        file_access.unwritable_folder(str(tmp_path), platform="linux")
        == "can't be written by this user"
    )


def test_a_folder_on_no_disk_is_not_judged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(file_access.os.path, "isdir", lambda _p: False)

    assert file_access.unwritable_folder("Q:/nowhere", lambda *_a: 5, "win32") is None


@pytest.mark.parametrize(
    ("parts", "client"),
    [
        (("Shared drives", "Team", "dipper.sldprt"), "Google Drive"),
        (("My Drive", "dipper.sldprt"), "Google Drive"),
        (("Users", "me", "Google Drive", "a.sldprt"), "Google Drive"),
        (("Users", "me", "OneDrive - Acme", "a.sldprt"), "OneDrive"),
        (("Users", "me", "OneDrive", "a.sldprt"), "OneDrive"),
        (("Users", "me", "Dropbox (Team)", "a.sldprt"), "Dropbox"),
        (("Users", "me", "Dropbox", "a.sldprt"), "Dropbox"),
        (("Users", "me", "iCloudDrive", "a.sldprt"), "iCloud Drive"),
        (("Users", "me", "Documents", "a.sldprt"), None),
    ],
)
def test_a_sync_clients_folder_is_known_by_its_name(
    tmp_path: Path, parts: tuple[str, ...], client: str | None
) -> None:
    path = os.path.join(str(tmp_path), *parts)

    assert file_access.sync_client(path, env={}) == client


def test_onedrive_is_known_by_its_environment(tmp_path: Path) -> None:
    root = tmp_path / "Work Files"
    env = {"OneDriveCommercial": str(root), "OneDrive": ""}

    assert file_access.sync_client(str(root / "a.sldprt"), env) == "OneDrive"
    assert file_access.sync_client(str(root), env) == "OneDrive"
    assert file_access.sync_client(str(tmp_path / "Work"), env) is None
    assert file_access.sync_client(str(tmp_path / "x.sldprt")) is None


class _Locked(PermissionError):
    winerror = 32


class _Denied(OSError):
    winerror = 5


def test_a_failed_file_move_says_why() -> None:
    assert file_access.os_error_cause(_Locked(13, "in use")).startswith(
        "the file is open in another program"
    )
    assert file_access.os_error_cause(_Denied(13, "denied")).startswith(
        "access is denied: this user may not move it"
    )
    assert "access is denied" in file_access.os_error_cause(PermissionError("no"))
    assert file_access.os_error_cause(
        FileNotFoundError(errno.ENOENT, "gone")
    ).startswith("the file is no longer where it was")
    assert file_access.os_error_cause(OSError(errno.EIO, "disk")) == "[Errno 5] disk"


@_WINDOWS_ONLY
def test_a_file_another_handle_holds_cannot_be_moved(tmp_path: Path) -> None:
    part = tmp_path / "held.sldprt"
    part.write_bytes(b"sw")

    with part.open("rb"):  # Python's handles don't share delete access
        held = file_access.move_blocker(str(part))
    free = file_access.move_blocker(str(part))

    assert held is not None and held.startswith("is open in another program")
    assert free is None
    assert part.read_bytes() == b"sw"
    assert file_access.unwritable_folder(str(tmp_path)) is None
