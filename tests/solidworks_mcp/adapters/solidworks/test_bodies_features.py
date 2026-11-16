"""Listing, combining, moving, deleting, splitting and inserting bodies on a fake part."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import bodies as bd
from solidworks_mcp.adapters.solidworks import io as io_module
from solidworks_mcp.adapters.solidworks.clearance import InvalidRequest

from .fake_bodies import Body, Harness, World, names

_A = [0.0, 0.0, 0.0, 10.0, 10.0, 10.0]
_B = [5.0, 0.0, 0.0, 15.0, 10.0, 10.0]
_C = [30.0, 0.0, 0.0, 40.0, 10.0, 10.0]


@pytest.fixture
def part() -> World:
    return World({"A": _A, "B": _B, "C": _C})


# ---- list_bodies ---------------------------------------------------------------


def test_each_body_with_its_facts(part: World) -> None:
    out = bd.list_bodies(Harness(part))

    assert out["count"] == 3
    first = out["bodies"][0]
    assert first == {
        "name": "A",
        "volume_mm3": 1000.0,
        "surface_area_mm2": 600.0,
        "center_of_mass_mm": [5.0, 5.0, 5.0],
        "box_mm": _A,
        "size_mm": [10.0, 10.0, 10.0],
        "face_count": 6,
        "visible": True,
    }
    # Zero-argument methods were flagged before they were called.
    assert {"GetBodyBox", "GetFaceCount"} <= part.body("A").flagged
    assert "GetBodies2" in part.model.flagged


def test_visible_only_leaves_hidden_bodies_out(part: World) -> None:
    part.body("B").Visible = False

    out = bd.list_bodies(Harness(part), visible_only=True)

    assert [b["name"] for b in out["bodies"]] == ["A", "C"]


def test_a_body_that_tells_nothing_reads_as_unknown(part: World) -> None:
    body = part.body("A")
    body.GetBodyBox = lambda: None  # type: ignore[method-assign]
    body.GetMassProperties = lambda _d: ()  # type: ignore[method-assign]
    body.GetFaceCount = lambda: "?"  # type: ignore[method-assign]
    body.Visible = None  # type: ignore[assignment]

    facts = bd.list_bodies(Harness(part))["bodies"][0]

    assert facts["volume_mm3"] is None and facts["box_mm"] is None
    assert facts["size_mm"] is None and facts["face_count"] is None
    assert facts["visible"] is None


def test_an_empty_part_has_no_bodies() -> None:
    assert bd.list_bodies(Harness(World())) == {"bodies": [], "count": 0}


def test_a_raw_dispatch_is_wrapped_before_it_is_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapped = Body(World(), "Wrapped", _A)
    monkeypatch.setattr(
        io_module, "_dynamic", SimpleNamespace(Dispatch=lambda _o: wrapped)
    )

    assert bd._wrapped(Harness(World()), object(), "Copy") is wrapped
    assert "Copy" in wrapped.flagged


# ---- selection and features ------------------------------------------------------


def test_a_body_select2_refuses_falls_back_to_select_by_id(part: World) -> None:
    part.body_select_fails = True

    bd.select_marked(Harness(part), part.model, [("B", part.body("B"), 2, "body")])

    assert part.by_id == [("B", "SOLIDBODY")]
    assert [(e.Name, m) for e, m in part.selection] == [("B", 2)]


def test_what_cannot_be_selected_is_an_error(part: World) -> None:
    part.body_select_fails = True
    ghost = Body(part, "Ghost", _A)

    with pytest.raises(RuntimeError, match="did not select Ghost"):
        bd.select_marked(Harness(part), part.model, [("Ghost", ghost, 1, "body")])

    assert part.selection == []


def test_a_face_that_cannot_be_selected_is_named_as_such(part: World) -> None:
    face = SimpleNamespace(Select4=lambda *_a: False)

    with pytest.raises(RuntimeError, match="did not select a tool face"):
        bd.select_marked(Harness(part), part.model, [(None, face, 4, "face")])


def test_a_part_without_a_feature_manager_is_an_error(part: World) -> None:
    part.model.FeatureManager = None  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="no FeatureManager"):
        bd.feature_manager(Harness(part), part.model)


def test_a_feature_without_a_name() -> None:
    assert bd.feature_name(Harness(World()), None) is None
    assert bd.feature_name(Harness(World()), SimpleNamespace(Name="")) is None


# ---- combine_bodies --------------------------------------------------------------


def test_subtract_marks_the_main_body_1_and_the_tools_2(part: World) -> None:
    out = bd.combine_bodies(Harness(part), "subtract", "A", ["B"])

    assert out["feature"] == "Combine1"
    assert out["form"] == "preselected (main body at mark 1)"
    assert part.combine_calls == [(bd.BODY_CUT, False, 0, [1, 2])]
    assert out["main_volume_before_mm3"] == 1000.0
    assert out["result_volume_mm3"] == 500.0
    assert [r["name"] for r in out["result"]] == ["A"]
    assert out["bodies_before"] == ["A", "B", "C"]
    assert out["bodies_after"] == ["A", "C"]


@pytest.mark.parametrize("operation", ["add", "common"])
def test_add_and_common_mark_every_body_2(part: World, operation: str) -> None:
    out = bd.combine_bodies(Harness(part), operation, "A", ["B"])

    assert out["form"] == "preselected (main body at mark 2)"
    assert part.combine_calls[0][3] == [2, 2]
    expected = 1500.0 if operation == "add" else 500.0
    assert out["result_volume_mm3"] == expected


def test_add_falls_back_to_the_main_body_at_mark_1(part: World) -> None:
    part.combine_marks = {(bd.BODY_ADD, (1, 2))}

    out = bd.combine_bodies(Harness(part), "add", "A", ["B"])

    assert out["form"] == "preselected (main body at mark 1)"
    assert [c[3] for c in part.combine_calls] == [[2, 2], [1, 2]]


@pytest.mark.parametrize(
    ("operation", "main_given", "tools"), [("subtract", True, 1), ("common", False, 2)]
)
def test_the_bodies_are_passed_directly_last(
    part: World, operation: str, main_given: bool, tools: int
) -> None:
    part.combine_marks = set()
    part.combine_direct_works = True

    out = bd.combine_bodies(Harness(part), operation, "A", ["B"])

    assert out["form"] == "bodies passed directly"
    assert part.combine_calls[-1][1:3] == (main_given, tools)


def test_no_combine_is_an_error(part: World) -> None:
    part.combine_fails = True

    with pytest.raises(RuntimeError, match="no Combine \\(common\\) feature from A"):
        bd.combine_bodies(Harness(part), "common", "A", ["C"])


@pytest.mark.parametrize("tools", [["A"], ["B", "B"]])
def test_the_main_body_among_the_tools_is_invalid(
    part: World, tools: list[str]
) -> None:
    with pytest.raises(InvalidRequest, match="each tool once"):
        bd.combine_bodies(Harness(part), "add", "A", tools)


def test_a_missing_body_is_not_found(part: World) -> None:
    with pytest.raises(
        LookupError, match="not found: Z. The part's solid bodies: A, B, C"
    ):
        bd.combine_bodies(Harness(part), "add", "A", ["Z"])


# ---- move_copy_body --------------------------------------------------------------


def test_a_move_shifts_the_bodies(part: World) -> None:
    out = bd.move_bodies(
        Harness(part), part.model, ["C"], [0, 5, 0], 0.0, "z", None, False, 1
    )

    assert out["features"] == ["Body-Move/Copy1"]
    assert part.body("C").box == [30, 5, 0, 40, 15, 10]
    assert out["moved"] == [
        {
            "name": "C",
            "center_before_mm": [35.0, 5.0, 5.0],
            "center_after_mm": [35.0, 10.0, 5.0],
            "center_shift_mm": [0.0, 5.0, 0.0],
        }
    ]
    assert out["new_bodies"] == [] and out["copy"] is False


def test_a_shift_and_a_turn_are_two_features(part: World) -> None:
    out = bd.move_bodies(
        Harness(part), part.model, ["A"], [10, 0, 0], 90, "y", [10, 0, 0], False, 1
    )

    assert out["features"] == ["Body-Move/Copy1", "Body-Move/Copy2"]
    # Shifted to x 10..20, then turned a quarter about +Y through (10, 0, 0).
    assert part.body("A").box == pytest.approx([10, 0, -10, 20, 10, 0])


def test_copies_are_new_bodies(part: World) -> None:
    out = bd.move_bodies(
        Harness(part), part.model, ["A"], [20, 0, 0], 0.0, "z", None, True, 2
    )

    assert [b["name"] for b in out["new_bodies"]] == [
        "Body-Move/Copy1[1]",
        "Body-Move/Copy1[2]",
    ]
    assert part.body("Body-Move/Copy1[2]").box == [40, 0, 0, 50, 10, 10]
    assert part.body("A").box == _A
    assert out["moved"] == []


def test_a_copy_turned_without_a_shift_is_one_feature(part: World) -> None:
    out = bd.move_bodies(
        Harness(part), part.model, ["A"], None, 180, "z", None, True, 1
    )

    assert out["features"] == ["Body-Move/Copy1"]
    assert part.body("Body-Move/Copy1[1]").box == pytest.approx([-10, -10, 0, 0, 0, 10])


def test_copies_shifted_then_turned_turn_the_copies(part: World) -> None:
    out = bd.move_bodies(
        Harness(part), part.model, ["A"], [0, 0, 20], 90, "x", None, True, 1
    )

    assert out["features"] == ["Body-Move/Copy1", "Body-Move/Copy2"]
    assert part.body("A").box == _A
    assert len(out["new_bodies"]) == 1


def test_nothing_to_move_is_invalid(part: World) -> None:
    with pytest.raises(InvalidRequest, match="nothing would move"):
        bd.move_bodies(
            Harness(part), part.model, ["A"], [0, 0, 0], 0, "z", None, False, 1
        )


def test_no_move_feature_is_an_error(part: World) -> None:
    part.move_fails = True

    with pytest.raises(RuntimeError, match="no Move/Copy Body feature to copy A"):
        bd.move_bodies(
            Harness(part), part.model, ["A"], [1, 0, 0], 0, "z", None, True, 1
        )


def test_a_moved_body_that_vanished_reports_only_its_name(part: World) -> None:
    def move_and_rename(bodies: list[Any], *_args: Any) -> Any:
        bodies[0].Name = "Renamed"
        return part.feature("Body-Move/Copy")

    part.move = move_and_rename  # type: ignore[method-assign]

    out = bd.move_bodies(
        Harness(part), part.model, ["A"], [1, 0, 0], 0, "z", None, False, 1
    )

    assert out["moved"] == [{"name": "A"}]


# ---- delete_keep_body ------------------------------------------------------------


def test_delete_bodies(part: World) -> None:
    out = bd.delete_keep_body(Harness(part), ["B"], False)

    assert out["feature"] == "Body-Delete/Keep1"
    assert out["removed"] == ["B"] and out["expected_removed"] == ["B"]
    assert names(part) == ["A", "C"]


def test_keep_bodies(part: World) -> None:
    out = bd.delete_keep_body(Harness(part), ["B"], True)

    assert out["removed"] == ["A", "C"] and names(part) == ["B"]


def test_keeping_every_body_is_invalid(part: World) -> None:
    with pytest.raises(InvalidRequest, match="deletes nothing"):
        bd.delete_keep_body(Harness(part), ["A", "B", "C"], True)


def test_no_delete_feature_is_an_error(part: World) -> None:
    part.delete_fails = True

    with pytest.raises(RuntimeError, match="no Body-Delete/Keep feature for B"):
        bd.delete_keep_body(Harness(part), ["B"], False)


# ---- split_body ------------------------------------------------------------------


@pytest.fixture
def plate() -> World:
    world = World(
        {"Plate": [-20.0, 0.0, -10.0, 20.0, 5.0, 10.0], "Pin": [30, 0, 0, 32, 5, 2]}
    )
    world.add_plane("Right Plane", 0, 0.0)
    world.add_plane("Plane1", 1, 2.0)
    return world


def test_a_default_plane_by_its_short_name(plate: World) -> None:
    out = bd.split_body(Harness(plate), "Right", None, None)

    assert out["tool"] == "Right Plane"
    assert out["feature"] == "Split1"
    assert [p["name"] for p in out["pieces"]] == ["Split1[1]", "Split1[2]"]
    assert out["pieces"][0]["box_mm"] == [-20.0, 0.0, -10.0, 0.0, 5.0, 10.0]
    assert out["bodies_after"] == ["Pin", "Split1[1]", "Split1[2]"]
    assert "PreSplitBody2" in plate.model.FeatureManager.flagged


def test_only_the_named_body_is_split(plate: World) -> None:
    plate.body("Pin").box = [30, 0, 0, 32, 5, 2]
    plate.add_plane("Plane2", 2, 1.0)

    out = bd.split_body(Harness(plate), "Plane2", None, "Pin")

    assert len(out["pieces"]) == 2 and out["body"] == "Pin"
    assert "Plate" in out["bodies_after"]


def test_a_body_the_tool_misses_is_an_error(plate: World) -> None:
    with pytest.raises(RuntimeError, match="doesn't split body Pin"):
        bd.split_body(Harness(plate), "Right", None, "Pin")


def test_a_sketch_by_its_name(plate: World) -> None:
    plate.add_plane("Sketch3", 1, 1.0)

    out = bd.split_body(Harness(plate), None, "Sketch3", None)

    assert out["tool"] == "Sketch3" and len(out["pieces"]) == 4


def test_a_tool_that_splits_nothing_is_an_error(plate: World) -> None:
    plate.add_plane("Far", 0, 100.0)

    with pytest.raises(RuntimeError, match="Far splits no body"):
        bd.split_body(Harness(plate), "Far", None, None)


def test_no_split_feature_is_an_error(plate: World) -> None:
    plate.model.FeatureManager.PostSplitBody2 = lambda *_a: None  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="no Split feature along Right Plane"):
        bd.split_body(Harness(plate), "Right", None, None)


@pytest.mark.parametrize(
    ("plane", "sketch", "message"),
    [
        ("Plane9", None, "Plane not found: Plane9"),
        (None, "Sketch9", "Sketch not found"),
    ],
)
def test_an_unknown_tool_is_not_found(
    plate: World, plane: str | None, sketch: str | None, message: str
) -> None:
    with pytest.raises(LookupError, match=message):
        bd.split_body(Harness(plate), plane, sketch, None)


# ---- insert_part -----------------------------------------------------------------


@pytest.fixture
def holder(tmp_path: Path, part: World) -> str:
    path = tmp_path / "holder.SLDPRT"
    path.write_text("")
    part.insertable[str(path)] = {
        "Label1": [0, 0, 0, 5, 1, 2],
        "Label2": [8, 0, 0, 12, 1, 2],
    }
    return str(path)


def test_a_part_is_inserted_as_bodies(part: World, holder: str) -> None:
    out = bd.insert_part(Harness(part), holder)

    assert out["feature"] == "holder-1"
    assert out["bodies_added"] == ["Label1", "Label2"]
    assert out["bodies"][1]["box_mm"] == [8.0, 0.0, 0.0, 12.0, 1.0, 2.0]
    assert out["move_features"] == []
    assert part.inserted == (holder, bd.INSERT_PART_IMPORT_SOLIDS, "")  # type: ignore[attr-defined]


def test_inserted_bodies_are_placed_and_the_link_broken(
    part: World, holder: str
) -> None:
    out = bd.insert_part(
        Harness(part), holder, "Default", [0, 10, 0], 0.0, "z", None, True
    )

    assert out["move_features"] == ["Body-Move/Copy1"]
    assert out["bodies"][0]["box_mm"] == [0.0, 10.0, 0.0, 5.0, 11.0, 2.0]
    assert part.inserted == (  # type: ignore[attr-defined]
        holder,
        bd.INSERT_PART_IMPORT_SOLIDS | bd.INSERT_PART_BREAK_LINK,
        "Default",
    )


def test_only_a_part_file_can_be_inserted(part: World, tmp_path: Path) -> None:
    with pytest.raises(InvalidRequest, match="not a part file"):
        bd.insert_part(Harness(part), str(tmp_path / "a.SLDASM"))


def test_a_missing_file_is_not_found(part: World, tmp_path: Path) -> None:
    with pytest.raises(LookupError, match="Part file not found"):
        bd.insert_part(Harness(part), str(tmp_path / "gone.sldprt"))


def test_a_part_cannot_go_into_itself(part: World, holder: str) -> None:
    part.path = holder

    with pytest.raises(InvalidRequest, match="into itself"):
        bd.insert_part(Harness(part), holder)


def test_nothing_inserted_is_an_error(part: World, holder: str) -> None:
    part.insertable.clear()

    with pytest.raises(RuntimeError, match="configuration 'Big'"):
        bd.insert_part(Harness(part), holder, "Big")


def test_a_part_without_solid_bodies_is_an_error(part: World, holder: str) -> None:
    part.insertable[holder] = {}

    with pytest.raises(RuntimeError, match="added no solid body"):
        bd.insert_part(Harness(part), holder)
