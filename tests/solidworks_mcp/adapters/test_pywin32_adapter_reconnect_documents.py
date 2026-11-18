# The reconnect fixtures are imported, and pytest passes them by name (F811).
# ruff: noqa: F811
"""After a reconnect the session works on fresh document handles (MMPLA).

On SOLIDWORKS 2026 SP4.1 the server reconnected after SolidWorks restarted,
and the first ``save_as`` answered "No active model" although
``list_open_documents`` listed an active document; the retry with
``document`` failed with a COM "disconnected" error. Now the reconnect adopts
the running SolidWorks' ``ActiveDoc`` as ``currentModel``, ``save_file``
falls back to the active document, and a ``currentModel`` whose object has
disconnected is replaced by a fresh one.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import saving
from solidworks_mcp.tools.error_classifier import ErrorClassifier
from solidworks_mcp.tools.file_management import register_file_management_tools

from .test_pywin32_adapter_reconnect import (  # noqa: F401 - fixtures
    NEW_FRAME,
    NEW_PID,
    OLD_FRAME,
    OLD_PID,
    _App,
    _fresh_keepers,
    adapter,
    connected,
    running,
    user32,
)

RPC_E_DISCONNECTED = -2147417848


def _disconnected() -> RuntimeError:
    return RuntimeError(
        RPC_E_DISCONNECTED, "The object invoked has disconnected from its clients."
    )


class _Extension:
    NeedsRebuild2 = 0


class _Doc:
    """An open part of the running SolidWorks."""

    def __init__(self, title: str, path: str) -> None:
        self.title = title
        self.path = path
        self.Visible = True
        self.Extension = _Extension()

    def GetTitle(self) -> str:  # noqa: N802 - COM name
        return self.title

    def GetPathName(self) -> str:  # noqa: N802
        return self.path

    def GetType(self) -> int:  # noqa: N802
        return 1


class _StaleDoc:
    """A proxy whose object has gone: every call is ``RPC_E_DISCONNECTED``."""

    def GetTitle(self) -> str:  # noqa: N802
        raise _disconnected()

    GetPathName = GetTitle


class _RunningApp(_App):
    """The SolidWorks started again, with documents the user opened."""

    def __init__(self, hwnd: int, docs: list[_Doc]) -> None:
        super().__init__(hwnd)
        self.docs = docs
        self.active: _Doc | None = docs[0] if docs else None
        self.activated: list[str] = []

    @property
    def ActiveDoc(self) -> _Doc | None:  # noqa: N802 - COM name
        return self.active

    def GetDocuments(self) -> list[_Doc]:  # noqa: N802
        return list(self.docs)

    def ActivateDoc3(self, title: str, *_args: Any) -> _Doc | None:  # noqa: N802
        self.activated.append(title)
        self.active = next(d for d in self.docs if d.title == title)
        return self.active


@pytest.fixture
def saved(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """The documents ``save_document`` was asked to save."""
    models: list[Any] = []

    def save_document(_adapter, model, file_path=None, _overwrite=False):
        models.append(model)
        return {"file_path": file_path, "saved_references": [], "warnings": []}

    monkeypatch.setattr(saving, "save_document", save_document)
    return models


def _restart_with(user32: Any, running: dict[str, Any], docs: list[_Doc]) -> Any:
    user32.remove(OLD_FRAME)
    user32.add(NEW_FRAME, NEW_PID, title="SOLIDWORKS")
    running["app"] = app = _RunningApp(NEW_FRAME, docs)
    return app


def _reconnect(adapter: PyWin32Adapter) -> str:
    """The call that finds SolidWorks restarted (it doesn't run)."""
    result = adapter._handle_com_operation("list_features", lambda: [])
    assert ErrorClassifier().classify(str(result.error)).error_type == (
        "solidworks_reconnected"
    )
    return str(result.error)


async def _save_as(adapter: PyWin32Adapter, path: str, **document: str) -> Any:
    mcp = FastMCP("reconnect")
    await register_file_management_tools(mcp, adapter, {})
    tool = next(t.fn for t in await mcp.list_tools() if t.name == "save_as")
    return await tool({"file_path": path}, **document)


def test_the_reconnect_adopts_the_active_document(
    connected: PyWin32Adapter, user32: Any, running: dict[str, Any]
) -> None:
    plate = _Doc("Plate.SLDPRT", "C:/work/Plate.SLDPRT")
    _restart_with(user32, running, [plate])

    error = _reconnect(connected)

    assert connected.currentModel is plate
    assert "Its active document, 'Plate.SLDPRT', is the one the next call" in error
    assert connected._last_active_document == (
        saving.os.path.normcase("C:/work/Plate.SLDPRT"),
        "Plate.SLDPRT",
    )
    assert connected._edge_index is None


def test_a_reconnect_to_an_empty_solidworks_says_so(
    connected: PyWin32Adapter, user32: Any, running: dict[str, Any]
) -> None:
    _restart_with(user32, running, [])

    assert "No document is open in it." in _reconnect(connected)
    assert connected.currentModel is None


async def test_save_as_after_a_reconnect_saves_the_active_document(
    connected: PyWin32Adapter,
    user32: Any,
    running: dict[str, Any],
    saved: list[Any],
    tmp_path: Any,
) -> None:
    plate = _Doc("Plate.SLDPRT", "C:/work/Plate.SLDPRT")
    _restart_with(user32, running, [plate])
    _reconnect(connected)

    reply = await _save_as(connected, str(tmp_path / "copy.SLDPRT"))

    assert reply["status"] == "success", reply
    assert saved == [plate]


async def test_save_as_finds_a_document_opened_after_the_reconnect(
    connected: PyWin32Adapter,
    user32: Any,
    running: dict[str, Any],
    saved: list[Any],
    tmp_path: Any,
) -> None:
    app = _restart_with(user32, running, [])
    _reconnect(connected)
    plate = _Doc("Plate.SLDPRT", "C:/work/Plate.SLDPRT")
    app.docs.append(plate)
    app.active = plate

    reply = await _save_as(connected, str(tmp_path / "copy.SLDPRT"))

    assert reply["status"] == "success", reply
    assert saved == [plate]
    assert connected.currentModel is plate


@pytest.mark.parametrize(
    ("document", "activated"),
    [("Plate.SLDPRT", []), ("C:/work/Lid.SLDPRT", ["Lid.SLDPRT"])],
    ids=["the-active-one", "another"],
)
async def test_save_as_with_document_after_a_reconnect(
    connected: PyWin32Adapter,
    user32: Any,
    running: dict[str, Any],
    saved: list[Any],
    tmp_path: Any,
    document: str,
    activated: list[str],
) -> None:
    plate = _Doc("Plate.SLDPRT", "C:/work/Plate.SLDPRT")
    lid = _Doc("Lid.SLDPRT", "C:/work/Lid.SLDPRT")
    app = _restart_with(user32, running, [plate, lid])
    _reconnect(connected)

    reply = await _save_as(connected, str(tmp_path / "copy.SLDPRT"), document=document)

    assert reply["status"] == "success", reply
    wanted = plate if not activated else lid
    assert saved == [wanted]
    assert app.activated == activated
    assert connected.currentModel is wanted


def test_a_disconnected_model_is_replaced_and_the_call_says_retry(
    connected: PyWin32Adapter, user32: Any
) -> None:
    plate = _Doc("Plate.SLDPRT", "C:/work/Plate.SLDPRT")
    app = _RunningApp(OLD_FRAME, [plate])
    connected.swApp = app
    connected.currentModel = _StaleDoc()

    def fails() -> None:
        raise _disconnected()

    result = connected._handle_com_operation("save_file", fails)

    assert result.is_error
    assert "had disconnected" in str(result.error)
    assert "now works on the active document, 'Plate.SLDPRT'" in str(result.error)
    assert "Retry the call." in str(result.error)
    assert connected.currentModel is plate
    assert connected._sw_process_id == OLD_PID


def test_with_nothing_open_the_message_says_so(connected: PyWin32Adapter) -> None:
    connected.swApp = _RunningApp(OLD_FRAME, [])
    connected.currentModel = _StaleDoc()

    def fails() -> None:
        raise _disconnected()

    result = connected._handle_com_operation("save_file", fails)

    assert "no document, none being open" in str(result.error)
    assert connected.currentModel is None


@pytest.mark.parametrize(
    "case", ["other-error", "no-model", "model-alive"], ids=lambda c: c
)
def test_other_failures_keep_the_model(connected: PyWin32Adapter, case: str) -> None:
    plate = _Doc("Plate.SLDPRT", "C:/work/Plate.SLDPRT")
    connected.swApp = _RunningApp(OLD_FRAME, [plate])
    alive = _Doc("Mine.SLDPRT", "C:/work/Mine.SLDPRT")
    connected.currentModel = None if case == "no-model" else alive

    def fails() -> None:
        if case == "other-error":
            raise RuntimeError(-2147352567, "Exception occurred.")
        raise _disconnected()

    result = connected._handle_com_operation("save_file", fails)

    assert "had disconnected" not in str(result.error)
    assert connected.currentModel is (None if case == "no-model" else alive)
