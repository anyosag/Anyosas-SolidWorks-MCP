"""The drawing tools' ``coordinates: "model"``, and ``plane_axes``.

On the Top Plane sketch x is model +X and sketch y is model -Z, so a model
point (x, 0, z) lands at sketch (x, -z). A plane through (20, 0, 10) square
to (sin 30, 0, cos 30) is one of the Dipper's cage faces.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.sketching import (
    AddArcInput,
    AddLineInput,
    AddOutlinesInput,
    Outline,
    PlaneAxesInput,
    register_sketching_tools,
)

_S, _C = math.sin(math.radians(30)), math.cos(math.radians(30))


async def _tools(adapter: Any, config: Any) -> dict[str, Any]:
    mcp = FastMCP("model coordinates")
    await register_sketching_tools(mcp, adapter, config)
    return {tool.name: tool.fn for tool in await mcp.list_tools()}


@pytest.fixture
async def part() -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_part()
    return adapter


@pytest.fixture
async def top(part: MockSolidWorksAdapter, mock_config: Any) -> dict[str, Any]:
    """The tools, with a sketch open on the Top Plane."""
    await part.create_sketch("Top")
    return await _tools(part, mock_config)


async def test_a_circle_by_its_model_centre(top) -> None:
    result = await top["add_circle"](
        {
            "center_x": 5,
            "center_y": 0,
            "center_z": -7,
            "radius": 2,
            "coordinates": "model",
        }
    )

    assert result["status"] == "success", result
    assert result["circle"]["center"] == {"x": 5.0, "y": 7.0}
    assert result["projection"] == {
        "coordinates": "model",
        "axes": "sketch x = model +X, sketch y = model -Z, normal = model +Y",
        "max_off_plane_mm": 0.0,
    }


async def test_a_point_off_the_plane_is_refused(top) -> None:
    result = await top["add_circle"](
        {
            "center_x": 5,
            "center_y": 2,
            "center_z": -7,
            "radius": 2,
            "coordinates": "model",
        }
    )

    assert result["error_type"] == "invalid_input"
    assert "center [5, 2, -7] mm is 2 mm above the sketch plane" in result["message"]


async def test_lines_centrelines_and_rectangles_by_model_points(top) -> None:
    ends = {"x1": 0, "y1": 0, "z1": 0, "x2": 10, "y2": 0, "z2": -10}
    line = await top["add_line"]({**ends, "coordinates": "model"})
    axis = await top["add_centerline"]({**ends, "coordinates": "model"})
    box = await top["add_rectangle"]({**ends, "coordinates": "model"})

    assert line["line"]["end"] == {"x": 10.0, "y": 10.0}
    assert line["line"]["length"] == pytest.approx(math.sqrt(200))
    assert axis["centerline"]["end_point"] == {"x": 10.0, "y": 10.0}
    assert box["rectangle"]["corner2"] == {"x": 10.0, "y": 10.0}
    assert box["rectangle"]["area"] == pytest.approx(100.0)
    assert {"projection"} <= set(line) & set(axis) & set(box)


async def test_a_rectangle_flat_in_the_sketch_is_refused(top) -> None:
    result = await top["add_rectangle"](
        {"x1": 0, "y1": 0, "z1": 0, "x2": 10, "y2": 0, "z2": 0, "coordinates": "model"}
    )

    assert result["error_type"] == "invalid_input"
    assert "has no length" in result["message"]


async def test_an_arc_by_model_points(top) -> None:
    result = await top["add_arc"](
        {
            "center_x": 0,
            "center_y": 0,
            "center_z": 0,
            "start_x": 5,
            "start_y": 0,
            "start_z": 0,
            "end_x": 0,
            "end_y": 0,
            "end_z": -5,
            "coordinates": "model",
        }
    )

    assert result["arc"]["end_point"] == {"x": 0.0, "y": 5.0}
    assert result["arc"]["sweep_deg"] == 90.0
    assert result["projection"]["coordinates"] == "model"


async def test_outlines_by_model_points_with_an_arc(top) -> None:
    result = await top["add_outlines"](
        {
            "coordinates": "model",
            "outlines": [
                {"points": [[0, 0, 0], [10, 0, 0], [10, 0, -10]]},
                {
                    "start": [20, 0, 0],
                    "segments": [
                        {"to": [30, 0, 0]},
                        {"type": "arc", "to": [20, 0, 0], "center": [25, 0, 0]},
                    ],
                },
            ],
        }
    )

    assert result["status"] == "success", result
    assert result["segment_count"] == 5
    assert result["arc_count"] == 1
    assert result["projection"]["max_off_plane_mm"] == 0.0


async def test_an_outline_off_the_plane_names_its_point(top) -> None:
    result = await top["add_outlines"](
        {
            "coordinates": "model",
            "outlines": [{"points": [[0, 0, 0], [1, 0, 0], [1, 3, 1]]}],
        }
    )

    assert "outlines[0].points[2] [1, 3, 1] mm is 3 mm above" in result["message"]


async def test_an_outline_that_collapses_on_the_plane_is_refused(
    part, mock_config
) -> None:
    await part.create_sketch("Front")
    tools = await _tools(part, mock_config)

    result = await tools["add_outlines"](
        {
            "coordinates": "model",
            "outlines": [{"points": [[0, 0, 0], [5, 0, 0], [0, 0, 0.001]]}],
        }
    )

    assert result["error_type"] == "invalid_input"
    assert "outlines[0] on the sketch plane" in result["message"]


async def test_a_cage_face_takes_model_points(part, mock_config) -> None:
    plane = await part.create_plane_through([20, 0, 10], [_S, 0, _C])
    await part.create_sketch(plane.data["name"])
    tools = await _tools(part, mock_config)
    corner = [20 + 3 * _C, 4, 10 - 3 * _S]

    result = await tools["add_circle"](
        {
            "center_x": corner[0],
            "center_y": corner[1],
            "center_z": corner[2],
            "radius": 1,
            "coordinates": "model",
        }
    )

    assert result["status"] == "success", result
    assert math.hypot(
        result["circle"]["center"]["x"], result["circle"]["center"]["y"]
    ) == (pytest.approx(5.0))


async def test_without_an_open_sketch_model_points_fail(part, mock_config) -> None:
    tools = await _tools(part, mock_config)

    result = await tools["add_circle"](
        {
            "center_x": 0,
            "center_y": 0,
            "center_z": 0,
            "radius": 1,
            "coordinates": "model",
        }
    )

    assert result["status"] == "error"
    assert "No sketch is open" in result["message"]


async def test_axes_that_are_no_frame_fail(mock_config) -> None:
    stub = SimpleNamespace(
        get_open_sketch_axes=AsyncMock(
            return_value=AdapterResult(
                status=AdapterResultStatus.SUCCESS, data={"x": 1}
            )
        )
    )
    tools = await _tools(stub, mock_config)

    result = await tools["add_circle"](
        {
            "center_x": 0,
            "center_y": 0,
            "center_z": 0,
            "radius": 1,
            "coordinates": "model",
        }
    )

    assert "didn't read as a plane" in result["message"]


async def test_an_adapter_without_axes_is_not_supported(mock_config) -> None:
    tools = await _tools(SimpleNamespace(), mock_config)

    result = await tools["add_line"](
        {"x1": 0, "y1": 0, "z1": 0, "x2": 1, "y2": 0, "z2": 0, "coordinates": "model"}
    )

    assert result["error_type"] == "not_supported"


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({"x1": 0, "y1": 0, "x2": 1, "y2": 0, "coordinates": "model"}, "needs z1, z2"),
        ({"x1": 0, "y1": 0, "x2": 1, "y2": 0, "z1": 0}, "z1 only go with"),
    ],
)
def test_z_goes_with_model_coordinates(data: dict[str, Any], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        AddLineInput(**data)


def test_an_arc_needs_its_z_values() -> None:
    with pytest.raises(ValueError, match="needs center_z, start_z, end_z"):
        AddArcInput(
            center_x=0,
            center_y=0,
            start_x=1,
            start_y=0,
            end_x=0,
            end_y=1,
            coordinates="model",
        )


def test_outline_points_are_all_2d_or_all_3d() -> None:
    with pytest.raises(ValueError, match="every point as"):
        Outline(points=[[0, 0], [1, 0, 0], [1, 1]])
    with pytest.raises(ValueError, match=r"every point is \[x, y, z\]"):
        AddOutlinesInput(
            coordinates="model", outlines=[{"points": [[0, 0], [1, 0], [1, 1]]}]
        )
    with pytest.raises(ValueError, match=r"every point is \[x, y\]"):
        AddOutlinesInput(outlines=[{"points": [[0, 0, 0], [1, 0, 0], [1, 1, 0]]}])


def test_model_outlines_wait_to_be_checked_on_the_plane() -> None:
    outline = Outline(points=[[0, 0, 0], [0, 0, 0], [0, 0, 0]])

    with pytest.raises(ValueError, match="distinct points"):
        outline.on_sketch([(0, 0), (0, 0), (0, 0)])


# --- plane_axes


async def test_plane_axes_reads_a_plane_without_a_sketch(part, mock_config) -> None:
    tools = await _tools(part, mock_config)

    result = await tools["plane_axes"]({"plane": "Right"})

    assert result["plane"] == "Right Plane"
    assert result["axes"]["x"] == [0.0, 0.0, -1.0]
    assert result["method"] == "plane_transform"
    assert result["message"] == (
        "Right Plane: sketch x = model -Z, sketch y = model +Y, normal = model +X"
    )
    assert part._current_sketch is None


async def test_plane_axes_refusals(part, mock_config) -> None:
    tools = await _tools(part, mock_config)

    assert "not found" in (await tools["plane_axes"]({"plane": "Plane4"}))["message"]
    assert (await tools["plane_axes"]({"plane": " "}))["error_type"] == "invalid_input"
    bare = await _tools(SimpleNamespace(), mock_config)
    assert (await bare["plane_axes"]({"plane": "Top"}))["error_type"] == "not_supported"
    with pytest.raises(ValueError, match="plane is required"):
        PlaneAxesInput(plane="")
