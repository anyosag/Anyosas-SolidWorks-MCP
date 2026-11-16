"""Where SolidWorks is installed, read from the registry.

SolidWorks writes each installed release's folder to
``HKLM\\SOFTWARE\\SolidWorks\\SOLIDWORKS <year>\\Setup``, value
``SolidWorks Folder``. The API help (``api\\sldworksapi.chm``) and the .NET
interop assemblies (``SolidWorks.Interop.*.dll``) live in that folder.

Reading the registry needs SolidWorks installed, not running: nothing here
starts, attaches to or calls SolidWorks.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

#: The registry key holding one subkey per installed release.
SOLIDWORKS_KEY = r"SOFTWARE\SolidWorks"
#: Where SolidWorks installs itself unless told otherwise.
DEFAULT_INSTALL_FOLDER = Path(r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS")

_RELEASE = re.compile(r"^SOLIDWORKS (\d{4})$", re.IGNORECASE)


def _registry(registry: Any, platform: str | None) -> Any | None:
    """The ``winreg`` module to read, or None off Windows.

    Args:
        registry (Any): A ``winreg``-like object (tests pass a fake), or None.
        platform (str | None): ``sys.platform`` (tests pass another value).

    Returns:
        Any | None: The registry to read.
    """
    if registry is not None:
        return registry
    if (platform or sys.platform) != "win32":
        return None
    import winreg

    return winreg


def install_folders(
    registry: Any = None, platform: str | None = None
) -> dict[int, Path]:
    """Every installed SolidWorks release's folder, by year.

    Args:
        registry (Any): A ``winreg``-like object; the real one by default.
        platform (str | None): ``sys.platform``; tests pass another value.

    Returns:
        dict[int, Path]: Year -> install folder, newest first; empty off
        Windows or without SolidWorks.
    """
    reg = _registry(registry, platform)
    if reg is None:
        return {}
    try:
        root = reg.OpenKey(reg.HKEY_LOCAL_MACHINE, SOLIDWORKS_KEY)
    except OSError:
        return {}
    folders: dict[int, Path] = {}
    with root:
        for position in range(reg.QueryInfoKey(root)[0]):
            match = _RELEASE.match(reg.EnumKey(root, position))
            if match is None:
                continue
            year = int(match.group(1))
            try:
                with reg.OpenKey(root, rf"{match.group(0)}\Setup") as setup:
                    value, _ = reg.QueryValueEx(setup, "SolidWorks Folder")
            except OSError:
                continue
            if isinstance(value, str) and value.strip():
                folders[year] = Path(value.strip())
    return dict(sorted(folders.items(), reverse=True))


def install_folder(
    year: int | None = None, registry: Any = None, platform: str | None = None
) -> tuple[int | None, Path | None]:
    """The install folder of one SolidWorks release.

    Args:
        year (int | None): The release wanted; None for the newest. A year
            that isn't installed falls back to the newest.
        registry (Any): A ``winreg``-like object; the real one by default.
        platform (str | None): ``sys.platform``; tests pass another value.

    Returns:
        tuple[int | None, Path | None]: The release's year and folder; the
        default folder with year None when the registry names none but it
        exists; ``(None, None)`` when nothing is found.
    """
    folders = install_folders(registry, platform)
    if year in folders:
        return year, folders[year]
    if folders:
        newest = next(iter(folders))
        return newest, folders[newest]
    if DEFAULT_INSTALL_FOLDER.is_dir():
        return None, DEFAULT_INSTALL_FOLDER
    return None, None
