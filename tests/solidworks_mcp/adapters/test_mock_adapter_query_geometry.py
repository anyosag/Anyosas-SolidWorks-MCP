"""The mock's ``query_geometry`` on the canned 10 mm block."""

from __future__ import annotations

import math

import pytest

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


async def _part(hole: bool = False) -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_part()
    if hole:
        await adapter.create_sketch("Top")
        await adapter.add_circle(5, 5, 2)
    return adapter


async def test_a_corner_has_three_faces_and_three_edges() -> None:
    adapter = await _part()

    result = await adapter.query_geometry([10.0, 10.0, 10.0], 0.5)

    assert result.is_success, result.error
    data = result.data
    assert (data["method"], data["complete"]) == ("index", True)
    assert data["faces_found"] == 3 and data["edges_found"] == 3
    assert {tuple(f["normal"]) for f in data["faces"]} == {
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    }
    assert all(e["kind"] == "line" and e["length_mm"] == 10.0 for e in data["edges"])
    assert all(e["id"] for e in data["faces"] + data["edges"])


async def test_the_edge_ids_select_the_same_edges() -> None:
    adapter = await _part()
    near = await adapter.query_geometry([10.0, 5.0, 10.0], 0.1, kinds="edges")

    picked = await adapter.select_edges({"edge_ids": [near.data["edges"][0]["id"]]})

    assert picked.is_success, picked.error
    edge = near.data["edges"][0]
    assert edge["start_mm"] == [10.0, 0.0, 10.0] and edge["end_mm"] == [
        10.0,
        10.0,
        10.0,
    ]
    assert picked.data["edges"][0]["start_mm"] == edge["start_mm"]


async def test_a_hole_gives_its_wall_and_rims() -> None:
    adapter = await _part(hole=True)

    result = await adapter.query_geometry([5.0, 5.0, 10.0], 2.5, limit=1)

    data = result.data
    assert data["faces"][0]["kind"] == "plane" and data["faces_found"] == 2
    rim = data["edges"][0]
    assert rim["kind"] == "circle" and rim["radius_mm"] == 2.0
    assert rim["length_mm"] == pytest.approx(4 * math.pi)
    wall = await adapter.query_geometry([5.0, 5.0, 4.0], 2.5, kinds="faces")
    cylinder = next(f for f in wall.data["faces"] if f["kind"] == "cylinder")
    assert cylinder["center_mm"] == [5.0, 5.0, 4.0] and cylinder["distance_mm"] == 2.0
    assert "edges" not in wall.data


async def test_the_refusals_match_the_real_adapter() -> None:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()

    nothing = await adapter.query_geometry([0.0, 0.0, 0.0])
    bad = await adapter.query_geometry([0.0, 0.0, 0.0], kinds="vertices")
    await adapter.create_assembly()
    assembly = await adapter.query_geometry([0.0, 0.0, 0.0])

    assert nothing.error == "No active model"
    assert bad.error.startswith("kinds must be one of faces, edges, both")
    assert assembly.error == (
        "query_geometry needs an active part document (active document is assembly)."
    )
