"""Equation and global-variable tools on the mock (the MMPLA well plate)."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.engineering_data import register_engineering_data_tools


async def _tools(adapter: Any, mock_config: Any) -> dict[str, Any]:
    mcp = FastMCP("equations")
    await register_engineering_data_tools(mcp, adapter, mock_config)
    return {tool.name: tool.fn for tool in await mcp.list_tools()}


@pytest.fixture
async def part(mock_config: Any) -> dict[str, Any]:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_part()
    return await _tools(adapter, mock_config)


async def test_one_variable_drives_two_dimensions(part: dict[str, Any]) -> None:
    added = await part["set_global_variable"](
        {"name": "well_d", "value": 1.2, "units": "mm"}
    )
    first = await part["add_equation"](
        {"dimension": "D1@Sketch1", "expression": "well_d"}
    )
    second = await part["add_equation"](
        {"dimension": "D2@Sketch1", "expression": '"well_d" * 2'}
    )

    assert added["status"] == "success", added
    assert added["data"]["action"] == "added"
    assert first["data"]["equation"]["equation"] == '"D1@Sketch1" = "well_d"'
    assert second["data"]["dimension"]["value"] == pytest.approx(2.4)
    assert second["data"]["value_before"] == 20.0
    assert "now 2.4" in second["message"]

    changed = await part["set_global_variable"]({"name": "well_d", "value": 1.5})

    assert changed["data"]["action"] == "changed"
    assert changed["data"]["previous"] == '"well_d" = 1.2mm'
    assert [d["dimension"]["value"] for d in changed["data"]["driven"]] == [
        pytest.approx(1.5),
        pytest.approx(3.0),
    ]
    assert "drives 2 equation(s)" in changed["message"]

    listed = await part["list_equations"]({})

    assert listed["data"]["count"] == 3
    assert [v["name"] for v in listed["data"]["global_variables"]] == ["well_d"]


async def test_units_and_arithmetic(part: dict[str, Any]) -> None:
    await part["set_global_variable"]({"name": "off", "value": 0.5, "units": "cm"})

    done = await part["add_equation"](
        {"dimension": "D1@Sketch1", "expression": '= -("off" - 2m / 1000) / 2 + 1in'}
    )

    # -(5 - 2) / 2 + 25.4
    assert done["data"]["dimension"]["value"] == pytest.approx(23.9)


@pytest.mark.parametrize(
    ("tool", "arguments", "error_type"),
    [
        ("add_equation", {"dimension": "D1@Sketch1", "expression": '"x"'}, None),
        ("add_equation", {"dimension": "D1@Sketch1", "expression": "1 +"}, None),
        ("add_equation", {"dimension": "D1@Sketch1", "expression": "1 / 0"}, None),
        ("add_equation", {"dimension": "D1@Sketch1", "expression": "2 ** 3"}, None),
        ("add_equation", {"dimension": "D9@Sketch9", "expression": "1"}, "not_found"),
        ("add_equation", {"dimension": "Sketch1", "expression": "1"}, None),
        ("set_global_variable", {"name": "D1@Sketch1", "value": 1}, None),
        ("set_global_variable", {"name": "w", "value": 1, "units": "yd"}, None),
        ("delete_equation", {}, None),
        ("delete_equation", {"name": "ghost"}, "not_found"),
    ],
)
async def test_refusals(
    part: dict[str, Any],
    tool: str,
    arguments: dict[str, Any],
    error_type: str | None,
) -> None:
    reply = await part[tool](arguments)

    assert reply["status"] == "error"
    assert reply["error_type"] == (error_type or "invalid_input"), reply


async def test_deleting_a_variable_in_use(part: dict[str, Any]) -> None:
    await part["set_global_variable"]({"name": "w", "value": 2})
    await part["add_equation"]({"dimension": "D1@Sketch1", "expression": "w"})

    refused = await part["delete_equation"]({"name": "w"})
    forced = await part["delete_equation"]({"name": "w", "force": True})

    assert refused["error_type"] == "invalid_input"
    assert forced["data"]["left_using_it"] == ['"D1@Sketch1" = "w"']
    assert forced["data"]["count"] == 0
    assert forced["message"] == 'Deleted "w" = 2mm'


async def test_delete_by_index(part: dict[str, Any]) -> None:
    await part["set_global_variable"]({"name": "w", "value": 2})
    await part["set_global_variable"]({"name": "n", "value": 4, "units": ""})

    done = await part["delete_equation"]({"index": 0})

    assert done["data"]["deleted"]["name"] == "w"
    assert [v["name"] for v in done["data"]["global_variables"]] == ["n"]


async def test_no_part_open(mock_config: Any) -> None:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    tools = await _tools(adapter, mock_config)

    reply = await tools["list_equations"]()

    assert reply["status"] == "error"
