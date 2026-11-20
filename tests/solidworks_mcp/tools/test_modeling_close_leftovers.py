"""close_model's close_leftovers: parts a closed assembly left loaded."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.tools.modeling import CloseModelInput, register_modeling_tools


def _result(status: AdapterResultStatus, data: Any = None) -> AdapterResult[Any]:
    return AdapterResult(status=status, data=data, execution_time=0.5)


LEFTOVERS = {
    "closed": [{"title": "Comb.SLDPRT", "path": "C:/d/Comb.SLDPRT", "lock_file": None}],
    "still_loaded": [{"title": "Holder.SLDPRT", "path": "C:/d/Holder.SLDPRT"}],
    "kept_modified": [{"title": "Stand.SLDPRT", "path": "C:/d/Stand.SLDPRT"}],
}


async def _close(adapter: Any) -> Any:
    mcp = FastMCP("test")
    await register_modeling_tools(mcp, adapter, {})
    return next(t.fn for t in await mcp.list_tools() if t.name == "close_model")


def _adapter(close_status: AdapterResultStatus) -> SimpleNamespace:
    return SimpleNamespace(
        close_model=AsyncMock(return_value=_result(close_status)),
        close_leftover_documents=AsyncMock(
            return_value=_result(AdapterResultStatus.SUCCESS, LEFTOVERS)
        ),
    )


def test_keep_active_needs_close_leftovers_and_no_save() -> None:
    with pytest.raises(ValueError, match="keep_active needs close_leftovers"):
        CloseModelInput(keep_active=True)
    with pytest.raises(ValueError, match="save does nothing"):
        CloseModelInput(keep_active=True, close_leftovers=True, save=True)


async def test_close_the_active_document_and_the_leftovers() -> None:
    adapter = _adapter(AdapterResultStatus.SUCCESS)
    close = await _close(adapter)

    reply = await close(CloseModelInput(close_leftovers=True))

    adapter.close_model.assert_awaited_once_with(False, False, list_documents=True)
    assert reply["status"] == "success"
    assert reply["message"] == (
        "Model closed successfully; closed 1 leftover document(s)"
    )
    assert reply["leftovers"] == LEFTOVERS
    assert reply["execution_time"] == 1.0
    assert "Stand.SLDPRT" in reply["warning"]
    assert "unsaved changes" in reply["warning"]
    assert "Holder.SLDPRT" in reply["warning"]


async def test_keep_active_closes_only_the_leftovers() -> None:
    adapter = _adapter(AdapterResultStatus.SUCCESS)
    adapter.close_leftover_documents.return_value = _result(
        AdapterResultStatus.SUCCESS,
        {"closed": [], "still_loaded": [], "kept_modified": []},
    )
    close = await _close(adapter)

    reply = await close(CloseModelInput(close_leftovers=True, keep_active=True))

    adapter.close_model.assert_not_awaited()
    assert reply["message"] == "closed 0 leftover document(s)"
    assert reply["saved"] is False
    assert "warning" not in reply


async def test_nothing_active_still_closes_the_leftovers() -> None:
    adapter = _adapter(AdapterResultStatus.WARNING)
    close = await _close(adapter)

    with_leftovers = await close(CloseModelInput(close_leftovers=True))
    without = await close(CloseModelInput())

    assert with_leftovers["status"] == "success"
    assert with_leftovers["message"] == "closed 1 leftover document(s)"
    assert without["status"] == "error"


async def test_close_leftovers_needs_the_capability() -> None:
    adapter = SimpleNamespace(
        close_model=AsyncMock(return_value=_result(AdapterResultStatus.SUCCESS))
    )
    close = await _close(adapter)

    reply = await close(CloseModelInput(close_leftovers=True))

    assert reply["status"] == "error"
    assert reply["error_type"] == "not_supported"
