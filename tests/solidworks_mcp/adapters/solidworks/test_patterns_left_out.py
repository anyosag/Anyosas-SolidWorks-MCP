"""``pattern_linear`` leaves out the features a listed pattern repeats, before selecting them.

On the Aggrewell lid (SOLIDWORKS 2026) a linear pattern of the vents, listed
with each ring's hole, chamfer and circular pattern, returned nothing; the
ring alone carried its seed vent too. The fake part refuses the same way.
"""

from __future__ import annotations

from solidworks_mcp.adapters.solidworks import patterns

from .fake_pattern_part import (
    LID_FEATURES,
    LID_LEFT_OUT,
    FakeAdapter,
    FakePatternPart,
    vent_lid,
)


def _edge_row() -> FakePatternPart:
    """A row of slots whose direction edge is the plate's: the plate is a parent, not a seed."""
    part = FakePatternPart().add("Plate", "Extrusion").add("Slot")
    return part.add(
        "Row", "LPattern", parents=("Slot", "Plate"), seed_count=1, seeds=("Slot",)
    )


def test_the_lid_grid_is_made_without_the_seeds_its_rings_repeat() -> None:
    lid = vent_lid()

    result = patterns.pattern_linear(
        FakeAdapter(lid), LID_FEATURES, "x", 2, 35.7, "z", 2, 35.7, True
    )

    assert result.is_success, result.error
    kept = [
        "Vent_Center_Hole",
        "Vent_Center_CS",
        "Vent_Ring1_Pattern",
        "Vent_Ring2_Pattern",
    ]
    assert lid.calls == [kept]
    assert result.data["patterned"] == kept
    assert result.data["left_out"] == LID_LEFT_OUT
    assert result.data["instances"] == 4


def test_a_chamfer_and_its_hole_are_both_patterned() -> None:
    lid = vent_lid()

    result = patterns.pattern_linear(
        FakeAdapter(lid), ["Vent_Center_Hole", "Vent_Center_CS"], "x", 2, 35.7
    )

    assert result.data["patterned"] == ["Vent_Center_Hole", "Vent_Center_CS"]
    assert "left_out" not in result.data


def test_an_unsure_seed_is_left_out_only_after_solidworks_refuses_it() -> None:
    part = _edge_row()

    result = patterns.pattern_linear(FakeAdapter(part), ["Slot", "Row"], "x", 2, 30.0)

    assert result.is_success, result.error
    assert part.calls == [["Slot", "Row"], ["Row"]]
    assert result.data["patterned"] == ["Row"]
    assert result.data["left_out"] == [{"feature": "Slot", "patterned_by": "Row"}]


def test_the_direction_edges_feature_listed_stays_in() -> None:
    part = _edge_row()

    result = patterns.pattern_linear(FakeAdapter(part), ["Plate", "Row"], "x", 2, 30.0)

    assert part.calls == [["Plate", "Row"]]
    assert result.data["patterned"] == ["Plate", "Row"]
    assert "left_out" not in result.data


def test_a_refusal_with_a_pattern_listed_names_the_cause() -> None:
    lid = vent_lid()
    lid.refuse_all = True

    result = patterns.pattern_linear(
        FakeAdapter(lid), ["Vent_Ring1_Hole", "Vent_Ring1_Pattern"], "x", 2, 35.7
    )

    assert not result.is_success
    assert result.error == (
        "FeatureLinearPattern5 returned nothing (seeds=['Vent_Ring1_Pattern'], "
        "direction='x'). Vent_Ring1_Pattern is a pattern or mirror, and "
        "SolidWorks refuses one listed with features it already repeats."
    )


def test_both_tries_refused_name_what_was_tried_last() -> None:
    part = _edge_row()
    part.refuse_all = True

    result = patterns.pattern_linear(FakeAdapter(part), ["Slot", "Row"], "x", 2, 30.0)

    assert part.calls == [["Slot", "Row"], ["Row"]]
    assert "(seeds=['Row'], direction='x')." in (result.error or "")


def test_bodies_are_not_read_as_features() -> None:
    lid = vent_lid()

    result = patterns.pattern_linear(
        FakeAdapter(lid), ["Vent_Ring1_Hole"], "x", 2, 35.7, bodies=True
    )

    assert lid.type_reads == []
    assert "body to pattern: Vent_Ring1_Hole" in (result.error or "")
