"""STL export of an assembly: one file, the user's option put back.

A DishRack assembly exported to ``X.stl`` came out as ten files, one per
component (``X - Assembly-1 Part-1.STL``, ...), because the user's "all
components in one file" STL option was off; the tool then said the export
failed although files had been written.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from solidworks_mcp.adapters import pywin32_adapter
from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import stl_settings as stl
from solidworks_mcp.adapters.solidworks import window as window_module

from .solidworks.test_stl_settings import _App

PART, ASSEMBLY = 1, 2


@pytest.fixture(autouse=True)
def _no_keepers() -> Iterator[None]:
    yield
    for keeper in list(window_module._KEEPERS.values()):
        keeper.stop()
    window_module._KEEPERS.clear()


def _one_file(path: str) -> None:
    Path(path).write_bytes(b"solid one\nendsolid one\n")


def _per_component(path: str) -> None:
    stem = os.path.splitext(path)[0]
    for name in ("Assembly-1 Part-1", "Assembly-1 Part-2"):
        Path(f"{stem} - {name}.STL").write_bytes(b"solid part\nendsolid part\n")


def _fails(_path: str) -> None:
    raise RuntimeError("SaveAs2 type mismatch")


class _Doc:
    """An ``IModelDoc2`` whose ``SaveAs2``/``SaveAs3`` write what they're given."""

    def __init__(
        self,
        app: _App,
        doc_type: int,
        save_as2: Callable[[str], None],
        save_as3: Callable[[str], None] = _fails,
    ) -> None:
        self.app = app
        self.doc_type = doc_type
        self.one_file_during_export: list[Any] = []
        self.save_as3_calls = 0
        self._save_as2 = save_as2
        self._save_as3 = save_as3
        self.Extension = SimpleNamespace(SaveAs2=self._extension_save_as2)

    def GetType(self) -> int:  # noqa: N802
        return self.doc_type

    def ResolveAllLightweightComponents(self, _now: bool) -> bool:  # noqa: N802
        return True

    def _extension_save_as2(self, path: str, *_args: Any) -> bool:
        self.one_file_during_export.append(
            self.app.values[("toggle", stl.STL_ONE_FILE)]
        )
        self._save_as2(path)
        return True

    def SaveAs3(self, path: str, *_args: Any) -> bool:  # noqa: N802
        self.save_as3_calls += 1
        self._save_as3(path)
        return True


def _adapter(monkeypatch: pytest.MonkeyPatch, app: _App, doc: Any) -> PyWin32Adapter:
    monkeypatch.setattr(pywin32_adapter, "PYWIN32_AVAILABLE", True)
    monkeypatch.setattr(pywin32_adapter.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        pywin32_adapter, "pywintypes", SimpleNamespace(com_error=RuntimeError)
    )
    monkeypatch.setenv("SOLIDWORKS_MCP_DIALOG_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("SOLIDWORKS_MCP_KEEP_MINIMIZED", "0")
    made = PyWin32Adapter({})
    made.swApp = app
    made.currentModel = doc
    return made


async def test_a_part_is_exported_as_before_and_reports_its_units(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _App()  # the user's units: inches (3)
    doc = _Doc(app, PART, _one_file)
    out = str(tmp_path / "bracket.stl")

    result = await _adapter(monkeypatch, app, doc).export_file(out, "stl")

    assert result.is_success, result.error
    assert result.data == {"file_path": out, "units": "in", "document_type": "part"}
    assert doc.one_file_during_export == [False]
    assert app.writes == []


async def test_an_assembly_goes_into_one_file_and_the_option_comes_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _App()
    doc = _Doc(app, ASSEMBLY, _one_file)
    out = str(tmp_path / "rack.stl")

    result = await _adapter(monkeypatch, app, doc).export_file(out, "stl")

    assert result.is_success, result.error
    assert doc.one_file_during_export == [True]
    assert app.values[("toggle", stl.STL_ONE_FILE)] is False
    assert result.data["document_type"] == "assembly"
    assert result.data["mesh"] == {
        "applied": ["all components in one file"],
        "restored": True,
    }


async def test_an_option_that_cannot_be_put_back_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _App(broken={stl.STL_ONE_FILE})  # the second write (restore) fails
    doc = _Doc(app, ASSEMBLY, _one_file)

    result = await _adapter(monkeypatch, app, doc).export_file(
        str(tmp_path / "rack.stl"), "stl"
    )

    assert result.is_success
    assert result.data["mesh"]["restored"] is False


async def test_files_written_per_component_are_named_and_not_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _App(refuse={stl.STL_ONE_FILE})  # SolidWorks keeps the user's "off"
    doc = _Doc(app, ASSEMBLY, _per_component)

    result = await _adapter(monkeypatch, app, doc).export_file(
        str(tmp_path / "rack.stl"), "stl"
    )

    assert result.is_error
    assert "SolidWorks wrote 2 STL files, one per component" in (result.error or "")
    assert "rack - Assembly-1 Part-1.STL, rack - Assembly-1 Part-2.STL" in (
        result.error or ""
    )
    assert doc.save_as3_calls == 0
    assert app.values[("toggle", stl.STL_ONE_FILE)] is False


async def test_files_the_saveas3_fallback_wrote_per_component_are_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _App()
    doc = _Doc(app, ASSEMBLY, _fails, save_as3=_per_component)

    result = await _adapter(monkeypatch, app, doc).export_file(
        str(tmp_path / "rack.stl"), "stl"
    )

    assert doc.save_as3_calls == 1
    assert "SolidWorks wrote 2 STL files" in (result.error or "")
    assert app.values[("toggle", stl.STL_ONE_FILE)] is False


async def test_an_export_that_writes_nothing_still_says_what_was_tried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _App()
    doc = _Doc(app, PART, _fails)

    result = await _adapter(monkeypatch, app, doc).export_file(
        str(tmp_path / "bracket.stl"), "stl"
    )

    assert "tried Extension.SaveAs2 and SaveAs3" in (result.error or "")


async def test_units_solidworks_does_not_report_are_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _App()
    del app.values[("integer", stl.STL_UNITS)]  # the read raises KeyError
    doc = _Doc(app, PART, _one_file)

    result = await _adapter(monkeypatch, app, doc).export_file(
        str(tmp_path / "bracket.stl"), "stl"
    )

    assert result.data["units"] is None


async def test_mesh_settings_and_the_one_file_option_are_reported_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _App()
    adapter = _adapter(monkeypatch, app, SimpleNamespace())
    monkeypatch.setattr(
        adapter,
        "export_file",
        AsyncMock(
            return_value=AdapterResult(
                status=AdapterResultStatus.SUCCESS,
                data={
                    "file_path": "C:/out/rack.stl",
                    "units": "mm",
                    "document_type": "assembly",
                    "mesh": {
                        "applied": ["all components in one file"],
                        "restored": False,
                    },
                },
            )
        ),
    )

    result = await adapter.export_stl_with_settings("C:/out/rack.stl", {"units": "mm"})

    assert result.data == {
        "file_path": os.path.abspath("C:/out/rack.stl"),
        "units": "mm",
        "document_type": "assembly",
        "mesh": {
            "applied": ["units mm", "all components in one file"],
            "restored": False,
        },
    }


# ---- SaveAs2's arguments ---------------------------------------------------------


def _is_null_dispatch(value: Any) -> bool:
    """``VARIANT(VT_DISPATCH, None)``, or its stand-in without pywin32."""
    try:
        import pythoncom
    except ImportError:
        return value is not None and "null callout" in repr(value)
    return getattr(value, "varianttype", None) == pythoncom.VT_DISPATCH and (
        value.value is None
    )


def _is_byref_long(value: Any) -> bool:
    try:
        import pythoncom
    except ImportError:
        return value == 0
    return getattr(value, "varianttype", None) == pythoncom.VT_BYREF | pythoncom.VT_I4


class _Extension:
    """``IModelDocExtension.SaveAs2``: notes its arguments; ``refuse`` raises first."""

    def __init__(self, refuse: int = 0) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.refuse = refuse

    def SaveAs2(self, path: str, *args: Any) -> bool:  # noqa: N802
        self.calls.append((path, *args))
        if len(self.calls) <= self.refuse:
            raise RuntimeError("(-2147352571, 'Type mismatch.', None, 4)")
        _one_file(path)
        return True


def test_saveas2_gets_the_arguments_its_type_library_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Name, Version, Options, ExportData, prefix text, AddTextAsPrefix, two outs."""
    adapter = _adapter(monkeypatch, _App(), SimpleNamespace())
    ext = _Extension()
    out = str(tmp_path / "bracket.stl")

    assert adapter._save_stl_with_extension(ext, None, out) is True

    [(path, version, options, export_data, text, as_prefix, errors, warnings)] = (
        ext.calls
    )
    assert (path, version, options) == (out, 0, pywin32_adapter._EXPORT_OPTIONS)
    # Not Python None, which marshals as VT_NULL: a null IDispatch.
    assert _is_null_dispatch(export_data)
    assert (text, as_prefix) == ("", False)
    assert _is_byref_long(errors) and _is_byref_long(warnings)


def test_without_export_data_a_refused_call_is_not_repeated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    adapter = _adapter(monkeypatch, _App(), SimpleNamespace())
    ext = _Extension(refuse=1)

    assert adapter._save_stl_with_extension(ext, None, str(tmp_path / "a.stl")) is False
    assert len(ext.calls) == 1  # the same call again would fail the same way


def test_refused_export_data_is_retried_as_a_null(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    adapter = _adapter(monkeypatch, _App(), SimpleNamespace())
    ext = _Extension(refuse=1)
    data = object()

    assert adapter._save_stl_with_extension(ext, data, str(tmp_path / "a.stl"))
    assert ext.calls[0][3] is data  # ExportData, the fourth argument
    assert _is_null_dispatch(ext.calls[1][3])


@pytest.mark.parametrize(("doc_type", "prepared"), [(PART, 1), (ASSEMBLY, 0)])
async def test_an_assembly_is_exported_without_export_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, doc_type: int, prepared: int
) -> None:
    app = _App()
    doc = _Doc(app, doc_type, _one_file)
    adapter = _adapter(monkeypatch, app, doc)
    asked: list[bool] = []
    monkeypatch.setattr(adapter, "_prepare_stl_export_data", lambda: asked.append(True))

    result = await adapter.export_file(str(tmp_path / "out.stl"), "stl")

    assert result.is_success, result.error
    assert len(asked) == prepared
    assert doc.save_as3_calls == 0  # SaveAs2 wrote it
