"""``open_model`` with ``read_only``: opened so nothing is saved over the file."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import (
    AdapterResult,
    AdapterResultStatus,
    SolidWorksModel,
)
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.modeling import register_modeling_tools


async def _open_tool(adapter: Any, config: Any) -> Any:
    mcp = FastMCP("open")
    await register_modeling_tools(mcp, adapter, config)
    return next(t.fn for t in await mcp.list_tools() if t.name == "open_model")


@pytest.fixture
async def adapter() -> MockSolidWorksAdapter:
    mock = MockSolidWorksAdapter({"mock_model_delay": 0})
    await mock.connect()
    return mock


async def test_read_only_opens_through_its_own_call(adapter, mock_config) -> None:
    open_model = await _open_tool(adapter, mock_config)

    result = await open_model(
        {"file_path": "C:/parts/bracket.sldprt", "read_only": True}
    )

    assert result["status"] == "success"
    assert result["message"] == "Opened Part: bracket.sldprt (read-only)"
    assert result["model"]["read_only"] is True
    assert "warning" not in result


async def test_a_document_already_open_for_editing_is_a_warning(
    adapter, mock_config
) -> None:
    open_model = await _open_tool(adapter, mock_config)
    await open_model({"file_path": "C:/parts/bracket.sldprt"})

    result = await open_model(
        {"file_path": "C:/parts/bracket.sldprt", "read_only": True}
    )

    assert result["status"] == "success"
    assert result["model"]["read_only"] is False
    assert result["warning"].startswith(
        "bracket.sldprt was already open in SolidWorks for editing"
    )


async def test_a_plain_open_says_it_is_writable(adapter, mock_config) -> None:
    open_model = await _open_tool(adapter, mock_config)

    result = await open_model({"file_path": "C:/parts/bracket.sldprt"})

    assert result["message"] == "Opened Part: bracket.sldprt"
    assert result["model"]["read_only"] is False


async def test_a_file_the_adapter_opened_read_only_says_so(mock_config) -> None:
    model = SolidWorksModel(
        path="C:/x.sldprt",
        name="x.sldprt",
        type="Part",
        is_active=True,
        properties={"read_only": True},
    )
    stub = SimpleNamespace(
        open_model=AsyncMock(
            return_value=AdapterResult(status=AdapterResultStatus.SUCCESS, data=model)
        )
    )
    open_model = await _open_tool(stub, mock_config)

    result = await open_model({"file_path": "C:/x.sldprt"})

    assert result["message"] == "Opened Part: x.sldprt (read-only)"


async def test_an_adapter_without_it_is_not_supported(mock_config) -> None:
    stub = SimpleNamespace(open_model=AsyncMock())
    open_model = await _open_tool(stub, mock_config)

    result = await open_model({"file_path": "C:/x.sldprt", "read_only": True})

    assert result["error_type"] == "not_supported"
    stub.open_model.assert_not_awaited()
