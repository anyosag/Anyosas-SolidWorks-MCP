"""``get_sketch`` reads a sketch without opening it for editing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.sketching import register_sketching_tools


async def _tool(adapter: Any, config: Any) -> Any:
    mcp = FastMCP("sketch")
    await register_sketching_tools(mcp, adapter, config)
    return next(t.fn for t in await mcp.list_tools() if t.name == "get_sketch")


@pytest.fixture
async def part() -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({"mock_model_delay": 0, "mock_sketch_delay": 0})
    await adapter.connect()
    await adapter.create_part()
    return adapter


async def test_a_sketch_is_read_with_its_plane(part, mock_config) -> None:
    await part.create_sketch("Front")
    await part.exit_sketch()
    read = await _tool(part, mock_config)

    result = await read({"name": "Sketch1"})

    assert result["status"] == "success"
    assert result["message"] == "Sketch1: 0 segment(s) on Front Plane"
    assert result["sketch"]["name"] == "Sketch1"
    assert "sketch" not in result["sketch"]
    assert result["sketch"]["plane"] == "Front Plane"


async def test_a_sketch_on_a_face_says_no_plane(mock_config) -> None:
    data = {"sketch": "Sketch2", "plane": None, "segments": [{"type": "text"}]}
    stub = SimpleNamespace(
        get_sketch=AsyncMock(
            return_value=AdapterResult(status=AdapterResultStatus.SUCCESS, data=data)
        )
    )
    read = await _tool(stub, mock_config)

    result = await read({"name": "Sketch2"})

    assert result["message"] == "Sketch2: 1 segment(s)"


async def test_a_feature_that_is_not_a_sketch_is_refused(part, mock_config) -> None:
    read = await _tool(part, mock_config)

    result = await read({"name": "Top Plane"})

    assert result["status"] == "error"
    assert "is a RefPlane, not a sketch" in result["message"]


async def test_an_adapter_without_it_is_not_supported(mock_config) -> None:
    read = await _tool(SimpleNamespace(), mock_config)

    result = await read({"name": "Sketch1"})

    assert result["error_type"] == "not_supported"


async def test_a_blank_name_is_invalid(part, mock_config) -> None:
    read = await _tool(part, mock_config)

    assert (await read({"name": ""}))["error_type"] == "invalid_input"
