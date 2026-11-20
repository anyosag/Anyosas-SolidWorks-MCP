"""The mock leaves out what a listed pattern repeats, as the live adapter does.

Its patterns and mirrors join the feature tree holding their seeds, so a
pattern of a pattern can be made, and listing a pattern beside its own seeds
leaves the seeds out (``left_out``).
"""

from __future__ import annotations

import pytest

from solidworks_mcp.adapters.base import ExtrusionParameters
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


@pytest.fixture
async def ring() -> tuple[MockSolidWorksAdapter, str, str, str]:
    """A part with a vent (a boss and its chamfer) patterned round an axis."""
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_part()
    hole = (await adapter.create_extrusion(ExtrusionParameters(depth=2.0))).data.name
    chamfer = (await adapter.add_chamfer(0.5, ["Edge1"])).data.name
    axis = (await adapter.create_axis("y")).data["name"]
    pattern = await adapter.pattern_circular([hole, chamfer], axis, 6)
    assert "left_out" not in pattern.data
    return adapter, hole, chamfer, pattern.data["name"]


async def test_a_pattern_listed_with_its_seeds_leaves_them_out(ring) -> None:
    adapter, hole, chamfer, ring_name = ring

    result = await adapter.pattern_linear([hole, chamfer, ring_name], "x", 2, 35.7)

    assert result.is_success, result.error
    assert result.data["patterned"] == [ring_name]
    assert result.data["left_out"] == [
        {"feature": hole, "patterned_by": ring_name},
        {"feature": chamfer, "patterned_by": ring_name},
    ]


async def test_a_mirror_of_a_pattern_of_a_pattern_keeps_only_the_outer(ring) -> None:
    adapter, hole, chamfer, ring_name = ring
    grid = (await adapter.pattern_linear([ring_name], "z", 2, 35.7)).data["name"]

    result = await adapter.mirror_feature(
        [hole, chamfer, ring_name, grid], "Right Plane", mirror_bodies=False
    )

    assert result.data["mirrored"] == [grid]
    assert [item["feature"] for item in result.data["left_out"]] == [
        hole,
        chamfer,
        ring_name,
    ]
    assert result.data["left_out"][2]["patterned_by"] == grid


async def test_a_ring_of_a_mirror_leaves_out_the_mirrors_seeds(ring) -> None:
    adapter, hole, _chamfer, _ring_name = ring
    mirror = await adapter.mirror_feature([hole], "Right Plane", mirror_bodies=False)
    axis = (await adapter.create_axis("z")).data["name"]

    result = await adapter.pattern_circular([hole, mirror.data["name"]], axis, 3)

    assert result.data["patterned"] == [mirror.data["name"]]
    assert result.data["left_out"] == [
        {"feature": hole, "patterned_by": mirror.data["name"]}
    ]


async def test_patterns_and_mirrors_join_the_feature_tree(ring) -> None:
    adapter, hole, _chamfer, ring_name = ring
    body_mirror = await adapter.mirror_feature([hole], "Right Plane")
    rows = await adapter.pattern_linear([hole], "x", 2, 5.0, bodies=True)

    kinds = {f["name"]: f["type"] for f in (await adapter.list_features()).data}

    assert kinds[ring_name] == "CirPattern"
    assert kinds[body_mirror.data["name"]] == "MirrorSolid"
    assert kinds[rows.data["name"]] == "LPattern"


async def test_bodies_repeat_no_feature(ring) -> None:
    adapter, hole, _chamfer, _ring_name = ring
    body_mirror = (await adapter.mirror_feature([hole], "Right Plane")).data["name"]

    result = await adapter.pattern_linear([hole, body_mirror], "x", 2, 5.0)

    assert result.data["patterned"] == [hole, body_mirror]
    assert "left_out" not in result.data
