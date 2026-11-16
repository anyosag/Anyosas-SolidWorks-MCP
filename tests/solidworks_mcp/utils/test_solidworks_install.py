"""SolidWorks install folders, read from a fake registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from solidworks_mcp.utils import solidworks_install
from solidworks_mcp.utils.solidworks_install import install_folder, install_folders


class FakeKey:
    """A registry key: its subkeys and values, usable as a context manager."""

    def __init__(self, subkeys: dict[str, FakeKey], values: dict[str, Any]) -> None:
        self.subkeys = subkeys
        self.values = values

    def __enter__(self) -> FakeKey:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class FakeRegistry:
    """``winreg`` over ``HKLM\\SOFTWARE\\SolidWorks`` with the given releases."""

    HKEY_LOCAL_MACHINE = "HKLM"

    def __init__(self, releases: dict[str, Any] | None) -> None:
        self.root = (
            None
            if releases is None
            else FakeKey(
                {
                    name: FakeKey(
                        {"Setup": FakeKey({}, setup)} if setup is not None else {},
                        {},
                    )
                    for name, setup in releases.items()
                },
                {},
            )
        )

    def OpenKey(self, parent: Any, path: str) -> FakeKey:
        if parent == "HKLM":
            if self.root is None or path != r"SOFTWARE\SolidWorks":
                raise FileNotFoundError(path)
            return self.root
        key = parent
        for part in path.split("\\"):
            if part not in key.subkeys:
                raise FileNotFoundError(path)
            key = key.subkeys[part]
        return key

    def QueryInfoKey(self, key: FakeKey) -> tuple[int, int, int]:
        return (len(key.subkeys), len(key.values), 0)

    def EnumKey(self, key: FakeKey, position: int) -> str:
        return list(key.subkeys)[position]

    def QueryValueEx(self, key: FakeKey, name: str) -> tuple[Any, int]:
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name], 1


def test_every_release_with_a_folder_newest_first() -> None:
    registry = FakeRegistry(
        {
            "SOLIDWORKS 2025": {"SolidWorks Folder": "D:/SW2025/ "},
            "SOLIDWORKS 2026": {"SolidWorks Folder": "C:/SW2026/"},
            "SOLIDWORKS CAM": {"SolidWorks Folder": "C:/CAM"},
            "SOLIDWORKS 2024": None,  # no Setup key
            "SOLIDWORKS 2023": {"Other": "x"},  # no folder value
            "SOLIDWORKS 2022": {"SolidWorks Folder": "  "},
            "General": {},
        }
    )

    assert install_folders(registry) == {
        2026: Path("C:/SW2026"),
        2025: Path("D:/SW2025"),
    }


def test_no_solidworks_key_and_not_windows_find_nothing() -> None:
    assert install_folders(FakeRegistry(None)) == {}
    assert install_folders(platform="linux") == {}


def test_the_real_registry_is_read_on_windows() -> None:
    assert isinstance(install_folders(), dict)


def test_one_release_by_year_or_the_newest() -> None:
    registry = FakeRegistry(
        {
            "SOLIDWORKS 2025": {"SolidWorks Folder": "D:/SW2025"},
            "SOLIDWORKS 2026": {"SolidWorks Folder": "C:/SW2026"},
        }
    )

    assert install_folder(2025, registry) == (2025, Path("D:/SW2025"))
    assert install_folder(None, registry) == (2026, Path("C:/SW2026"))
    assert install_folder(2030, registry) == (2026, Path("C:/SW2026"))


def test_the_default_folder_when_the_registry_names_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(solidworks_install, "DEFAULT_INSTALL_FOLDER", tmp_path)
    assert install_folder(None, FakeRegistry(None)) == (None, tmp_path)

    monkeypatch.setattr(solidworks_install, "DEFAULT_INSTALL_FOLDER", tmp_path / "x")
    assert install_folder(None, FakeRegistry(None)) == (None, None)
