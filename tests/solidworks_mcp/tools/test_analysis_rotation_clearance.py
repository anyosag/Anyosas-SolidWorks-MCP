"""The ``rotation_clearance`` tool: inputs checked, findings in words, warnings."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.analysis import (
    RotationClearanceInput,
    register_analysis_tools,
)

_ARM = [40.0, -5.0, -5.0, 50.0, 5.0, 5.0]
_WALL = [-100.0, 20.0, -10.0, 100.0, 30.0, 10.0]


@pytest.fixture
async def tools(mock_config: Any) -> tuple[MockSolidWorksAdapter, dict[str, Any]]:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    adapter.part_bodies_mm = {"Arm": _ARM, "Wall": _WALL}
    await adapter.create_part()
    mcp = FastMCP("rotation-clearance")
    await register_analysis_tools(mcp, adapter, mock_config)
    return adapter, {tool.name: tool.fn for tool in await mcp.list_tools()}


def _answer(adapter: MockSolidWorksAdapter, **data: Any) -> AsyncMock:
    mock = AsyncMock(
        return_value=AdapterResult(status=AdapterResultStatus.SUCCESS, data=data)
    )
    adapter.rotation_clearance = mock  # type: ignore[method-assign]
    return mock


async def test_an_arm_swung_into_a_wall(tools) -> None:
    _adapter, tool = tools

    reply = await tool["rotation_clearance"](
        {"moving": "Arm", "other": "Wall", "axis": "z", "range_deg": [0, 90]}
    )

    assert reply["status"] == "success", reply
    assert reply["message"].startswith("Turning Arm against Wall: + way hits at ")
    assert "- way" not in reply["message"]
    assert reply["data"]["positive"]["first_contact_deg"] < 20
    # A point 50 mm out moves 4.4 mm per 5 degree step.
    assert "a wall thinner than that can be passed unseen" in reply["warning"]


async def test_the_inputs_reach_the_adapter(tools) -> None:
    adapter, tool = tools
    mock = _answer(adapter, positive={"to_deg": 0.0}, negative={"to_deg": 0.0})

    await tool["rotation_clearance"](
        {
            "moving": " lid-1 ",
            "other": "base-1",
            "axis": [0, 0, 2],
            "pivot_mm": [0, 5, 0],
            "range_deg": [-10, 170],
            "step_deg": 2,
            "tolerance_deg": 0.05,
        }
    )

    mock.assert_awaited_once_with(
        "lid-1", "base-1", [0.0, 0.0, 1.0], [0, 5, 0], [-10, 170], 2.0, 0.05
    )


async def test_the_closest_pass_and_a_free_way(tools) -> None:
    adapter, tool = tools
    _answer(
        adapter,
        positive={"to_deg": 180.0, "first_contact_deg": 160.3, "free_to_deg": 160.2},
        negative={"to_deg": -30.0, "first_contact_deg": None, "free_to_deg": -30.0},
        closest_pass={"angle_deg": 37.2, "clearance_mm": 0.3},
        min_clearance_mm=0.0,
        min_clearance_deg=0.0,
        clearance_measured=True,
        travel_per_step_mm=0.5,
    )

    reply = await tool["rotation_clearance"]({"moving": "a", "other": "b", "axis": "x"})

    assert reply["message"] == (
        "Turning a against b: + way hits at 160.3°, - way free to -30°; closest "
        "pass 0.3 mm at 37.2°"
    )
    assert "passed unseen" not in reply.get("warning", "")


async def test_the_smallest_gap_without_a_closest_pass(tools) -> None:
    adapter, tool = tools
    _answer(
        adapter,
        positive={"to_deg": 90.0, "first_contact_deg": None},
        negative={"to_deg": 0.0},
        closest_pass=None,
        min_clearance_mm=1.5,
        min_clearance_deg=90.0,
        clearance_measured=True,
    )

    reply = await tool["rotation_clearance"]({"moving": "a", "other": "b", "axis": "y"})

    assert reply["message"].endswith("; smallest gap 1.5 mm at 90°")


async def test_what_could_not_be_measured_or_put_back(tools) -> None:
    adapter, tool = tools
    _answer(
        adapter,
        positive={"to_deg": 90.0, "first_contact_deg": 45.0},
        negative={"to_deg": 0.0},
        clearance_measured=False,
        restored=False,
    )

    reply = await tool["rotation_clearance"]({"moving": "a", "other": "b", "axis": "y"})

    assert "only contacts (overlaps) were found" in reply["warning"]
    assert "a could not be put back where it was" in reply["warning"]


async def test_a_mated_component_is_warned_about(tools) -> None:
    adapter, tool = tools
    _answer(
        adapter,
        positive={"to_deg": 30.0, "first_contact_deg": None},
        negative={"to_deg": 0.0},
        clearance_measured=True,
        mates=1,
    )

    reply = await tool["rotation_clearance"]({"moving": "a", "other": "b", "axis": "y"})

    assert "a has 1 mate(s), and the sweep turned it regardless" in reply["warning"]


async def test_parts_that_already_overlap(tools) -> None:
    adapter, tool = tools
    _answer(adapter, interfering_at_start=True, start_overlap_mm3=12.5)

    reply = await tool["rotation_clearance"]({"moving": "a", "other": "b", "axis": "y"})

    assert reply["message"] == (
        "a against b: they already overlap by 12.5 mm³ where they are, so nothing "
        "was turned"
    )
    assert "Clear the overlap first" in reply["warning"]


@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ({"moving": "a", "other": " a ", "axis": "z"}, "must be different"),
        ({"moving": " ", "other": "b", "axis": "z"}, "required"),
        ({"moving": "a", "other": "b", "axis": "w"}, "not 'w'"),
        ({"moving": "a", "other": "b", "axis": "z", "range_deg": [5, 9]}, "at most 0"),
        (
            {
                "moving": "a",
                "other": "b",
                "axis": "z",
                "tolerance_deg": 2,
                "step_deg": 1,
            },
            "tolerance_deg",
        ),
    ],
)
def test_bad_inputs_are_refused(payload: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        RotationClearanceInput(**payload)


async def test_an_adapter_without_the_sweep(tools, mock_config) -> None:
    class _Bare:
        pass

    mcp = FastMCP("bare")
    await register_analysis_tools(mcp, _Bare(), mock_config)  # type: ignore[arg-type]
    fn = next(t.fn for t in await mcp.list_tools() if t.name == "rotation_clearance")

    reply = await fn({"moving": "a", "other": "b", "axis": "z"})

    assert reply["status"] == "error"
    assert reply["error_type"] == "not_supported"
