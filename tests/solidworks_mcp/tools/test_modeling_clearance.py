"""The ``measure_clearance`` tool on the mock, and its input and error rules."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.modeling import (
    MeasureClearanceInput,
    register_modeling_tools,
)


@pytest.fixture
async def latch(mock_config: Any) -> tuple[MockSolidWorksAdapter, Any]:
    """A tongue and a slot 10 mm cubes, 0.25 mm apart."""
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_assembly()
    await adapter.insert_component("C:/parts/tongue.sldprt", 0, 0, 0)
    await adapter.insert_component("C:/parts/slot.sldprt", 10.25, 0, 0)
    mcp = FastMCP("clearance")
    await register_modeling_tools(mcp, adapter, mock_config)
    tool = next(t for t in await mcp.list_tools() if t.name == "measure_clearance")
    return adapter, tool


async def test_the_gap_and_where_it_is(latch: Any) -> None:
    _adapter, tool = latch

    result = await tool.fn({"component_a": "component-1", "component_b": "component-2"})

    assert result["status"] == "success", result
    assert result["message"] == (
        "Clearance between component-1 and component-2: 0.25 mm"
    )
    assert result["data"]["clearance_mm"] == pytest.approx(0.25)
    assert result["data"]["point_b_mm"] == [10.25, 5.0, 5.0]
    # Nothing touches.
    assert "warning" not in result  # the experimental note is in the description


async def test_touching_components_point_to_check_interference(latch: Any) -> None:
    adapter, tool = latch
    adapter.component_boxes_mm["component-2"] = [-1.0, 0.0, 0.0, 5.0, 5.0, 5.0]

    result = await tool.fn({"component_a": "component-1", "component_b": "component-2"})

    assert result["status"] == "success", result
    assert result["data"]["clearance_mm"] == 0
    assert result["data"]["touching"] is True
    assert result["message"] == "component-1 and component-2 touch or overlap"
    assert result["warning"].startswith(
        "component-1 and component-2 touch or overlap, so there is no clearance "
        "to report; use check_interference"
    )


@pytest.mark.parametrize(
    ("arguments", "fragment"),
    [
        ({"component_a": "component-1", "component_b": " component-1 "}, "both"),
        ({"component_a": "component-1", "component_b": "  "}, "both required"),
        ({"component_a": "component-1"}, "component_b"),
    ],
)
async def test_bad_names_are_invalid_input(
    latch: Any, arguments: dict[str, Any], fragment: str
) -> None:
    _adapter, tool = latch

    result = await tool.fn(arguments)

    assert result["error_type"] == "invalid_input"
    assert fragment in result["message"]


async def test_an_unknown_component_is_not_found(latch: Any) -> None:
    _adapter, tool = latch

    result = await tool.fn({"component_a": "component-1", "component_b": "pin-1"})

    assert result["error_type"] == "not_found"
    assert "The assembly holds: component-1, component-2" in result["message"]


async def test_a_bodiless_component_is_invalid_input_and_named(latch: Any) -> None:
    adapter, tool = latch
    adapter.component_boxes_mm["component-2"] = None

    result = await tool.fn({"component_a": "component-1", "component_b": "component-2"})

    assert result["error_type"] == "invalid_input"
    assert "component_b 'component-2'" in result["message"]


async def test_a_part_is_invalid_input(latch: Any) -> None:
    adapter, tool = latch
    await adapter.create_part()

    result = await tool.fn({"component_a": "component-1", "component_b": "component-2"})

    assert result["error_type"] == "invalid_input"
    assert "the active document is a part" in result["message"]


def test_the_input_strips_the_names() -> None:
    parsed = MeasureClearanceInput(component_a=" tongue-1 ", component_b="slot-1")

    assert (parsed.component_a, parsed.component_b) == ("tongue-1", "slot-1")


async def test_the_mock_reply_is_complete_and_bounded(latch: Any) -> None:
    _adapter, tool = latch

    result = await tool.fn(
        {"component_a": "component-1", "component_b": "component-2", "coarse": True}
    )

    assert result["data"]["complete"] is True
    assert result["data"]["at_least_mm"] == pytest.approx(0.25)
    # Asked for speed, the mock answers as the components measured whole.
    assert result["data"]["method"] == "components"
    assert result["data"]["face_a"] is None and result["data"]["pairs_total"] is None

    full = await tool.fn({"component_a": "component-1", "component_b": "component-2"})
    assert full["data"]["method"] == "face_pairs"
    assert full["data"]["face_a"]["kind"] == "plane"


async def test_a_coarse_answer_gives_both_bounds(latch: Any) -> None:
    adapter, tool = latch
    mock = AsyncMock(
        return_value=AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "clearance_mm": 0.6,
                "at_least_mm": 0.2,
                "complete": False,
                "stopped_by": "coarse",
                "touching": False,
                "pairs_checked": 12,
                "pairs_total": 2304,
            },
        )
    )
    adapter.measure_clearance = mock  # type: ignore[method-assign]

    result = await tool.fn(
        {
            "component_a": "component-1",
            "component_b": "component-2",
            "coarse": True,
            "timeout_s": 5,
        }
    )

    mock.assert_awaited_once_with("component-1", "component-2", True, 5.0)
    assert result["message"] == (
        "Clearance between component-1 and component-2: at most 0.6 mm and at "
        "least 0.2 mm (12 of 2304 face pairs measured)"
    )
    assert result["warning"].startswith("Stopped early (coarse)")


async def test_a_timed_out_answer_names_the_timeout(latch: Any) -> None:
    adapter, tool = latch
    adapter.measure_clearance = AsyncMock(  # type: ignore[method-assign]
        return_value=AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"clearance_mm": 1.0, "at_least_mm": 0.5, "complete": False},
        )
    )

    result = await tool.fn(
        {"component_a": "component-1", "component_b": "component-2", "timeout_s": 1}
    )

    assert "Stopped early (timeout_s)" in result["warning"]


def test_a_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError, match="greater than 0"):
        MeasureClearanceInput(component_a="a-1", component_b="b-1", timeout_s=0)
