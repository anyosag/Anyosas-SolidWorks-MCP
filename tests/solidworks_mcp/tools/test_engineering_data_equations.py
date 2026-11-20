"""The equation tools on the mock adapter (``parametric-equations``)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.adapters.solidworks.rebuild_diagnostics import RawRebuildIssue
from solidworks_mcp.tools.engineering_data import (
    DeleteEquationInput,
    SetEquationsInput,
    register_engineering_data_tools,
)
from solidworks_mcp.tools.modeling import register_modeling_tools


async def _tools(adapter: Any, mock_config: Any) -> dict[str, Any]:
    mcp = FastMCP("equations")
    await register_engineering_data_tools(mcp, adapter, mock_config)
    await register_modeling_tools(mcp, adapter, mock_config)
    return {tool.name: tool.fn for tool in await mcp.list_tools()}


@pytest.fixture
async def mock() -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_part()
    return adapter


@pytest.fixture
async def part(mock: MockSolidWorksAdapter, mock_config: Any) -> dict[str, Any]:
    return await _tools(mock, mock_config)


def test_set_equations_input_takes_one_string() -> None:
    assert SetEquationsInput.model_validate({"equation": '"W" = 1'}).equations == [
        '"W" = 1'
    ]
    assert SetEquationsInput.model_validate({"equations": '"W" = 1'}).equations == [
        '"W" = 1'
    ]
    assert DeleteEquationInput.model_validate({"equation": 2}).equation == 2


async def test_set_list_and_delete(part: dict[str, Any]) -> None:
    done = await part["set_equations"](
        {"equations": ['"W" = 45', '"D1@Sketch1" = "W"']}
    )
    listed = await part["list_equations"]({})
    deleted = await part["delete_equation"]({"equation": "W"})

    assert done["status"] == "success", done
    assert done["message"] == "Set 2 equation(s)"
    assert [e["replaced"] for e in done["data"]["equations"]] == [False, False]
    assert done["data"]["failing_features"] == []
    assert done["data"]["measured_change"]["volume_delta_mm3"] is None
    assert "note" in done["data"]["measured_change"]
    assert "fail to rebuild" not in done.get("warning", "")
    assert listed["message"] == "Listed 2 equation(s)"
    assert listed["data"]["automatic_solve_order"] is True
    assert deleted["message"] == 'Deleted "W" = 45'
    assert deleted["data"]["count"] == 1
    assert "now broken" in deleted["warning"]


async def test_a_failing_feature_is_a_success_with_a_warning(
    mock: MockSolidWorksAdapter, part: dict[str, Any]
) -> None:
    mock.injected_rebuild_issues.append(
        RawRebuildIssue(feature="Cut-Extrude1", type="ICE", code=2, is_warning=False)
    )

    done = await part["set_equations"]({"equation": '"D1@Boss-Extrude1" = 500'})

    assert done["status"] == "success", done
    assert "applied" in done["warning"]
    assert "Cut-Extrude1" in done["warning"]


@pytest.mark.parametrize(
    ("tool", "arguments", "error_type", "says"),
    [
        ("set_equations", {"equations": ["W = 40"]}, "invalid_input", '"W" = 40'),
        ("set_equations", {"equations": []}, "invalid_input", None),
        (
            "set_equations",
            {"equations": ['"W" = 1', '"w" = 2']},
            "invalid_input",
            "both set",
        ),
        (
            "set_equations",
            {"equations": ['"A" = 1', '"B" = __refuse__']},
            "invalid_input",
            '"B" = __refuse__',
        ),
        ("delete_equation", {"equation": "Q"}, "not_found", "the equations are"),
        ("delete_equation", {}, "invalid_input", None),
    ],
)
async def test_refusals(
    part: dict[str, Any],
    tool: str,
    arguments: dict[str, Any],
    error_type: str,
    says: str | None,
) -> None:
    reply = await part[tool](arguments)

    assert reply["status"] == "error"
    assert reply["error_type"] == error_type, reply
    if says:
        assert says in reply["message"]


async def test_a_bad_left_side_reads_nothing(mock_config: Any) -> None:
    calls: list[str] = []

    async def record(*_args: Any) -> AdapterResult[Any]:
        calls.append("called")
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data={})

    adapter = SimpleNamespace(set_equations=record, get_model_geometry_summary=record)
    tools = await _tools(adapter, mock_config)

    reply = await tools["set_equations"]({"equations": ["W = 40"]})

    assert reply["error_type"] == "invalid_input"
    assert calls == []


async def test_driven_dimensions(part: dict[str, Any]) -> None:
    await part["set_equations"]({"equations": ['"W" = 45', '"D1@Sketch1" = "W"']})

    listed = await part["list_model_dimensions"]({})
    refused = await part["set_dimension"]({"name": "D1@Sketch1", "value": 30})

    driven = {d["full_name"]: d["driven_by"] for d in listed["data"]}
    assert driven["D1@Sketch1"] == '"D1@Sketch1" = "W"'
    assert driven["D2@Sketch1"] is None
    assert refused["error_type"] == "invalid_input"
    assert '"D1@Sketch1" = "W"' in refused["message"]
    assert "set_equations" in refused["message"]


async def test_no_part_open(mock_config: Any) -> None:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    tools = await _tools(adapter, mock_config)

    reply = await tools["list_equations"]()

    assert reply["status"] == "error"
    assert reply["error_type"] == "invalid_input"
    assert "part or an assembly" in reply["message"]
