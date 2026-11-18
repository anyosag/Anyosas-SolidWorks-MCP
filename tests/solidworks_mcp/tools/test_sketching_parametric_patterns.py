"""``sketch_circular_pattern`` and ``sketch_linear_pattern`` name their dimensions."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.modeling import register_modeling_tools
from solidworks_mcp.tools.sketching import register_sketching_tools


@pytest.fixture
async def sketch(mock_config: Any) -> dict[str, Any]:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_part()
    mcp = FastMCP("sketch-patterns")
    await register_sketching_tools(mcp, adapter, mock_config)
    await register_modeling_tools(mcp, adapter, mock_config)
    tools = {tool.name: tool.fn for tool in await mcp.list_tools()}
    await tools["create_sketch"]({"plane": "Top"})
    return tools


async def _circle(tools: dict[str, Any]) -> str:
    made = await tools["add_circle"]({"center_x": 30, "center_y": 0, "radius": 1})
    return made["circle"]["id"]


async def test_a_circular_pattern_about_a_centre_names_its_dimensions(
    sketch: dict[str, Any],
) -> None:
    seed = await _circle(sketch)

    reply = await sketch["sketch_circular_pattern"](
        {"entities": [seed], "count": 8, "center_x": 10, "center_y": 0}
    )

    assert reply["status"] == "success", reply
    pattern = reply["pattern"]
    assert pattern["center"] == {"x": 10.0, "y": 0.0}
    assert pattern["parametric"] is True
    roles = {d["role"]: d for d in pattern["dimensions"]}
    assert roles["count"]["value"] == 8.0
    assert roles["angle"]["units"] == "deg"
    assert "about (10, 0) mm" in reply["message"]

    changed = await sketch["set_dimension"](
        {"name": roles["count"]["name"], "value": 12}
    )
    assert changed["dimension_update"]["new_value"] == 12


async def test_plain_copies_have_no_dimensions(sketch: dict[str, Any]) -> None:
    seed = await _circle(sketch)

    reply = await sketch["sketch_circular_pattern"](
        {"entities": [seed], "add_dimensions": False}
    )

    assert reply["pattern"]["parametric"] is False
    assert reply["pattern"]["dimensions"] == []


async def test_a_linear_pattern_names_its_spacing_and_count(
    sketch: dict[str, Any],
) -> None:
    seed = await _circle(sketch)

    reply = await sketch["sketch_linear_pattern"](
        {"entities": [seed], "spacing": 4.5, "count": 12}
    )

    assert reply["status"] == "success", reply
    assert [(d["role"], d["value"]) for d in reply["pattern"]["dimensions"]] == [
        ("spacing", 4.5),
        ("count", 12.0),
    ]


@pytest.mark.parametrize(
    ("tool", "arguments", "field"),
    [
        ("sketch_circular_pattern", {"entities": []}, "entities"),
        ("sketch_circular_pattern", {"entities": ["C"], "count": 1}, "count"),
        ("sketch_circular_pattern", {"entities": ["C"], "angle": 400}, "angle"),
        ("sketch_linear_pattern", {"entities": ["C"], "spacing": 0}, "spacing"),
    ],
)
async def test_bad_patterns_are_refused_before_the_adapter(
    sketch: dict[str, Any], tool: str, arguments: dict[str, Any], field: str
) -> None:
    reply = await sketch[tool](arguments)

    assert reply["status"] == "error"
    assert reply["error_type"] == "invalid_input"
    assert field in reply["message"]
