"""Fully defining a sketch through the tools: points, checked relations, distances."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.sketching import register_sketching_tools


def _ok(data: Any) -> AdapterResult[Any]:
    return AdapterResult(status=AdapterResultStatus.SUCCESS, data=data)


async def _tools(adapter: Any, config: Any) -> dict[str, Any]:
    mcp = FastMCP("sketch")
    await register_sketching_tools(mcp, adapter, config)
    return {t.name: t.fn for t in await mcp.list_tools()}


async def test_a_relation_reports_what_it_tied_and_how_it_was_checked(
    mock_config,
) -> None:
    added = {
        "id": "Constraint_5",
        "entities": ["Circle_1.center", "Centerline_2"],
        "verified_by": "relation_count",
    }
    stub = SimpleNamespace(add_sketch_constraint=AsyncMock(return_value=_ok(added)))
    tools = await _tools(stub, mock_config)

    result = await tools["add_sketch_constraint"](
        {
            "entity1": "Circle_1",
            "entity2": "Centerline_2",
            "relation_type": "coincident",
        }
    )

    assert result["status"] == "success"
    assert "(tied Circle_1.center and Centerline_2)" in result["message"]
    assert result["constraint"]["id"] == "Constraint_5"
    assert result["constraint"]["entities_used"] == added["entities"]
    assert result["constraint"]["verified_by"] == "relation_count"


async def test_a_dimension_reports_its_name_and_what_it_measures(mock_config) -> None:
    added = {"id": "Dimension_6", "name": "D2@Sketch1", "measured": 12.0}
    stub = SimpleNamespace(add_sketch_dimension=AsyncMock(return_value=_ok(added)))
    tools = await _tools(stub, mock_config)

    result = await tools["add_sketch_dimension"](
        {
            "entity1": "Circle_1",
            "entity2": "Origin",
            "dimension_type": "horizontal",
            "value": 12,
        }
    )

    assert result["status"] == "success"
    assert result["message"] == (
        "Added horizontal dimension of 12 mm to Circle_1 and Origin"
    )
    assert result["dimension"]["name"] == "D2@Sketch1"
    assert result["dimension"]["measured"] == 12.0
    stub.add_sketch_dimension.assert_awaited_once_with(
        "Circle_1", "Origin", "horizontal", 12.0
    )


async def test_an_unknown_dimension_type_is_invalid(mock_config) -> None:
    stub = SimpleNamespace(add_sketch_dimension=AsyncMock())
    tools = await _tools(stub, mock_config)

    result = await tools["add_sketch_dimension"](
        {"entity1": "Line_1", "dimension_type": "aligned", "value": 5}
    )

    assert result["error_type"] == "invalid_input"
    stub.add_sketch_dimension.assert_not_awaited()


async def test_the_mock_takes_points_and_the_origin(mock_config) -> None:
    adapter = MockSolidWorksAdapter({"mock_model_delay": 0, "mock_sketch_delay": 0})
    await adapter.connect()
    await adapter.create_part()
    await adapter.create_sketch("Front")
    circle = (await adapter.add_circle(8.0, 3.0, 0.3)).data
    tools = await _tools(adapter, mock_config)

    tied = await tools["add_sketch_constraint"](
        {
            "entity1": f"{circle}.center",
            "entity2": "Origin",
            "relation_type": "coincident",
        }
    )
    unknown = await tools["add_sketch_constraint"](
        {"entity1": "Arc_9.center", "entity2": "Origin", "relation_type": "coincident"}
    )

    assert tied["status"] == "success", tied
    assert "Unknown sketch entity 'Arc_9.center'" in unknown["message"]


async def test_a_summary_is_asked_for_and_says_how_defined_it_is(mock_config) -> None:
    adapter = MockSolidWorksAdapter({"mock_model_delay": 0, "mock_sketch_delay": 0})
    await adapter.connect()
    await adapter.create_part()
    await adapter.create_sketch("Front")
    await adapter.exit_sketch()
    tools = await _tools(adapter, mock_config)

    result = await tools["get_sketch"]({"name": "Sketch1", "detail": "summary"})

    assert result["status"] == "success"
    assert result["message"] == "Sketch1: 0 segment(s) on Front Plane, fully_defined"
    sketch = result["sketch"]
    assert sketch["detail"] == "summary" and "segments" not in sketch
    assert sketch["relations"] == [] and sketch["circles"] == []


async def test_a_summary_counts_its_segments(mock_config) -> None:
    data = {
        "segment_counts": {"circle": 174, "line": 4},
        "definition_state": "under_defined",
    }
    stub = SimpleNamespace(get_sketch=AsyncMock(return_value=_ok(data)))
    tools = await _tools(stub, mock_config)

    result = await tools["get_sketch"]({"name": "Sketch3", "detail": "summary"})

    assert result["message"] == "Sketch3: 178 segment(s), under_defined"
    stub.get_sketch.assert_awaited_once_with("Sketch3", "summary")
