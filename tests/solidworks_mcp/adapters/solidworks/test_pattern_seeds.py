"""What a listed pattern repeats already: read from its parents, left out before selecting.

A pattern listed beside its own seeds makes SolidWorks refuse the whole call
(Aggrewell enclosure lid, SOLIDWORKS 2026). Its seeds are its parents that
aren't reference geometry; when there are more of those than its seed count,
which are seeds is unsure, and they are left out only after a refusal.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks.pattern_seeds import (
    NOT_SEEDS,
    PATTERN_TYPES,
    PatternParents,
    SeedPlan,
    no_plan,
    plan,
    plan_for,
)

from .fake_pattern_part import (
    LID_FEATURES,
    LID_LEFT_OUT,
    FakeAdapter,
    FakePatternPart,
    vent_lid,
)


def _tree(**features: tuple[str, tuple[str, ...], int | None]) -> Any:
    """``kind_of`` and ``parents_of`` over ``name=(kind, parents, seed_count)``."""

    def kind_of(name: str) -> str | None:
        return features[name][0] if name in features else None

    def parents_of(name: str) -> PatternParents:
        _kind, parents, count = features[name]
        return PatternParents(parents, count)

    return kind_of, parents_of


# ---- the plan --------------------------------------------------------------------


def test_the_lid_leaves_out_each_rings_hole_and_chamfer() -> None:
    lid = vent_lid()

    seeds = plan_for(FakeAdapter(lid), LID_FEATURES)

    assert seeds.attempts() == [
        [
            "Vent_Center_Hole",
            "Vent_Center_CS",
            "Vent_Ring1_Pattern",
            "Vent_Ring2_Pattern",
        ]
    ]
    assert seeds.left_out(seeds.attempts()[0]) == LID_LEFT_OUT
    assert seeds.patterns == ["Vent_Ring1_Pattern", "Vent_Ring2_Pattern"]


def test_a_chamfer_listed_with_its_hole_keeps_both() -> None:
    kind_of, parents_of = _tree(Hole=("Cut", (), None), CS=("Chamfer", ("Hole",), None))

    seeds = plan(["Hole", "CS"], kind_of, parents_of)

    assert seeds.attempts() == [["Hole", "CS"]]
    assert seeds.left_out(["Hole", "CS"]) == []
    assert seeds.left_out_field(["Hole", "CS"]) == {}


def test_a_pattern_of_a_pattern_carries_the_inner_seeds_too() -> None:
    kind_of, parents_of = _tree(
        Hole=("Cut", (), None),
        CS=("Chamfer", ("Hole",), None),
        Ring=("CirPattern", ("Hole", "CS"), 2),
        Grid=("LPattern", ("Ring",), 1),
    )

    seeds = plan(["Grid", "Hole", "CS", "Ring"], kind_of, parents_of)

    assert seeds.attempts() == [["Grid"]]
    assert seeds.left_out(["Grid"]) == [
        {"feature": "Hole", "patterned_by": "Grid"},
        {"feature": "CS", "patterned_by": "Grid"},
        {"feature": "Ring", "patterned_by": "Grid"},
    ]


def test_more_parents_than_seeds_leaves_them_in_until_a_refusal() -> None:
    # A direction edge on the plate makes the plate a parent too.
    kind_of, parents_of = _tree(
        Plate=("Extrusion", (), None),
        Slot=("Cut", (), None),
        Row=("LPattern", ("Slot", "Plate"), 1),
    )

    seeds = plan(["Slot", "Row"], kind_of, parents_of)

    assert seeds.sure == {}
    assert seeds.unsure == {"Slot": "Row"}
    assert seeds.attempts() == [["Slot", "Row"], ["Row"]]
    assert seeds.left_out(["Slot", "Row"]) == []
    assert seeds.left_out_field(["Row"]) == {
        "left_out": [{"feature": "Slot", "patterned_by": "Row"}]
    }


def test_an_unread_seed_count_is_unsure() -> None:
    kind_of, parents_of = _tree(
        Boss=("Boss", (), None), Fill=("APattern", ("Boss",), None)
    )

    seeds = plan(["Boss", "Fill"], kind_of, parents_of)

    assert seeds.unsure == {"Boss": "Fill"}


def test_one_pattern_sure_of_a_seed_wins_over_one_unsure() -> None:
    kind_of, parents_of = _tree(
        Plate=("Extrusion", (), None),
        Slot=("Cut", (), None),
        Row=("LPattern", ("Slot", "Plate"), 1),
        Ring=("CirPattern", ("Slot",), 1),
    )

    seeds = plan(["Slot", "Row", "Ring"], kind_of, parents_of)

    assert seeds.sure == {"Slot": "Ring"}
    assert seeds.unsure == {}
    assert seeds.attempts() == [["Row", "Ring"]]


def test_a_sure_seed_is_not_made_unsure_by_a_later_pattern() -> None:
    kind_of, parents_of = _tree(
        Plate=("Extrusion", (), None),
        Slot=("Cut", (), None),
        Ring=("CirPattern", ("Slot",), 1),
        Row=("LPattern", ("Slot", "Plate"), 1),
    )

    seeds = plan(["Slot", "Ring", "Row"], kind_of, parents_of)

    assert seeds.sure == {"Slot": "Ring"}
    assert seeds.unsure == {}


def test_an_inner_pattern_read_unsurely_makes_its_seeds_unsure() -> None:
    kind_of, parents_of = _tree(
        Plate=("Extrusion", (), None),
        Hole=("Cut", (), None),
        Ring=("CirPattern", ("Hole", "Plate"), 1),
        Grid=("LPattern", ("Ring",), 1),
    )

    seeds = plan(["Hole", "Ring", "Grid"], kind_of, parents_of)

    assert seeds.sure == {"Ring": "Grid"}
    assert seeds.unsure == {"Hole": "Ring"}


def test_a_pattern_reached_twice_is_read_once() -> None:
    reads: list[str] = []
    kind_of, read = _tree(
        Hole=("Cut", (), None),
        Ring=("CirPattern", ("Hole",), 1),
        Left=("MirrorPattern", ("Ring",), 1),
        Both=("LPattern", ("Ring", "Left"), 2),
    )

    def parents_of(name: str) -> PatternParents:
        reads.append(name)
        return read(name)

    seeds = plan(["Hole", "Both"], kind_of, parents_of)

    assert seeds.sure == {"Hole": "Both"}
    assert reads == ["Both", "Ring", "Left"]


def test_a_body_mirror_is_named_but_its_parents_stay() -> None:
    kind_of, parents_of = _tree(
        Boss=("Extrusion", (), None), Mirror1=("MirrorSolid", ("Boss",), None)
    )

    seeds = plan(["Boss", "Mirror1"], kind_of, parents_of)

    assert seeds.attempts() == [["Boss", "Mirror1"]]
    assert seeds.patterns == ["Mirror1"]


def test_a_feature_not_listed_is_never_left_out() -> None:
    kind_of, parents_of = _tree(
        Hole=("Cut", (), None), Ring=("CirPattern", ("Hole",), 1)
    )

    assert plan(["Ring"], kind_of, parents_of).left_out(["Ring"]) == []


def test_no_plan_leaves_out_nothing() -> None:
    seeds = no_plan(["Body1", "Body2"])

    assert seeds.attempts() == [["Body1", "Body2"]]
    assert seeds.refusal(["Body1", "Body2"]) == ""


@pytest.mark.parametrize(
    ("patterns", "used", "expected"),
    [
        ([], ["Hole"], ""),
        (["Ring"], ["Hole"], ""),
        (
            ["Ring"],
            ["Ring"],
            " Ring is a pattern or mirror, and SolidWorks refuses one listed "
            "with features it already repeats.",
        ),
        (
            ["Ring", "Row"],
            ["Ring", "Row"],
            " Ring, Row are patterns or mirrors, and SolidWorks refuses one listed "
            "with features it already repeats.",
        ),
    ],
)
def test_the_refusal_names_the_patterns_still_listed(
    patterns: list[str], used: list[str], expected: str
) -> None:
    assert SeedPlan(used, patterns=patterns).refusal(used) == expected


def test_a_listed_thread_feature_is_named_in_the_refusal() -> None:
    # A circular pattern of Post_Thread_M5 made nothing on the Aggrewell tray.
    kind_of, parents_of = _tree(
        Post_Thread_M5=("SweepThread", (), None),
        Boss=("Extrusion", (), None),
        Ring=("CirPattern", ("Hole",), 1),
        Hole=("Cut", (), None),
    )

    one = plan(["Post_Thread_M5"], kind_of, parents_of)
    both = plan(["Boss", "Ring", "Post_Thread_M5"], kind_of, parents_of)

    assert one.threads == ["Post_Thread_M5"]
    assert one.refusal(["Post_Thread_M5"]) == (
        " Post_Thread_M5 is a Thread feature, which SolidWorks made no pattern "
        "of: cut each thread with its own create_thread call."
    )
    assert one.refusal([]) == ""
    assert both.refusal(["Ring", "Post_Thread_M5"]).startswith(
        " Ring is a pattern or mirror"
    )
    assert "Post_Thread_M5 is a Thread feature" in both.refusal(
        ["Ring", "Post_Thread_M5"]
    )
    assert (
        SeedPlan(["A", "B"], threads=["A", "B"])
        .refusal(["A", "B"])
        .startswith(" A, B are Thread features")
    )


def test_the_type_lists_are_the_helps() -> None:
    assert {"LPattern", "CirPattern", "MirrorPattern"} <= PATTERN_TYPES
    assert "MirrorSolid" not in PATTERN_TYPES
    assert {"RefAxis", "RefPlane", "ProfileFeature", "3DProfileFeature"} <= NOT_SEEDS
    assert not PATTERN_TYPES & NOT_SEEDS


# ---- reading the part --------------------------------------------------------------


def test_reference_geometry_among_the_parents_is_no_seed() -> None:
    part = FakePatternPart().add("Ring_Axis", "RefAxis").add("Hole")
    part.add(
        "Ring", "CirPattern", parents=("Hole", "Ring_Axis", "Top Plane"), seed_count=1
    )

    seeds = plan_for(FakeAdapter(part), ["Hole", "Ring", "Ring_Axis"])

    assert seeds.sure == {"Hole": "Ring"}


def test_each_feature_type_is_read_once() -> None:
    lid = vent_lid()

    plan_for(FakeAdapter(lid), LID_FEATURES)

    assert sorted(lid.type_reads) == sorted(set(lid.type_reads))


def test_a_qualified_name_is_looked_up_and_matched_bare() -> None:
    part = FakePatternPart().add("Hole")
    part.add("Ring", "CirPattern", parents=("Hole",), seed_count=1)

    seeds = plan_for(FakeAdapter(part), ["Hole@Lid", "Ring@Lid", "Gone"])

    assert seeds.patterns == ["Ring@Lid"]
    assert seeds.attempts() == [["Ring@Lid", "Gone"]]
    assert seeds.left_out(["Ring@Lid", "Gone"]) == [
        {"feature": "Hole@Lid", "patterned_by": "Ring@Lid"}
    ]


@pytest.mark.parametrize("count", [None, True, "two"])
def test_a_seed_count_that_is_not_a_number_is_unsure(count: Any) -> None:
    part = FakePatternPart().add("Hole")
    part.add("Ring", "CirPattern", parents=("Hole",), seed_count=count)

    seeds = plan_for(FakeAdapter(part), ["Hole", "Ring"])

    assert seeds.unsure == {"Hole": "Ring"}


def test_a_float_seed_count_counts() -> None:
    part = FakePatternPart().add("Hole")
    part.add("Ring", "CirPattern", parents=("Hole",), seed_count=1.0)

    assert plan_for(FakeAdapter(part), ["Hole", "Ring"]).sure == {"Hole": "Ring"}


def test_a_pattern_without_a_definition_is_unsure() -> None:
    part = FakePatternPart().add("Hole")
    part.add("Ring", "CirPattern", parents=("Hole",), seed_count=1)
    part.features["Ring"].GetDefinition = lambda: None  # type: ignore[method-assign]

    assert plan_for(FakeAdapter(part), ["Hole", "Ring"]).unsure == {"Hole": "Ring"}


def test_a_parent_without_a_name_and_a_single_parent_are_read() -> None:
    part = FakePatternPart().add("Hole")
    part.add("Ring", "CirPattern", seed_count=1)
    nameless = SimpleNamespace(Name="", GetTypeName2=lambda: "Cut")
    part.features["Ring"].GetParents = lambda: (nameless, part.features["Hole"])  # type: ignore[method-assign]

    assert plan_for(FakeAdapter(part), ["Hole", "Ring"]).sure == {"Hole": "Ring"}

    part.features["Ring"].GetParents = lambda: part.features["Hole"]  # type: ignore[method-assign]

    assert plan_for(FakeAdapter(part), ["Hole", "Ring"]).sure == {"Hole": "Ring"}


def test_a_type_that_is_not_text_is_no_pattern() -> None:
    part = FakePatternPart().add("Hole")
    part.features["Hole"].GetTypeName2 = lambda: 7  # type: ignore[method-assign]

    assert plan_for(FakeAdapter(part), ["Hole"]).patterns == []
