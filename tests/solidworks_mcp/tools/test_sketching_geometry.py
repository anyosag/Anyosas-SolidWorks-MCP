"""``add_sketch_geometry``: rows of circles, lines, rectangles and centrelines.

The rows go to the adapter's ``add_sketch_entities`` as one call (one
SolidWorks operation); the reply gives one id per row under the input's keys.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.sketching import register_sketching_tools


@pytest.fixture
async def tools(mock_config: Any) -> dict[str, Any]:
    """The sketching tools on a mock part, by name, and what reached the adapter."""
    adapter = MockSolidWorksAdapter({"mock_sketch_delay": 0})
    await adapter.connect()
    await adapter.create_part()
    sent: list[tuple[list[dict[str, Any]], bool]] = []
    drawing = adapter.add_sketch_entities

    async def spy(
        items: list[dict[str, Any]], rollback_on_error: bool = True
    ) -> AdapterResult[dict[str, Any]]:
        sent.append((items, rollback_on_error))
        return await drawing(items, rollback_on_error)

    adapter.add_sketch_entities = spy  # type: ignore[method-assign]
    mcp = FastMCP("sketch geometry")
    await register_sketching_tools(mcp, adapter, mock_config)
    found: dict[str, Any] = {tool.name: tool.fn for tool in await mcp.list_tools()}
    found["_adapter"] = adapter
    found["_sent"] = sent
    return found


async def test_rows_are_drawn_in_one_call_with_an_id_per_row(
    tools: dict[str, Any],
) -> None:
    await tools["create_sketch"]({"plane": "Top"})

    reply = await tools["add_sketch_geometry"](
        {
            "circles": [[0, 0, 5], [20, 0, 2.5]],
            "lines": [[0, 0, 10, 0]],
            "rectangles": [[-5, -5, 5, 5]],
            "centerlines": [[0, -9, 0, 9]],
        }
    )

    assert reply["status"] == "success", reply
    assert reply["message"] == (
        "Drew 2 circles, 1 line, 1 rectangle and 1 centerline in one operation"
    )
    assert reply["circles"] == ["Circle_1", "Circle_2"]
    assert reply["lines"] == ["Line_3"]
    assert reply["rectangles"] == ["Rectangle_4"]
    assert reply["centerlines"] == ["Centerline_5"]
    assert reply["entity_count"] == 5
    assert reply["construction"] is False
    assert "execution_time" in reply
    assert len(tools["_sent"]) == 1


async def test_each_row_becomes_an_item_named_after_it(tools: dict[str, Any]) -> None:
    await tools["create_sketch"]({"plane": "Front"})

    reply = await tools["add_sketch_geometry"](
        {
            "centerlines": [[0, -9, 0, 9]],
            "circles": [[1, 2, 3]],
            "lines": [[0, 0, 4, 0], [4, 0, 4, 4]],
            "construction": True,
        }
    )

    assert reply["status"] == "success", reply
    ((items, rollback),) = tools["_sent"]
    assert rollback is True  # a failed call removes what it drew
    assert items == [
        {
            "center_x": 1,
            "center_y": 2,
            "radius": 3,
            "tool": "add_circle",
            "label": "circles[0]",
            "construction": True,
        },
        {
            "x1": 0,
            "y1": 0,
            "x2": 4,
            "y2": 0,
            "tool": "add_line",
            "label": "lines[0]",
            "construction": True,
        },
        {
            "x1": 4,
            "y1": 0,
            "x2": 4,
            "y2": 4,
            "tool": "add_line",
            "label": "lines[1]",
            "construction": True,
        },
        # A centreline already is construction geometry.
        {
            "x1": 0,
            "y1": -9,
            "x2": 0,
            "y2": 9,
            "tool": "add_centerline",
            "label": "centerlines[0]",
        },
    ]
    assert reply["rectangles"] == []
    assert reply["construction"] is True


async def test_one_row_is_named_in_the_singular(tools: dict[str, Any]) -> None:
    await tools["create_sketch"]({"plane": "Top"})

    reply = await tools["add_sketch_geometry"]({"circles": [[0, 0, 1]]})

    assert reply["message"] == "Drew 1 circle in one operation"


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "give at least one row: circles, lines, rectangles or centerlines"),
        ({"circles": [[0, 0, 1], [5, 0, 0]]}, "circles[1]: radius must be positive"),
        (
            {"rectangles": [[1, 1, 1, 1]]},
            "rectangles[0]: rectangle corners must differ",
        ),
        ({"lines": [[0, 0, 1]]}, "lines.0: List should have at least 4 items"),
        ({"circles": [[0, 0, 1, 2]]}, "circles.0: List should have at most 3 items"),
        ({"circles": [["a", 0, 1]]}, "circles.0.0: Input should be a valid number"),
        ({"circle": [[0, 0, 1]]}, "circle: Extra inputs are not permitted"),
        (
            {"rectangles": [[0, 0, 1, 1]] * 501},
            "2004 segments in one call (a rectangle is four); the most is 2000",
        ),
    ],
)
async def test_rows_the_single_tools_would_refuse_are_refused_first(
    tools: dict[str, Any], arguments: dict[str, Any], message: str
) -> None:
    await tools["create_sketch"]({"plane": "Top"})

    reply = await tools["add_sketch_geometry"](arguments)

    assert reply["status"] == "error"
    assert reply["error_type"] == "invalid_input"
    assert message in reply["message"]
    assert tools["_sent"] == []


async def test_without_an_open_sketch_it_says_so(tools: dict[str, Any]) -> None:
    reply = await tools["add_sketch_geometry"]({"circles": [[0, 0, 1]]})

    assert reply["status"] == "error"
    assert reply["error_type"] == "no_active_sketch"
    assert reply["message"] == "Failed to draw sketch geometry: No active sketch"


async def test_a_refused_row_gives_the_adapters_account(tools: dict[str, Any]) -> None:
    """The failed row, and what became of the rest, reach the agent."""
    await tools["create_sketch"]({"plane": "Top"})
    account = {
        "items": [],
        "failed_index": 1,
        "failed": "circles[1]",
        "error": "Failed to create circle",
        "removed": True,
    }

    async def refuse(
        items: list[dict[str, Any]], rollback_on_error: bool = True
    ) -> AdapterResult[dict[str, Any]]:
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error=(
                "circles[1] (circle at (2, 0), radius 1) was not created: Failed to "
                "create circle. The 1 entity this call drew was removed again."
            ),
            data=account,
            execution_time=0.2,
        )

    tools["_adapter"].add_sketch_entities = refuse

    reply = await tools["add_sketch_geometry"]({"circles": [[0, 0, 1], [2, 0, 1]]})

    assert reply["status"] == "error"
    assert reply["message"].startswith(
        "Failed to draw sketch geometry: circles[1] (circle at (2, 0), radius 1) "
        "was not created"
    )
    assert reply["error_type"] == "operation_failed"
    assert reply["data"] == account
    assert reply["execution_time"] == 0.2
