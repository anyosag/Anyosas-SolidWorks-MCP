"""``check_interference`` always names the pairs that overlap most.

The Dipper session's check with ``components`` reported 62 interferences and
no pair list reached the agent.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.analysis import (
    TOP_INTERFERENCE_PAIRS,
    interference_pairs,
    register_analysis_tools,
)


def _overlap(a: str, b: str, volume: Any) -> dict[str, Any]:
    return {"components": [a, b], "component_count": 2, "volume_mm3": volume}


def test_overlaps_are_grouped_by_pair_and_ranked() -> None:
    found = interference_pairs(
        [
            _overlap("cover-1", "base-1", 2.0),
            _overlap("pin-1", "base-1", 10.0),
            _overlap("base-1", "cover-1", 3.5),
            _overlap("latch-1", "cover-1", None),
            "not an interference",  # type: ignore[list-item]
        ]
    )

    assert found["pair_count"] == 3
    assert found["total_volume_mm3"] == 15.5
    assert found["top_pairs"] == [
        {
            "components": ["base-1", "pin-1"],
            "count": 1,
            "volume_mm3": 10.0,
            "largest_mm3": 10.0,
        },
        {
            "components": ["base-1", "cover-1"],
            "count": 2,
            "volume_mm3": 5.5,
            "largest_mm3": 3.5,
        },
        {
            "components": ["cover-1", "latch-1"],
            "count": 1,
            "volume_mm3": 0.0,
            "largest_mm3": 0.0,
        },
    ]


def test_only_the_top_pairs_are_listed() -> None:
    many = [_overlap(f"part-{i}", "base-1", float(i)) for i in range(62)]

    found = interference_pairs(many)

    assert len(found["top_pairs"]) == TOP_INTERFERENCE_PAIRS == 10
    assert found["top_pairs"][0]["components"] == ["base-1", "part-61"]
    assert found["pair_count"] == 62


async def _check(config: Any, data: Any) -> dict[str, Any]:
    adapter = MockSolidWorksAdapter({})
    adapter.check_interference = AsyncMock(  # type: ignore[method-assign]
        return_value=AdapterResult(status=AdapterResultStatus.SUCCESS, data=data)
    )
    mcp = FastMCP("interference")
    await register_analysis_tools(mcp, adapter, config)
    tool = next(t.fn for t in await mcp.list_tools() if t.name == "check_interference")
    return await tool({"components": ["cover-1"]})


async def test_the_reply_and_message_name_the_largest(mock_config: Any) -> None:
    overlaps = [_overlap("cover-1", "base-1", 1.25) for _ in range(60)] + [
        _overlap("cover-1", "latch-1", 4.0),
        _overlap("cover-1", "pin-1", 0.5),
    ]

    reply = await _check(
        mock_config,
        {
            "interference_found": True,
            "interference_count": 62,
            "interferences": overlaps,
        },
    )

    data = reply["data"]
    assert data["interference_count"] == 62
    assert data["top_pairs"][0] == {
        "components": ["base-1", "cover-1"],
        "count": 60,
        "volume_mm3": 75.0,
        "largest_mm3": 1.25,
    }
    assert len(data["interferences"]) == 62
    assert reply["message"] == (
        "62 interference(s) between 3 pair(s) of components; most overlap: "
        "base-1 / cover-1 75 mm³ (60 places); cover-1 / latch-1 4 mm³; "
        "cover-1 / pin-1 0.5 mm³"
    )


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"interference_count": 0, "interferences": []}, "No interference found"),
        (
            {"interference_count": 3, "interferences": []},
            "3 interference(s); SolidWorks named no components for them",
        ),
        (
            {"interference_count": None, "interferences": []},
            "Interference check completed",
        ),
        (None, "Interference check completed"),
    ],
)
async def test_messages_without_pairs(
    mock_config: Any, data: Any, message: str
) -> None:
    reply = await _check(mock_config, data)

    assert reply["message"] == message
    assert reply["data"]["top_pairs"] == []
