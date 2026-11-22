"""The mock's ``check_interference``: each component is its box, where it was placed.

The mock answered ``None`` for every count, so a dry run learned nothing. Its
components are boxes (``component_boxes_mm``, a 10 mm cube by default) for
``measure_clearance`` already; now each pair of boxes that share a volume is
one interference of it, in the real adapter's reply shape.
"""

from __future__ import annotations

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.adapters.solidworks.interference import (
    BODY_INTERSECTION,
    COINCIDENT_REASON,
    INTERFERENCE_DETECTION,
    fallback_note,
)


async def _assembly(*origins: tuple[float, float, float]) -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_assembly()
    for x, y, z in origins:
        await adapter.insert_component("C:/parts/block.sldprt", x, y, z)
    return adapter


def _found(data: dict) -> list[tuple[list[str], float]]:
    return [(i["components"], i["volume_mm3"]) for i in data["interferences"]]


async def test_components_apart_are_clean() -> None:
    adapter = await _assembly((0, 0, 0), (10.25, 0, 0))

    result = await adapter.check_interference({})

    assert result.is_success, result.error
    assert result.data == {
        "interference_found": False,
        "interference_count": 0,
        "interferences": [],
        "coincident_treated_as_interference": False,
        "scope": "whole assembly",
        "tolerance_applied": None,
        "method": BODY_INTERSECTION,
    }


async def test_boxes_that_share_a_volume_are_one_interference_of_it() -> None:
    adapter = await _assembly((0, 0, 0), (5, 0, 0), (50, 0, 0))

    result = await adapter.check_interference({})

    assert result.data["interference_found"] is True
    assert _found(result.data) == [(["component-1", "component-2"], 500.0)]


async def test_a_component_is_checked_where_it_was_moved() -> None:
    adapter = await _assembly((0, 0, 0), (50, 0, 0))

    await adapter.set_component_transform("component-2", [2.0, 0, 0])
    result = await adapter.check_interference({})

    assert _found(result.data) == [(["component-1", "component-2"], 800.0)]


async def test_a_component_with_no_solid_body_overlaps_nothing() -> None:
    adapter = await _assembly((0, 0, 0), (0, 0, 0))
    adapter.component_boxes_mm["component-2"] = None

    result = await adapter.check_interference({})

    assert result.data["interference_count"] == 0


async def test_hidden_components_are_left_out_only_when_asked() -> None:
    adapter = await _assembly((0, 0, 0), (5, 0, 0))
    await adapter.set_component_visibility(["component-2"], False)

    left_out = await adapter.check_interference({"ignore_hidden": True})
    included = await adapter.check_interference({})

    assert left_out.data["interference_count"] == 0
    assert included.data["interference_count"] == 1


async def test_a_components_list_limits_the_pairs() -> None:
    adapter = await _assembly((0, 0, 0), (4, 0, 0), (8, 0, 0))

    one = await adapter.check_interference({"components": ["component-3"]})
    two = await adapter.check_interference(
        {"components": ["component-3", "component-1"]}
    )

    assert _found(one.data) == [
        (["component-1", "component-3"], 200.0),
        (["component-2", "component-3"], 600.0),
    ]
    assert _found(two.data) == [(["component-1", "component-3"], 200.0)]
    assert two.data["scope"] == ["component-1", "component-3"]


async def test_coincident_counts_touching_boxes_as_solidworks_detection_does() -> None:
    adapter = await _assembly((0, 0, 0), (10, 0, 0))

    plain = await adapter.check_interference({})
    touching = await adapter.check_interference({"coincident": True})

    assert plain.data["interference_count"] == 0
    assert _found(touching.data) == [(["component-1", "component-2"], 0.0)]
    assert touching.data["coincident_treated_as_interference"] is True
    assert touching.data["method"] == INTERFERENCE_DETECTION
    assert touching.data["note"] == fallback_note(COINCIDENT_REASON)
