"""The mock draws many sketch entities in one call, as the live adapter does.

It refuses what the live adapter refuses, with the same messages (both use
``sketch.sketch_entities_problem``), and numbers the ids the same way.
"""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


@pytest.fixture
async def adapter() -> MockSolidWorksAdapter:
    mock = MockSolidWorksAdapter({"mock_sketch_delay": 0})
    await mock.connect()
    await mock.create_part()
    await mock.create_sketch("Top")
    return mock


def _items() -> list[dict[str, Any]]:
    return [
        {"tool": "add_circle", "center_x": 0, "center_y": 0, "radius": 5},
        {"tool": "add_line", "x1": 0, "y1": 0, "x2": 10, "y2": 0},
        {"tool": "add_rectangle", "x1": 0, "y1": 0, "x2": 4, "y2": 2},
        {
            "tool": "add_arc",
            "center_x": 0,
            "center_y": 0,
            "start_x": 5,
            "start_y": 0,
            "end_x": 0,
            "end_y": 5,
        },
        {"tool": "add_centerline", "x1": 0, "y1": -5, "x2": 0, "y2": 5},
    ]


async def test_each_item_gets_an_id_in_order(adapter: MockSolidWorksAdapter) -> None:
    result = await adapter.add_sketch_entities(_items(), rollback_on_error=False)

    assert result.is_success
    data = result.data or {}
    assert [(i["index"], i["tool"], i["id"]) for i in data["items"]] == [
        (0, "add_circle", "Circle_1"),
        (1, "add_line", "Line_2"),
        (2, "add_rectangle", "Rectangle_3"),
        (3, "add_arc", "Arc_4"),
        (4, "add_centerline", "Centerline_5"),
    ]
    assert data["count"] == 5
    # Later calls (constraints, construction) know the ids.
    assert (await adapter.set_sketch_construction(["Rectangle_3"])).is_success


async def test_what_the_live_adapter_refuses_is_refused(
    adapter: MockSolidWorksAdapter,
) -> None:
    result = await adapter.add_sketch_entities([{"tool": "add_circle", "radius": 1}])

    assert result.is_error
    assert result.error == (
        "Invalid request: item 0 (add_circle) needs center_x, center_y"
    )


async def test_no_open_sketch_is_refused() -> None:
    mock = MockSolidWorksAdapter({})

    result = await mock.add_sketch_entities(_items())

    assert result.error == "No active sketch"
