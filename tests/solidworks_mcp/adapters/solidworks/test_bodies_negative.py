"""``make_mating_negative`` and ``body_clearance`` on a fake part (``fake_bodies``).

The lid is a 40 x 10 x 40 box; the labels are 1 mm high boxes sunk into its
underside, the session's case: tools fully inside the target.
"""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import bodies as bd
from solidworks_mcp.adapters.solidworks import limits
from solidworks_mcp.adapters.solidworks.clearance import InvalidRequest

from .fake_bodies import Harness, World, names

_LID = [0.0, 0.0, 0.0, 40.0, 10.0, 40.0]
_LABEL1 = [5.0, 1.0, 5.0, 15.0, 2.0, 8.0]
_LABEL2 = [20.0, 1.0, 5.0, 30.0, 2.0, 8.0]


@pytest.fixture
def lid() -> World:
    return World({"Lid": _LID, "Label1": _LABEL1, "Label2": _LABEL2})


def _negative(world: World, **kwargs: Any) -> dict[str, Any]:
    args = {"target": "Lid", "tools": ["Label1", "Label2"], "clearance_mm": 0.15}
    args.update(kwargs)
    return bd.make_mating_negative(Harness(world), **args)


def _grown_volume(box: list[float], by: float) -> float:
    return bd.box_volume(bd.grow_box(box, by))


def test_shifted_copies_cut_the_grown_labels_out(lid: World) -> None:
    out = _negative(lid)

    assert out["method"] == "shifted_copies"
    assert out["copies_per_tool"] == 67
    # 66 shifted copies and the tool, per label; each joined to the union.
    assert lid.copies >= 2 * 67
    assert lid.booleans.count(bd.BODY_ADD) == 2 * 66
    removed = _grown_volume(_LABEL1, 0.15) + _grown_volume(_LABEL2, 0.15)
    assert out["volume_removed_mm3"] == pytest.approx(removed)
    assert out["volume_before_mm3"] == pytest.approx(16000.0)
    assert out["residual_overlap_mm3"] == 0.0
    assert out["features"] == ["Imported1", "Imported2", "Combine1"]
    assert out["folder"] == "Negative of Label1 and 1 more"
    assert lid.folders == [["Imported1", "Imported2", "Combine1"]]
    assert lid.body("Lid").holes == [
        bd.grow_box(_LABEL1, 0.15),
        bd.grow_box(_LABEL2, 0.15),
    ]
    # The Indent forms were tried first: a face of each tool, then the bodies.
    assert lid.indent_forms == [["Face", "Face"], ["Body", "Body"]]
    assert out["attempts"][:2] == [
        "InsertIndent with a face of each tool at mark 4 gave no feature",
        "InsertIndent with the tool bodies at mark 4 gave no feature",
    ]
    assert out["attempts"][2] == "Combine subtract preselected (main body at mark 1)"
    # The tools stay; the imported bodies were consumed by the combine.
    assert names(lid) == ["Lid", "Label1", "Label2"]
    # The math utility's CreateTransform was flagged first (pitfall #41).
    assert "CreateTransform" in lid.app.utility.flagged


def test_without_a_folder_the_features_stay_loose(lid: World) -> None:
    lid.folder_fails = True

    out = _negative(lid, method="shifted_copies")

    assert out["folder"] is None
    assert out["features"] == ["Imported1", "Imported2", "Combine1"]


def test_an_indent_that_cuts_is_kept(lid: World) -> None:
    lid.indent_result = "cut"

    out = _negative(lid, method="auto")

    assert out["method"] == "indent"
    assert out["features"] == ["Indent1"]
    assert out["folder"] is None and out["copies_per_tool"] == 0
    assert out["residual_overlap_mm3"] == 0.0
    assert lid.copies < 67  # no shifted copies were made
    assert lid.indent_forms == [["Face", "Face"]]


def test_an_indent_that_removes_nothing_is_deleted_then_copies_cut(lid: World) -> None:
    lid.indent_result = "nothing"

    out = _negative(lid)

    assert out["method"] == "shifted_copies"
    assert lid.deleted == ["Indent1", "Indent2"]
    assert "made Indent1 but it removed 0.0 mm3" in out["attempts"][0]


def test_indent_only_reports_what_it_tried(lid: World) -> None:
    with pytest.raises(RuntimeError, match="method 'shifted_copies'") as raised:
        _negative(lid, method="indent")

    assert "InsertIndent with a face of each tool at mark 4 gave no feature" in str(
        raised.value
    )
    assert lid.copies == 0 or lid.booleans.count(bd.BODY_ADD) == 0


def test_shifted_copies_only_skips_the_indent(lid: World) -> None:
    out = _negative(lid, method="shifted_copies", directions=8, axis="z")

    assert lid.indent_forms == []
    assert out["copies_per_tool"] == 19
    assert out["directions"] == 8 and out["axis"] == "z"


def test_a_tool_without_faces_skips_the_face_form(lid: World) -> None:
    lid.body("Label1").GetFaces = lambda: None  # type: ignore[method-assign]

    out = _negative(lid)

    assert out["attempts"][0] == (
        "InsertIndent with a face of each tool: a tool has no face to select"
    )
    assert lid.indent_forms == [["Body", "Body"]]


def test_tool_faces_that_cannot_be_selected_fall_through(
    lid: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from .fake_bodies import Face

    monkeypatch.setattr(Face, "Select4", lambda *_a: False)

    out = _negative(lid)

    assert out["attempts"][0] == (
        "InsertIndent with a face of each tool: SolidWorks did not select a tool "
        "face, a tool face; nothing was changed."
    )
    assert out["method"] == "shifted_copies"


def test_a_body_made_without_the_check_when_the_check_refuses(lid: World) -> None:
    lid.from_body_needs_no_check = True

    out = _negative(lid)

    assert out["features"][:2] == ["Imported1", "Imported2"]


def test_a_failed_step_leaves_nothing_behind(lid: World) -> None:
    lid.combine_fails = True

    with pytest.raises(RuntimeError, match="no Combine \\(subtract\\)"):
        _negative(lid, method="shifted_copies")

    assert lid.deleted == ["Imported2", "Imported1"]
    assert names(lid) == ["Lid", "Label1", "Label2"]


def test_no_body_from_the_grown_tool_is_an_error(lid: World) -> None:
    lid.from_body_fails = True

    with pytest.raises(RuntimeError, match="CreateFeatureFromBody3 made no body"):
        _negative(lid, method="shifted_copies")


def test_features_that_make_no_body_are_an_error(lid: World) -> None:
    lid.model.CreateFeatureFromBody3 = lambda *_a: lid.feature("Imported")  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="no solid body to subtract"):
        _negative(lid, method="shifted_copies")


def test_a_join_that_fails_is_an_error(lid: World) -> None:
    lid.add_fails = True

    with pytest.raises(RuntimeError, match="could not join copy 2 of Label1"):
        _negative(lid, method="shifted_copies")


def test_a_copy_that_cannot_be_moved_is_an_error(lid: World) -> None:
    lid.body("Label1").ApplyTransform = lambda _x: False  # type: ignore[method-assign]
    # Copies come from the label's Copy; make them refuse the transform too.
    original = lid.body("Label1").Copy

    def copy() -> Any:
        made = original()
        made.ApplyTransform = lambda _x: False
        return made

    lid.body("Label1").Copy = copy  # type: ignore[method-assign]
    lid.body("Label1").flagged.add("Copy")

    with pytest.raises(RuntimeError, match="could not be shifted"):
        _negative(lid, method="shifted_copies")


def test_a_tool_that_cannot_be_copied_is_an_error(lid: World) -> None:
    lid.body("Label1").Copy = lambda: None  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="Copy gave nothing for Label1"):
        _negative(lid, method="shifted_copies")


def test_no_math_utility_is_an_error(lid: World) -> None:
    lid.app.GetMathUtility = lambda: None  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="GetMathUtility gave nothing"):
        _negative(lid, method="shifted_copies")


def test_the_loop_stops_when_the_client_goes(lid: World) -> None:
    limits.mark_client_gone("test")

    with pytest.raises(limits.OperationStopped, match="growing Label1"):
        _negative(lid, method="shifted_copies")


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"tools": ["Lid"]}, "the target not among the tools"),
        ({"tools": ["Label1", "Label1"]}, "each tool once"),
    ],
)
def test_bad_names_are_invalid_requests(
    lid: World, kwargs: dict[str, Any], fragment: str
) -> None:
    with pytest.raises(InvalidRequest, match=fragment):
        _negative(lid, **kwargs)


def test_an_unknown_tool_is_not_found(lid: World) -> None:
    with pytest.raises(LookupError, match="Body\\(ies\\) not found: Label9"):
        _negative(lid, tools=["Label9"])


def test_a_renamed_result_body_is_found(lid: World) -> None:
    real_combine = lid.combine

    def renaming(op: int, target: Any, others: list[Any]) -> Any:
        feature = real_combine(op, target, others)
        target.Name = "Combine1"
        return feature

    lid.combine = renaming  # type: ignore[method-assign]

    out = _negative(lid, method="shifted_copies")

    assert out["result_body"] == "Combine1"
    assert out["volume_after_mm3"] < out["volume_before_mm3"]


# ---- body_clearance -------------------------------------------------------------


def test_two_bodies_apart() -> None:
    world = World({"A": [0, 0, 0, 10, 10, 10], "B": [12.5, 0, 0, 20, 10, 10]})

    out = bd.body_clearance(Harness(world), "A", "B")

    assert out["clearance_mm"] == pytest.approx(2.5)
    assert out["touching"] is False and out["overlap_mm3"] == 0.0
    assert out["point_a_mm"][0] == pytest.approx(10.0)
    assert out["point_b_mm"][0] == pytest.approx(12.5)
    assert out["face_a"]["kind"] == "plane"
    assert out["pairs_total"] == 36
    assert 1 <= out["pairs_checked"] < 36
    # No overlap check: the boxes are apart.
    assert world.booleans == []


def test_overlapping_bodies_touch_with_their_shared_volume(lid: World) -> None:
    out = bd.body_clearance(Harness(lid), "Label1", "Lid")

    assert out["touching"] is True and out["clearance_mm"] == 0.0
    assert out["overlap_mm3"] == pytest.approx(30.0)
    assert out["pairs_checked"] == 0
    assert lid.booleans == [bd.BODY_INTERSECT]


def test_bodies_whose_boxes_touch_are_measured_by_their_faces() -> None:
    world = World({"A": [0, 0, 0, 10, 10, 10], "B": [10, 0, 0, 20, 10, 10]})

    out = bd.body_clearance(Harness(world), "A", "B")

    assert out["touching"] is True and out["clearance_mm"] == 0.0
    assert out["overlap_mm3"] == 0.0
    assert world.booleans == [bd.BODY_INTERSECT]


def test_the_same_geometry_twice_overlaps_entirely() -> None:
    world = World({"A": [0, 0, 0, 10, 10, 10], "B": [0, 0, 0, 10, 10, 10]})

    out = bd.body_clearance(Harness(world), "A", "B")

    assert out["overlap_mm3"] == pytest.approx(1000.0)


def test_the_same_body_twice_is_an_invalid_request(lid: World) -> None:
    with pytest.raises(InvalidRequest, match="both 'Lid'"):
        bd.body_clearance(Harness(lid), "Lid", "Lid")


def test_a_body_without_faces_is_an_error() -> None:
    world = World({"A": [0, 0, 0, 1, 1, 1], "B": [5, 0, 0, 6, 1, 1]})
    world.body("B").GetFaces = lambda: ()  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="No face of body 'B'"):
        bd.body_clearance(Harness(world), "A", "B")


def test_nothing_measured_is_an_error() -> None:
    world = World({"A": [0, 0, 0, 1, 1, 1], "B": [5, 0, 0, 6, 1, 1]})
    world.model.ClosestDistance = lambda *_a: -1  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="measured none of the 36 face pairs"):
        bd.body_clearance(Harness(world), "A", "B")


def test_an_intersection_that_raises_is_an_error(lid: World) -> None:
    def broken(*_a: Any) -> Any:
        raise OSError("boolean failed")

    lid.boolean = broken  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="Operations2 \\(common\\) failed"):
        bd.body_clearance(Harness(lid), "Label1", "Lid")


def test_an_uncopyable_body_is_an_error(lid: World) -> None:
    lid.body("Lid").Copy = lambda: None  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="the overlap can't be read"):
        bd.body_clearance(Harness(lid), "Label1", "Lid")
