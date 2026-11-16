"""What the adapter leaves selected: nothing after a feature, nothing in an export.

The Lid build (SolidWorks 2026 SP4.1): ``add_chamfer`` left its chamfer face
selected, and the next ``export_stl`` wrote only that face, 592 triangles
spanning y 1.0-1.4 mm, and reported success. With the selection cleared the
same export held 18,738 triangles, 91.6 x 4.7 x 91.6 mm.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters import pywin32_adapter
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import window as window_module

from .solidworks.test_stl_settings import _App


@pytest.fixture(autouse=True)
def _no_keepers() -> Iterator[None]:
    yield
    for keeper in list(window_module._KEEPERS.values()):
        keeper.stop()
    window_module._KEEPERS.clear()


class _Doc:
    """A part whose exports write the file and note whether it was selected."""

    def __init__(self, clears: bool = True) -> None:
        self.selected = True  # the chamfer face add_chamfer left behind
        self.clears = clears
        self.events: list[str] = []
        self.selected_during_export: list[bool] = []
        self.Extension = SimpleNamespace(SaveAs2=self._save_as2)

    def ClearSelection2(self, _all: bool) -> bool:  # noqa: N802 - COM name
        self.events.append("ClearSelection2")
        if not self.clears:
            raise RuntimeError("RPC server is unavailable")
        self.selected = False
        return True

    def GetType(self) -> int:  # noqa: N802 - COM name
        return 1

    def ResolveAllLightweightComponents(self, _now: bool) -> bool:  # noqa: N802
        return True

    def _save_as2(self, path: str, *_args: Any) -> bool:
        self.events.append("SaveAs2")
        self.selected_during_export.append(self.selected)
        Path(path).write_bytes(b"solid lid\nendsolid lid\n")
        return True

    def SaveAs3(self, path: str, *_args: Any) -> bool:  # noqa: N802 - COM name
        self.events.append("SaveAs3")
        self.selected_during_export.append(self.selected)
        Path(path).write_bytes(b"ISO-10303-21;")
        return True


def _adapter(monkeypatch: pytest.MonkeyPatch, doc: Any) -> PyWin32Adapter:
    monkeypatch.setattr(pywin32_adapter, "PYWIN32_AVAILABLE", True)
    monkeypatch.setattr(pywin32_adapter.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        pywin32_adapter, "pywintypes", SimpleNamespace(com_error=RuntimeError)
    )
    monkeypatch.setenv("SOLIDWORKS_MCP_DIALOG_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("SOLIDWORKS_MCP_KEEP_MINIMIZED", "0")
    made = PyWin32Adapter({})
    made.swApp = _App()
    made.currentModel = doc
    return made


# ---- exports ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "fmt", "writer"),
    [("lid.stl", "stl", "SaveAs2"), ("lid.step", "step", "SaveAs3")],
)
async def test_an_export_clears_the_selection_before_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, fmt: str, writer: str
) -> None:
    doc = _Doc()

    result = await _adapter(monkeypatch, doc).export_file(str(tmp_path / name), fmt)

    assert result.is_success, result.error
    assert doc.events == ["ClearSelection2", writer]
    assert doc.selected_during_export == [False]


async def test_a_selection_that_cannot_be_cleared_does_not_stop_the_export(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc = _Doc(clears=False)

    result = await _adapter(monkeypatch, doc).export_file(
        str(tmp_path / "lid.stl"), "stl"
    )

    assert result.is_success, result.error
    assert doc.events == ["ClearSelection2", "SaveAs2"]


# ---- feature operations ---------------------------------------------------------


def _fails() -> None:
    raise RuntimeError("Failed to create chamfer")


@pytest.mark.parametrize(
    "operation", sorted(pywin32_adapter._SELECTION_CLEARING_OPERATIONS)
)
def test_a_feature_operation_clears_the_selection_when_it_ends(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    doc = _Doc()
    adapter = _adapter(monkeypatch, doc)

    result = adapter._handle_com_operation(
        operation, lambda: doc.events.append("feature") or "Chamfer1"
    )

    assert result.is_success, result.error
    assert doc.events == ["feature", "ClearSelection2"]
    assert doc.selected is False


def test_a_failed_feature_operation_clears_the_selection_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = _Doc()

    result = _adapter(monkeypatch, doc)._handle_com_operation("add_chamfer", _fails)

    assert result.is_error
    assert "Failed to create chamfer" in (result.error or "")
    assert doc.events == ["ClearSelection2"]


def test_a_failed_clear_keeps_the_operation_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = _Doc(clears=False)

    result = _adapter(monkeypatch, doc)._handle_com_operation(
        "add_fillet", lambda: "Fillet1"
    )

    assert result.is_success
    assert result.data == "Fillet1"


@pytest.mark.parametrize(
    "operation",
    ["select_edges", "select_face_edges", "select_feature", "exit_sketch", "add_line"],
)
def test_selecting_and_sketch_operations_keep_their_selection(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    doc = _Doc()

    _adapter(monkeypatch, doc)._handle_com_operation(operation, lambda: True)

    assert doc.events == []
    assert doc.selected is True


def test_only_the_outermost_operation_clears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A feature operation run inside another keeps the selection the outer
    one is still building on; the outer one clears once, at its end."""
    doc = _Doc()
    adapter = _adapter(monkeypatch, doc)

    def _outer() -> str:
        adapter._handle_com_operation("add_fillet", lambda: "Fillet1")
        doc.events.append(f"inner done, selected={doc.selected}")
        return "Mirror1"

    adapter._handle_com_operation("mirror_feature", _outer)

    assert doc.events == ["inner done, selected=True", "ClearSelection2"]


def test_an_operation_refused_before_it_ran_leaves_the_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refused from another thread, nothing ran; the user's selection stays."""
    doc = _Doc()
    adapter = _adapter(monkeypatch, doc)
    adapter._com_thread_id = threading.get_ident() + 1

    result = adapter._handle_com_operation("add_chamfer", lambda: "Chamfer1")

    assert result.is_error
    assert doc.events == []


def test_no_model_is_nothing_to_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter(monkeypatch, _Doc())
    adapter.currentModel = None

    assert adapter._handle_com_operation("add_chamfer", lambda: 1).is_success
