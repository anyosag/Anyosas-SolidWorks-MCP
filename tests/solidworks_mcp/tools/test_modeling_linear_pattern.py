"""The ``pattern_linear`` tool: its input, ``measured_change``, and an empty pattern.

From the Dipper build: 8 identical slot cuts and 16 drafted lead-in cuts in
rows, with only ``pattern_circular`` to repeat a feature.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.tools.modeling import PatternLinearInput, register_modeling_tools


def _ok(data: Any = None) -> AdapterResult[Any]:
    return AdapterResult(status=AdapterResultStatus.SUCCESS, data=data)


def _volume(mm3: float) -> AdapterResult[Any]:
    box = {"size": [40.0, 10.0, 40.0]}
    return _ok({"volume_mm3": mm3, "bounding_box_mm": box, "solid_body_count": 1})


async def _tool(mcp_server, adapter) -> Any:
    await register_modeling_tools(mcp_server, adapter, {})
    return {t.name: t.fn for t in await mcp_server.list_tools()}["pattern_linear"]


_PATTERN = {"name": "LPattern1", "instances": 8, "patterned": ["Slot"]}


async def test_a_row_reports_what_it_changed(mcp_server, mock_adapter) -> None:
    mock_adapter.pattern_linear = AsyncMock(return_value=_ok(dict(_PATTERN)))
    mock_adapter.get_model_geometry_summary = AsyncMock(
        side_effect=[_volume(16000.0), _volume(15720.0)]
    )
    tool = await _tool(mcp_server, mock_adapter)

    reply = await tool(
        {"features": ["Slot"], "direction": "x", "count": 8, "spacing": 12}
    )

    assert reply["status"] == "success", reply
    mock_adapter.pattern_linear.assert_awaited_once_with(
        ["Slot"], "x", 8, 12.0, None, 1, 0.0, False, False, False
    )
    assert reply["message"] == "Patterned Slot: 8 instances (LPattern1)"
    assert reply["pattern"] == _PATTERN
    assert reply["measured_change"]["volume_delta_mm3"] == -280.0


async def test_a_grid_passes_both_directions(mcp_server, mock_adapter) -> None:
    mock_adapter.pattern_linear = AsyncMock(return_value=_ok(dict(_PATTERN)))
    tool = await _tool(mcp_server, mock_adapter)

    await tool(
        {
            "features": ["Lead-in"],
            "direction": "Axis1",
            "count": 4,
            "spacing": 10,
            "direction2": "-z",
            "count2": 4,
            "spacing2": 7.5,
            "reverse_direction2": True,
            "bodies": True,
        }
    )

    mock_adapter.pattern_linear.assert_awaited_once_with(
        ["Lead-in"], "Axis1", 4, 10.0, "-z", 4, 7.5, False, True, True
    )


async def test_an_empty_pattern_is_deleted_and_fails(mcp_server, mock_adapter) -> None:
    mock_adapter.pattern_linear = AsyncMock(return_value=_ok(dict(_PATTERN)))
    mock_adapter.get_model_geometry_summary = AsyncMock(
        side_effect=[_volume(16000.0), _volume(16000.0)]
    )
    mock_adapter.delete_feature = AsyncMock(return_value=_ok({}))
    tool = await _tool(mcp_server, mock_adapter)

    reply = await tool(
        {"features": ["Slot"], "direction": "x", "count": 8, "spacing": 12}
    )

    assert reply["status"] == "error"
    assert "built no instance: the volume stayed at 16000 mm3" in reply["message"]
    assert "The empty pattern LPattern1 was deleted." in reply["message"]
    mock_adapter.delete_feature.assert_awaited_once_with("LPattern1")


async def test_an_empty_pattern_that_cannot_be_deleted_says_so(
    mcp_server, mock_adapter
) -> None:
    mock_adapter.pattern_linear = AsyncMock(return_value=_ok(dict(_PATTERN)))
    mock_adapter.get_model_geometry_summary = AsyncMock(
        side_effect=[_volume(16000.0), _volume(16000.0)]
    )
    mock_adapter.delete_feature = AsyncMock(
        return_value=AdapterResult(status=AdapterResultStatus.ERROR, error="locked")
    )
    tool = await _tool(mcp_server, mock_adapter)

    reply = await tool(
        {"features": ["Slot"], "direction": "x", "count": 8, "spacing": 12}
    )

    assert "LPattern1 is still in the tree (locked)" in reply["message"]


async def test_the_mock_patterns_a_feature_it_made(mcp_server, mock_adapter) -> None:
    await mock_adapter.create_part()
    await mock_adapter.create_sketch("Top")
    await mock_adapter.exit_sketch()
    tool = await _tool(mcp_server, mock_adapter)
    boss = await {t.name: t.fn for t in await mcp_server.list_tools()}[
        "create_extrusion"
    ]({"depth": 5})

    reply = await tool(
        {
            "features": [boss["extrusion"]["name"]],
            "direction": "-x",
            "count": 3,
            "spacing": 6,
        }
    )

    assert reply["status"] == "success", reply
    assert reply["pattern"]["direction"]["flipped"] is True
    assert reply["pattern"]["instances"] == 3


@pytest.mark.parametrize(
    ("fields", "fragment"),
    [
        ({"features": [" "]}, "at least one name"),
        ({"count": 1}, "count must be at least 2"),
        ({"direction": "edge:1,2"}, "an edge is given as"),
        ({"count2": 2}, "need a direction2"),
        ({"direction2": "y"}, "needs count2 and spacing2"),
        ({"direction2": "y", "count2": 1, "spacing2": 4}, "count2 must be at least 2"),
        ({"direction2": "", "count2": 2, "spacing2": 4}, "direction is required"),
    ],
)
def test_a_pattern_that_cannot_be_made_is_refused(
    fields: dict[str, Any], fragment: str
) -> None:
    base: dict[str, Any] = {
        "features": ["Slot"],
        "direction": "x",
        "count": 4,
        "spacing": 5,
    }

    with pytest.raises(ValueError, match=fragment):
        PatternLinearInput(**{**base, **fields})
