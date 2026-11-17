"""Axes and planes through any point, a pattern about any axis, and ``regions``.

The Dipper's cages: a vertical axis through a cell centre at (20, 0, 10),
planes square to (sin 30, 0, cos 30) through it, and one cut patterned six
times about the axis instead of 21 planes, sketches and cuts.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import (
    AdapterResult,
    AdapterResultStatus,
    SolidWorksFeature,
)
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.modeling import (
    CreateAxisInput,
    CreateReferencePlaneInput,
    PatternCircularInput,
    register_modeling_tools,
)

_S, _C = math.sin(math.radians(30)), math.cos(math.radians(30))


def _ok(data: Any) -> AdapterResult:
    return AdapterResult(status=AdapterResultStatus.SUCCESS, data=data)


async def _tools(adapter: Any, config: Any) -> dict[str, Any]:
    mcp = FastMCP("reference geometry")
    await register_modeling_tools(mcp, adapter, config)
    return {tool.name: tool.fn for tool in await mcp.list_tools()}


@pytest.fixture
async def part() -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_part()
    return adapter


# --- create_axis


async def test_an_axis_through_a_cell_centre(part, mock_config) -> None:
    tools = await _tools(part, mock_config)

    result = await tools["create_axis"]({"point": [20, 0, 10], "direction": [0, 3, 0]})

    assert result["status"] == "success", result
    assert result["axis"]["name"] == "Axis1"
    assert result["axis"]["second_point_mm"] == [20.0, 10.0, 10.0]
    assert result["message"] == (
        "Created Axis1 through [20.0, 0.0, 10.0] mm along [0.0, 1.0, 0.0]"
    )
    assert "warning" not in result


async def test_an_axis_through_two_points(part, mock_config) -> None:
    tools = await _tools(part, mock_config)

    result = await tools["create_axis"](
        {"point": [0, 0, 0], "second_point": [0, 0, -4]}
    )

    assert result["axis"]["direction"] == [0.0, 0.0, -1.0]


async def test_an_axis_read_back_off_the_line_warns(mock_config) -> None:
    data = {"name": "Axis2", "read_back": {"on_line": False, "start_mm": [0, 0, 0]}}
    stub = SimpleNamespace(create_axis_through=AsyncMock(return_value=_ok(data)))
    tools = await _tools(stub, mock_config)

    result = await tools["create_axis"]({"point": [0, 0, 0], "direction": [1, 0, 0]})

    assert result["warning"].startswith("The axis SolidWorks made doesn't run along")
    stub.create_axis_through.assert_awaited_once_with([0.0, 0.0, 0.0], [10.0, 0.0, 0.0])


async def test_an_axis_needs_the_adapter_to_make_it(mock_config) -> None:
    tools = await _tools(SimpleNamespace(), mock_config)

    result = await tools["create_axis"]({"point": [0, 0, 0], "direction": [1, 0, 0]})

    assert result["error_type"] == "not_supported"


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({}, "reference is required, or point"),
        ({"reference": "x", "direction": [1, 0, 0]}, "go with point"),
        ({"reference": "x", "point": [0, 0, 0]}, "not both"),
        ({"point": [0, 0, 0]}, "give direction or second_point"),
        (
            {"point": [0, 0, 0], "direction": [1, 0, 0], "second_point": [1, 0, 0]},
            "give direction or second_point",
        ),
        ({"point": [0, 0, 0], "direction": [0, 0, 0]}, "has no length"),
        ({"point": [0, 0, 0], "second_point": [0, 0, 0]}, "same point"),
        ({"point": [0, 0], "direction": [1, 0, 0]}, "three numbers"),
    ],
)
def test_axis_inputs_are_checked(data: dict[str, Any], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        CreateAxisInput(**data)


# --- create_reference_plane through a point


async def test_a_plane_through_a_point(part, mock_config) -> None:
    tools = await _tools(part, mock_config)

    result = await tools["create_reference_plane"](
        {"point": [20, 0, 10], "normal": [_S, 0, _C]}
    )

    assert result["status"] == "success", result
    assert result["plane"]["name"] == "Plane1"
    assert result["plane"]["axes"]["origin_mm"] == [20.0, 0.0, 10.0]
    assert "warning" not in result


async def test_a_reversed_or_missed_plane_warns(mock_config) -> None:
    data = {
        "name": "Plane3",
        "normal_matches": False,
        "through_point": False,
        "axes": {"normal": [0.0, 0.0, -1.0]},
    }
    stub = SimpleNamespace(create_plane_through=AsyncMock(return_value=_ok(data)))
    tools = await _tools(stub, mock_config)

    result = await tools["create_reference_plane"](
        {"point": [0, 0, 0], "normal": [0, 0, 1]}
    )

    assert "normal the other way, to [0.0, 0.0, -1.0]" in result["warning"]
    assert "doesn't pass through point" in result["warning"]


async def test_a_plane_through_a_point_needs_the_adapter(mock_config) -> None:
    tools = await _tools(SimpleNamespace(), mock_config)

    result = await tools["create_reference_plane"](
        {"point": [0, 0, 0], "normal": [0, 0, 1]}
    )

    assert result["error_type"] == "not_supported"


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({"point": [0, 0, 0]}, "needs normal"),
        ({"point": [0, 0, 0], "normal": [0, 0, 1], "offset": 5}, "no offset"),
        (
            {"reference": "Top Plane", "offset": 5, "normal": [0, 0, 1]},
            "goes with point",
        ),
        ({"point": [0, 0, 0], "normal": [0, 0, 0]}, "has no length"),
        ({"point": [0, 0, 0], "face_point": [0, 0, 0]}, "exactly one of"),
    ],
)
def test_plane_inputs_are_checked(data: dict[str, Any], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        CreateReferencePlaneInput(**data)


async def test_an_offset_plane_reports_its_axes(part, mock_config) -> None:
    tools = await _tools(part, mock_config)

    result = await tools["create_reference_plane"](
        {"reference": "Front Plane", "offset": 12}
    )

    assert result["plane"]["axes"]["origin_mm"] == [0.0, 0.0, 12.0]


# --- pattern_circular about any axis


async def test_a_pattern_makes_its_axis_first(part, mock_config) -> None:
    tools = await _tools(part, mock_config)

    result = await tools["pattern_circular"](
        {
            "features": ["Cut-Extrude1"],
            "axis_point": [20, 0, 10],
            "axis_direction": [0, 1, 0],
            "count": 6,
        }
    )

    assert result["status"] == "success", result
    assert result["pattern"]["axis"] == "Axis1"
    assert result["pattern"]["axis_created"] == {
        "name": "Axis1",
        "sketch": "3DSketch1",
        "point_mm": [20.0, 0.0, 10.0],
        "direction": [0.0, 1.0, 0.0],
    }
    assert "around Axis1 (6 instances, 60 degrees apart)" in result["message"]


async def test_a_failed_pattern_removes_the_axis_it_made(mock_config) -> None:
    made = {"name": "Axis5", "sketch": "3DSketch2"}
    stub = SimpleNamespace(
        create_axis_through=AsyncMock(return_value=_ok(made)),
        pattern_circular=AsyncMock(
            return_value=AdapterResult(
                status=AdapterResultStatus.ERROR, error="nothing patterned"
            )
        ),
        delete_feature=AsyncMock(return_value=_ok({})),
    )
    tools = await _tools(stub, mock_config)

    result = await tools["pattern_circular"](
        {
            "features": ["Cut-Extrude1"],
            "axis_point": [0, 0, 0],
            "axis_direction": [0, 0, 2],
            "count": 3,
        }
    )

    assert result["status"] == "error"
    stub.delete_feature.assert_awaited_once_with("3DSketch2")
    assert stub.pattern_circular.await_args.args[1] == "Axis5"
    stub.create_axis_through.assert_awaited_once_with([0.0, 0.0, 0.0], [0.0, 0.0, 10.0])


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({}, "axis is required, or axis_point"),
        ({"axis": "Axis1", "axis_direction": [0, 1, 0]}, "goes with axis_point"),
        ({"axis": "Axis1", "axis_point": [0, 0, 0]}, "not both"),
        ({"axis_point": [0, 0, 0]}, "needs axis_direction"),
        ({"axis_point": [0, 0, 0], "axis_direction": [0, 0, 0]}, "has no length"),
    ],
)
def test_pattern_axis_inputs_are_checked(data: dict[str, Any], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        PatternCircularInput(features=["Cut1"], count=3, **data)


# --- regions


def _feature(**parameters: Any) -> AdapterResult:
    return _ok(
        SolidWorksFeature(name="Boss-Extrude1", type="Extrusion", parameters=parameters)
    )


async def test_regions_reach_the_adapter_and_the_reply(mock_config) -> None:
    note = "Sketch 'Rings': its contours cross ..."
    stub = SimpleNamespace(
        create_extrusion=AsyncMock(
            return_value=_feature(regions="all", regions_note=note)
        ),
        create_cut_extrude=AsyncMock(return_value=_feature(regions="all")),
    )
    tools = await _tools(stub, mock_config)

    boss = await tools["create_extrusion"]({"depth": 5})
    cut = await tools["create_cut_extrude"]({"depth": 5, "regions": "all"})

    assert boss["extrusion"]["regions"] == "all"
    assert boss["warning"] == note
    assert stub.create_cut_extrude.await_args.args[0].regions == "all"
    assert cut["cut_extrude"]["regions"] == "all"
    assert "warning" not in cut


async def test_without_regions_the_reply_has_none(mock_config) -> None:
    stub = SimpleNamespace(create_extrusion=AsyncMock(return_value=_feature()))
    tools = await _tools(stub, mock_config)

    boss = await tools["create_extrusion"]({"depth": 5})

    assert "regions" not in boss["extrusion"]
