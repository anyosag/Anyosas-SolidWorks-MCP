"""``check_printability`` with ``orientations`` and ``min_feature_mm``."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.analysis import (
    CheckPrintabilityInput,
    register_analysis_tools,
)


@pytest.fixture
async def tools(mock_config: Any) -> tuple[MockSolidWorksAdapter, dict[str, Any]]:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_part()
    mcp = FastMCP("printability-options")
    await register_analysis_tools(mcp, adapter, mock_config)
    return adapter, {tool.name: tool.fn for tool in await mcp.list_tools()}


async def test_the_best_of_the_mock_tetrahedron(tools) -> None:
    _adapter, tool = tools

    reply = await tool["check_printability"]({"orientations": 6, "min_feature_mm": 0.4})

    assert reply["status"] == "success", reply
    assert reply["message"].startswith("Best of 6 orientations: build along ")
    assert "Checked in it." in reply["message"]
    assert reply["data"]["orientation"] == "best"
    assert "thin_features" in reply["data"]


async def test_a_given_direction_and_the_thin_regions(tools) -> None:
    adapter, tool = tools
    mock = AsyncMock(
        return_value=AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "ok": True,
                "orientation": "given",
                "orientations": {
                    "tried": 26,
                    "best": {
                        "build_direction": [0.0, -1.0, 0.0],
                        "rotate_to_z": {"angle_deg": 90.0, "axis": [1.0, 0.0, 0.0]},
                    },
                },
                "thin_features": {
                    "min_feature_mm": 0.4,
                    "thinnest_mm": 0.3,
                    "regions": {"count": 2},
                },
            },
        )
    )
    adapter.check_printability = mock  # type: ignore[method-assign]

    reply = await tool["check_printability"](
        {"build_direction": "y", "orientations": 26, "min_feature_mm": 0.4}
    )

    mock.assert_awaited_once_with("y", 45.0, 5.0, 26, 0.4)
    assert reply["message"] == (
        "Best of 26 orientations: build along [0.0, -1.0, 0.0] (turn 90.0° about "
        "[1.0, 0.0, 0.0] to make it +Z). Prints in this orientation without "
        "supports; 2 region(s) thinner than 0.4 mm, the thinnest 0.3 mm"
    )


async def test_nothing_thin(tools) -> None:
    adapter, tool = tools
    adapter.check_printability = AsyncMock(  # type: ignore[method-assign]
        return_value=AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "ok": True,
                "thin_features": {"min_feature_mm": 0.4, "regions": {"count": 0}},
            },
        )
    )

    reply = await tool["check_printability"](
        {"build_direction": "z", "min_feature_mm": 0.4}
    )

    assert reply["message"].endswith("; nothing thinner than 0.4 mm")


async def test_the_old_three_arguments_without_the_options(tools) -> None:
    adapter, tool = tools
    mock = AsyncMock(
        return_value=AdapterResult(
            status=AdapterResultStatus.SUCCESS, data={"ok": True}
        )
    )
    adapter.check_printability = mock  # type: ignore[method-assign]

    await tool["check_printability"]({"build_direction": "z"})

    mock.assert_awaited_once_with("z", 45.0, 5.0)


def test_a_direction_or_orientations_is_needed() -> None:
    with pytest.raises(ValueError, match="give build_direction, or orientations"):
        CheckPrintabilityInput()
    assert CheckPrintabilityInput(orientations=6).build_direction is None
