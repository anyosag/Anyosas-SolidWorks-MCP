"""``mirror_feature`` and ``pattern_circular`` leave out what a listed pattern repeats.

On the Aggrewell lid (SOLIDWORKS 2026) a mirror of the vents, listed with each
ring's hole, chamfer and circular pattern, returned nothing
("InsertMirrorFeature returned nothing"). The fake part refuses the same way.
"""

from __future__ import annotations

from solidworks_mcp.adapters.solidworks.features import (
    _mirror_feature_impl,
    _pattern_circular_impl,
)

from .fake_pattern_part import (
    LID_FEATURES,
    LID_LEFT_OUT,
    FakeAdapter,
    FakePatternPart,
    vent_lid,
)

_KEPT = [
    "Vent_Center_Hole",
    "Vent_Center_CS",
    "Vent_Ring1_Pattern",
    "Vent_Ring2_Pattern",
]


def _rows() -> FakePatternPart:
    """A slot in rows along an edge of the plate, which is a parent but no seed."""
    part = FakePatternPart().add("Plate", "Extrusion").add("Slot")
    return part.add(
        "Row", "LPattern", parents=("Slot", "Plate"), seed_count=1, seeds=("Slot",)
    )


# ---- mirror_feature ------------------------------------------------------------


def test_the_lid_mirror_is_made_without_the_seeds_its_rings_repeat() -> None:
    lid = vent_lid()

    result = _mirror_feature_impl(
        FakeAdapter(lid), LID_FEATURES, "Right Plane", True, False
    )

    assert result.is_success, result.error
    assert lid.calls == [_KEPT]
    assert result.data["mirrored"] == _KEPT
    assert result.data["left_out"] == LID_LEFT_OUT


def test_a_mirror_of_a_pattern_of_a_pattern_keeps_only_the_outer() -> None:
    lid = vent_lid()
    lid.add(
        "Vent_Grid",
        "LPattern",
        parents=("Vent_Ring1_Pattern", "Right Plane"),
        seed_count=1,
    )

    result = _mirror_feature_impl(
        FakeAdapter(lid),
        ["Vent_Ring1_Hole", "Vent_Ring1_CS", "Vent_Ring1_Pattern", "Vent_Grid"],
        "Top Plane",
        True,
        False,
    )

    assert result.data["mirrored"] == ["Vent_Grid"]
    assert result.data["left_out"] == [
        {"feature": "Vent_Ring1_Hole", "patterned_by": "Vent_Ring1_Pattern"},
        {"feature": "Vent_Ring1_CS", "patterned_by": "Vent_Ring1_Pattern"},
        {"feature": "Vent_Ring1_Pattern", "patterned_by": "Vent_Grid"},
    ]


def test_a_mirror_retries_without_an_unsure_seed_solidworks_refused() -> None:
    part = _rows()

    result = _mirror_feature_impl(
        FakeAdapter(part), ["Slot", "Row"], "Right Plane", True, False
    )

    assert part.calls == [["Slot", "Row"], ["Row"]]
    assert result.data["left_out"] == [{"feature": "Slot", "patterned_by": "Row"}]


def test_a_refused_mirror_with_a_pattern_listed_names_the_cause() -> None:
    lid = vent_lid()
    lid.refuse_all = True

    result = _mirror_feature_impl(
        FakeAdapter(lid), ["Vent_Ring2_Pattern"], "Right Plane", True, False
    )

    assert (result.error or "").endswith(
        "InsertMirrorFeature returned nothing (sources=['Vent_Ring2_Pattern'], "
        "plane=Right Plane). Vent_Ring2_Pattern is a pattern or mirror, and "
        "SolidWorks refuses one listed with features it already repeats."
    )


def test_a_body_mirror_reads_no_feature_types() -> None:
    lid = vent_lid()

    result = _mirror_feature_impl(
        FakeAdapter(lid), ["Body1"], "Right Plane", True, True
    )

    assert lid.type_reads == []
    assert "Failed to select body to mirror: Body1" in (result.error or "")


# ---- pattern_circular ------------------------------------------------------------


def test_a_ring_of_rings_is_made_without_their_seeds() -> None:
    lid = vent_lid().add("Lid_Axis", "RefAxis")

    result = _pattern_circular_impl(
        FakeAdapter(lid), LID_FEATURES, "Lid_Axis", 4, 360.0, True
    )

    assert result.is_success, result.error
    assert lid.calls == [_KEPT]
    assert result.data["patterned"] == _KEPT
    assert result.data["left_out"] == LID_LEFT_OUT


def test_a_circular_pattern_retries_without_an_unsure_seed() -> None:
    part = _rows().add("Hub_Axis", "RefAxis")

    result = _pattern_circular_impl(
        FakeAdapter(part), ["Slot", "Row"], "Hub_Axis", 3, 360.0, True
    )

    assert part.calls == [["Slot", "Row"], ["Row"]]
    assert result.data["patterned"] == ["Row"]


def test_a_refused_circular_pattern_names_the_cause() -> None:
    lid = vent_lid().add("Lid_Axis", "RefAxis")
    lid.refuse_all = True

    result = _pattern_circular_impl(
        FakeAdapter(lid),
        ["Vent_Center_Hole", "Vent_Ring1_Pattern"],
        "Lid_Axis",
        3,
        360.0,
        True,
    )

    assert (result.error or "").endswith(
        "FeatureCircularPattern5 returned nothing (features=['Vent_Center_Hole', "
        "'Vent_Ring1_Pattern'], axis=Lid_Axis). Vent_Ring1_Pattern is a pattern or "
        "mirror, and SolidWorks refuses one listed with features it already repeats."
    )


def test_a_refused_pattern_of_plain_features_says_only_what_was_tried() -> None:
    lid = vent_lid().add("Lid_Axis", "RefAxis")
    lid.refuse_all = True

    result = _pattern_circular_impl(
        FakeAdapter(lid), ["Vent_Center_Hole"], "Lid_Axis", 3, 360.0, True
    )

    assert (result.error or "").endswith(
        "FeatureCircularPattern5 returned nothing (features=['Vent_Center_Hole'], "
        "axis=Lid_Axis)."
    )
