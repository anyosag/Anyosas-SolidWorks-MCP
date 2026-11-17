"""``set_component_transform`` with ``turn_deg`` about an axis through a pivot."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.modeling import (
    SetComponentTransformInput,
    register_modeling_tools,
)


@pytest.fixture
async def tools(mock_config: Any) -> tuple[MockSolidWorksAdapter, dict[str, Any]]:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_assembly()
    mcp = FastMCP("component-turn")
    await register_modeling_tools(mcp, adapter, mock_config)
    tool = {t.name: t.fn for t in await mcp.list_tools()}
    await tool["insert_component"]({"file_path": "C:/lid.sldprt"})
    return adapter, tool


async def test_a_lid_opened_about_its_hinge(tools) -> None:
    _adapter, tool = tools

    reply = await tool["set_component_transform"](
        {
            "component": "component-1",
            "origin_mm": [100, 0, 0],
            "turn_deg": 90,
            "turn_axis": "z",
            "turn_pivot_mm": [50, 0, 0],
        }
    )

    assert reply["status"] == "success", reply
    assert reply["transform"]["origin_mm"] == pytest.approx([50.0, 50.0, 0.0])
    assert reply["transform"]["rotation"][0] == pytest.approx([0.0, 1.0, 0.0])


async def test_the_turn_reaches_the_adapter_as_a_unit_vector(tools) -> None:
    adapter, tool = tools
    mock = AsyncMock(
        return_value=AdapterResult(status=AdapterResultStatus.SUCCESS, data={})
    )
    adapter.set_component_transform = mock  # type: ignore[method-assign]

    await tool["set_component_transform"](
        {"component": "component-1", "turn_deg": -30, "turn_axis": [0, 2, 0]}
    )

    mock.assert_awaited_once_with(
        "component-1", None, None, None, [0.0, 1.0, 0.0], None, -30.0
    )


async def test_an_axis_without_a_turn_is_no_turn(tools) -> None:
    adapter, tool = tools
    mock = AsyncMock(
        return_value=AdapterResult(status=AdapterResultStatus.SUCCESS, data={})
    )
    adapter.set_component_transform = mock  # type: ignore[method-assign]

    await tool["set_component_transform"](
        {"component": "component-1", "origin_mm": [1, 2, 3], "turn_axis": "x"}
    )

    mock.assert_awaited_once_with("component-1", [1, 2, 3], None, None)


@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ({"turn_deg": 10}, "turn_deg needs turn_axis"),
        ({"turn_deg": 10, "turn_axis": [0, 0, 0]}, "zero vector"),
        ({"turn_deg": 10, "turn_axis": "up"}, "not 'up'"),
    ],
)
def test_a_turn_that_cannot_be_made(payload: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        SetComponentTransformInput(component="lid-1", **payload)
