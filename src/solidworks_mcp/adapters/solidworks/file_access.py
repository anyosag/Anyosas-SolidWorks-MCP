"""Whether a file can be moved and a folder written, asked without changing either.

``move_document`` checks these before a move, so its dry run can say what
would stop it. The Dipper's part on ``G:\\Shared drives\\...`` (Google Drive
for desktop) passed the dry run and then failed: ``MoveDocument`` returned 2
(``swMoveCopyErrorFail``).

* :func:`move_blocker`: opens the file for ``DELETE`` only, which a move
  needs, sharing everything, and closes it at once: the file isn't read or
  written. A sharing violation means another program holds it open in a way
  that stops a move.
* :func:`unwritable_folder`: opens the destination folder (or the nearest
  one that exists) for ``FILE_ADD_FILE``. Folders' read-only attribute means
  nothing on Windows (Documents has it), so ``os.access`` isn't asked there.
* :func:`sync_client`: names the cloud-sync client whose folder a path is in.
* :func:`os_error_cause`: says why a plain file move failed.

Win32 only, no COM, so any thread may call these. Off Windows the checks
answer None (nothing known), except the folder check, which uses
``os.access``.
"""

from __future__ import annotations

import ctypes
import errno
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: ``DELETE``: the access right a rename or move needs on the file.
DELETE = 0x00010000
#: ``FILE_ADD_FILE``: the right to create a file in a folder.
FILE_ADD_FILE = 0x0002
_SHARE_ALL = 0x00000007  # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
_OPEN_EXISTING = 3
#: Needed to open a folder with ``CreateFileW``.
_BACKUP_SEMANTICS = 0x02000000
_INVALID_HANDLE = ctypes.c_void_p(-1).value

ERROR_ACCESS_DENIED = 5
ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION = 33

#: Folder names a sync client gives its root or its top folders, lower case.
_SYNC_FOLDERS = {
    "my drive": "Google Drive",
    "shared drives": "Google Drive",
    "google drive": "Google Drive",
    "dropbox": "Dropbox",
    "icloud drive": "iCloud Drive",
    "iclouddrive": "iCloud Drive",
    "onedrive": "OneDrive",
}
#: Environment variables OneDrive sets to its folders.
_ONEDRIVE_VARIABLES = ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")

_HELD_OPEN = (
    "is open in another program that doesn't let it be moved: a sync client "
    "(Google Drive, OneDrive, Dropbox) while it uploads or downloads it, an "
    "antivirus scan, Explorer's preview pane, or SolidWorks in another "
    "session. Wait for it to finish, or close it, and try again"
)


def _kernel32(platform: str | None = None, loader: Any = None) -> Any | None:
    """``kernel32`` with the signatures :func:`open_error` uses, or None off Windows."""
    if (platform or sys.platform) != "win32":
        return None
    dll = (loader or ctypes.WinDLL)("kernel32", use_last_error=True)
    dll.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    dll.CreateFileW.restype = ctypes.c_void_p
    dll.CloseHandle.argtypes = [ctypes.c_void_p]
    dll.CloseHandle.restype = ctypes.c_int
    return dll


def open_error(
    path: str,
    access: int,
    flags: int = 0,
    kernel32: Any = None,
    last_error: Callable[[], int] = ctypes.get_last_error,
) -> int | None:
    """The Windows error opening ``path`` for ``access`` gives; 0 when it opens.

    Every kind of sharing is allowed and the handle is closed at once, so
    nothing is read, written or kept open.

    Args:
        path: The file or folder.
        access: The access rights asked for (:data:`DELETE`, ...).
        flags: ``CreateFileW`` flags (``FILE_FLAG_BACKUP_SEMANTICS`` for a
            folder).
        kernel32: A ``kernel32``-like object (tests pass a fake).
        last_error: ``ctypes.get_last_error`` (tests pass a fake).

    Returns:
        int | None: The ``GetLastError`` code, 0 when it opened, or None off
        Windows.
    """
    dll = kernel32 if kernel32 is not None else _kernel32()
    if dll is None:
        return None
    handle = dll.CreateFileW(
        path, access, _SHARE_ALL, None, _OPEN_EXISTING, flags, None
    )
    if handle is None or handle == _INVALID_HANDLE:
        return int(last_error())
    dll.CloseHandle(handle)
    return 0


def move_blocker(
    path: str, opener: Callable[..., int | None] = open_error
) -> str | None:
    """Why ``path`` can't be moved right now, or None when nothing is known to stop it.

    Only a sharing or lock violation counts: another program has it open.
    An access denied is left to the move itself, which says so if it fails.

    Args:
        path: The file.
        opener: :func:`open_error` (tests pass a fake).

    Returns:
        str | None: The reason, to follow the path in a sentence.
    """
    code = opener(path, DELETE)
    if code in (ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION):
        return _HELD_OPEN
    return None


def unwritable_folder(
    folder: str,
    opener: Callable[..., int | None] = open_error,
    platform: str | None = None,
) -> str | None:
    """Why no file can be created in ``folder``, or None when nothing says so.

    A folder that doesn't exist yet is checked through the nearest one that
    does, where it would be made.

    Args:
        folder: The destination folder.
        opener: :func:`open_error` (tests pass a fake).
        platform: ``sys.platform`` (tests pass another value).

    Returns:
        str | None: The reason, to follow the folder in a sentence.
    """
    existing = os.path.abspath(folder)
    while not os.path.isdir(existing):
        parent = os.path.dirname(existing)
        if parent == existing:
            return None
        existing = parent
    where = "" if existing == os.path.abspath(folder) else f" (through {existing})"
    if (platform or sys.platform) != "win32":
        if os.access(existing, os.W_OK):
            return None
        return f"can't be written by this user{where}"
    if opener(existing, FILE_ADD_FILE, _BACKUP_SEMANTICS) == ERROR_ACCESS_DENIED:
        return f"can't be written by this user: access is denied{where}"
    return None


def sync_client(path: str, env: dict[str, str] | None = None) -> str | None:
    """The cloud-sync client whose folder ``path`` is in, or None.

    Google Drive for desktop shows ``My Drive`` and ``Shared drives`` on its
    own drive; OneDrive's folders are in its environment variables or named
    ``OneDrive - <organisation>``; Dropbox's are ``Dropbox`` or
    ``Dropbox (<team>)``.

    Args:
        path: A file or folder.
        env: The environment (tests pass their own).

    Returns:
        str | None: ``"Google Drive"``, ``"OneDrive"``, ``"Dropbox"``,
        ``"iCloud Drive"`` or None.
    """
    env = dict(os.environ) if env is None else env
    full = os.path.normcase(os.path.abspath(path))
    for variable in _ONEDRIVE_VARIABLES:
        root = (env.get(variable) or "").strip()
        if root:
            base = os.path.normcase(os.path.abspath(root))
            if full == base or full.startswith(base.rstrip(os.sep) + os.sep):
                return "OneDrive"
    for part in Path(os.path.abspath(path)).parts[1:]:
        name = part.lower()
        client = _SYNC_FOLDERS.get(name)
        if client is None and name.startswith("onedrive - "):
            client = "OneDrive"
        if client is None and name.startswith("dropbox ("):
            client = "Dropbox"
        if client is not None:
            return client
    return None


def os_error_cause(exc: OSError) -> str:
    """Why a plain file move failed, in words a caller can act on."""
    code = getattr(exc, "winerror", None)
    if code in (ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION):
        return f"the file {_HELD_OPEN}"
    if isinstance(exc, PermissionError) or code == ERROR_ACCESS_DENIED:
        return (
            "access is denied: this user may not move it out of its folder or "
            "write to the destination (a shared drive's permissions, or a "
            f"read-only file or folder) [{exc}]"
        )
    if exc.errno == errno.ENOENT:
        return f"the file is no longer where it was [{exc}]"
    return str(exc)
