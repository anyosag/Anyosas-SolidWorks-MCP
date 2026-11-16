"""The IO mixin's list_references and move_document (``references``)."""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import references
from solidworks_mcp.adapters.solidworks.io import SolidWorksIOMixin


class _Harness(SolidWorksIOMixin):
    def __init__(self, connected: bool = True) -> None:
        self._connected = connected
        self.operations: list[str] = []

    def is_connected(self) -> bool:
        return self._connected

    def _handle_com_operation(self, name: str, operation: Any) -> Any:
        self.operations.append(name)
        try:
            return AdapterResult(status=AdapterResultStatus.SUCCESS, data=operation())
        except Exception as exc:  # noqa: BLE001 - mirror the real adapter
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=f"Error in {name}: {exc}"
            )


@pytest.mark.parametrize(
    ("method", "args"),
    [("list_references", ("C:/a.sldprt",)), ("move_document", ("C:/a.sldprt", "C:/b"))],
)
async def test_not_connected_is_said_first(method: str, args: tuple) -> None:
    result = await getattr(_Harness(connected=False), method)(*args)

    assert result.error == "Not connected to SolidWorks"


async def test_each_runs_in_its_own_operation(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def listing(adapter: Any, path: str, traverse: bool) -> dict[str, Any]:
        seen["list"] = (path, traverse)
        return {"file": path}

    def moving(adapter: Any, *args: Any) -> dict[str, Any]:
        seen["move"] = args
        return {"moved": False}

    monkeypatch.setattr(references, "list_references", listing)
    monkeypatch.setattr(references, "move_document", moving)
    harness = _Harness()

    listed = await harness.list_references("C:/a.sldprt", True)
    moved = await harness.move_document("C:/a.sldprt", "C:/b.sldprt", ["C:/c.sldasm"])

    assert listed.data == {"file": "C:/a.sldprt"}
    assert moved.data == {"moved": False}
    assert seen == {
        "list": ("C:/a.sldprt", True),
        "move": ("C:/a.sldprt", "C:/b.sldprt", ["C:/c.sldasm"], None, True),
    }
    assert harness.operations == ["list_references", "move_document"]


async def test_errors_lose_the_operation_prefix(monkeypatch) -> None:
    def refuse(*_a: Any) -> Any:
        raise LookupError("File not found: C:/a.sldprt")

    monkeypatch.setattr(references, "list_references", refuse)

    result = await _Harness().list_references("C:/a.sldprt")

    assert result.error == "File not found: C:/a.sldprt"


def test_they_live_on_the_io_mixin() -> None:
    pytest.importorskip("win32com", reason="pywin32 is Windows-only")
    from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter

    for name in ("list_references", "move_document"):
        owner = next(k.__name__ for k in PyWin32Adapter.__mro__ if name in vars(k))
        assert owner == "SolidWorksIOMixin"


def test_they_choose_their_own_document() -> None:
    pytest.importorskip("win32com", reason="pywin32 is Windows-only")
    from solidworks_mcp.adapters import pywin32_adapter

    assert {"list_references", "move_document"} <= (
        pywin32_adapter._DOCUMENT_CHOOSING_OPERATIONS
    )
    assert "list_references" in pywin32_adapter._READ_ONLY_OPERATIONS
