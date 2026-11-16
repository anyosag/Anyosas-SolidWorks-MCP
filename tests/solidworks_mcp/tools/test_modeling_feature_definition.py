"""``get_feature_definition`` reads a feature's parameters without editing it."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import ExtrusionParameters
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.modeling import register_modeling_tools


async def _tool(adapter: Any, config: Any) -> Any:
    mcp = FastMCP("definition")
    await register_modeling_tools(mcp, adapter, config)
    return next(
        t.fn for t in await mcp.list_tools() if t.name == "get_feature_definition"
    )


@pytest.fixture
async def part() -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter(
        {"mock_model_delay": 0, "mock_feature_delay": 0, "mock_sketch_delay": 0}
    )
    await adapter.connect()
    await adapter.create_part()
    return adapter


async def test_an_extrude_is_read(part, mock_config) -> None:
    await part.create_sketch("Top")
    await part.exit_sketch()
    await part.create_extrusion(ExtrusionParameters(depth=8.0))
    read = await _tool(part, mock_config)

    result = await read({"name": "Boss-Extrude1"})

    assert result["status"] == "success"
    assert result["message"] == (
        "Read Boss-Extrude1 (Extrusion): 4 parameter(s), 1 dimension(s)"
    )
    assert result["data"]["dimensions"][0]["name"] == "D1@Boss-Extrude1"
    assert result["data"]["parameters"]["direction1"]["depth_mm"] == 8.0


async def test_a_missing_feature_is_not_found(part, mock_config) -> None:
    read = await _tool(part, mock_config)

    result = await read({"name": "Cut-Extrude9"})

    assert result["error_type"] == "not_found"
    assert result["message"].startswith("Failed to read Cut-Extrude9:")


async def test_a_blank_name_is_invalid(part, mock_config) -> None:
    read = await _tool(part, mock_config)

    result = await read({"name": "  "})

    assert result["error_type"] == "invalid_input"


async def test_an_adapter_without_it_is_not_supported(mock_config) -> None:
    read = await _tool(SimpleNamespace(), mock_config)

    result = await read({"name": "Boss-Extrude1"})

    assert result["error_type"] == "not_supported"
