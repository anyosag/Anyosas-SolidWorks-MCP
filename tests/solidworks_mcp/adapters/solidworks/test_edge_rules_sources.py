"""``select_edges``' new sources and filters (``edge_rules``), on the pocket plate.

From the Dipper build: edges shared by two faces given by points, a feature's
edges on one body (bodies are renamed after features, pitfall #51), convex or
concave edges, and ids that last across rebuilds. ``fake_pocket_plate`` is a
200 x 160 mm plate with 160 pockets (``Cut-Extrude<n>``) and a far block.
"""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import edge_rules as er
from solidworks_mcp.adapters.solidworks.selection import SolidWorksSelectionMixin

from .fake_pocket_plate import (
    pocket_box_mm,
    pocket_corner,
    pocket_faces,
    pocket_plate,
    point_on,
)


class _Adapter(SolidWorksSelectionMixin):
    def __init__(self, model: Any) -> None:
        self.currentModel = model
        self.swApp = None

    def _attempt(self, operation: Any, default: Any = None) -> Any:
        try:
            return operation()
        except Exception:  # noqa: BLE001
            return default

    def _handle_com_operation(self, name: str, operation: Any, *args: Any) -> Any:
        try:
            return AdapterResult(
                status=AdapterResultStatus.SUCCESS, data=operation(*args)
            )
        except Exception as exc:  # noqa: BLE001 - mirror the real adapter
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=f"Error in {name}: {exc}"
            )


def _ends(kept: list[tuple[Any, dict[str, Any]]]) -> set[tuple[Any, ...]]:
    return {er.edge_key(info) for _edge, info in kept}


def _match(model: Any, rule: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    selector = er.EdgeRuleSelector(_Adapter(model))
    return er.match_rules(
        er.expand_query(rule), selector.candidates, selector.convexity
    )


# ---- the rules (pure) --------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "fragment"),
    [
        (
            {"feature": "Cut1", "between_faces": [[0, 0, 0], [1, 1, 1]]},
            "Give at most one of feature, face_point, side, plane, between_faces or "
            "edge_ids (got feature, between_faces)",
        ),
        ({"between_faces": [[0, 0, 0]]}, "between_faces must be two points"),
        ({"between_faces": "top"}, "between_faces must be two points"),
        ({"between_faces": [[0, 0, 0], [1, 1]]}, "between_faces[1] must be [x, y, z]"),
        ({"face_point": "here"}, "face_point must be [x, y, z]"),
        ({"edge_ids": []}, "edge_ids must be a list of 1 to 500"),
        ({"edge_ids": ["!"]}, "edge_ids[0]: '!' is not an edge id"),
        ({"edge_ids": ["AP8="], "body": "Plate"}, "leave body out"),
        ({"body": " "}, "body must be a solid body's name"),
        ({"body": 5}, "body must be a solid body's name"),
        ({"convexity": "bumpy"}, "convexity must be one of convex, concave, tangent"),
    ],
)
def test_bad_rules_are_explained(query: dict[str, Any], fragment: str) -> None:
    assert fragment in (er.validate_edge_query(query) or "")


def test_new_keys_are_rule_keys_and_with_ids_is_not() -> None:
    query = {"with_ids": True, "any_of": [{"convexity": "convex", "body": "Plate"}]}

    assert er.validate_edge_query(query) is None
    assert er.expand_query(query) == [{"convexity": "convex", "body": "Plate"}]
    assert "with_ids" not in er.RULE_KEYS


@pytest.mark.parametrize(
    ("query", "source"),
    [
        (
            {"between_faces": [[1, 2, 3], [4, 5, 6]]},
            "the join of the faces at [1, 2, 3] mm and [4, 5, 6] mm",
        ),
        ({"edge_ids": ["a", "b"]}, "2 edge id(s)"),
        ({"body": "Plate"}, "body 'Plate'"),
        (
            {"feature": "Cut-Extrude2", "body": "Plate"},
            "feature 'Cut-Extrude2' on body 'Plate'",
        ),
        ({"side": "top", "body": "Plate"}, "the top face on body 'Plate'"),
    ],
)
def test_new_sources_in_words(query: dict[str, Any], source: str) -> None:
    assert er.describe_source(query) == source


def test_convexity_is_read_last_and_what_does_not_read_is_counted() -> None:
    edges = [
        ("a", {"curve": "linear", "length_mm": 1.0}),
        ("b", {"curve": "linear", "length_mm": 5.0}),
        ("c", {"curve": "linear", "length_mm": 6.0}),
        ("d", {"curve": "circular"}),
    ]
    asked: list[str] = []

    def classify(edge: str, _info: dict[str, Any]) -> str | None:
        asked.append(edge)
        return {"b": "concave", "c": None}.get(edge, "convex")

    kept, counts = er.filter_edges(
        edges,
        {"edge_type": "linear", "min_length": 2, "convexity": "concave"},
        classify,
    )

    assert [edge for edge, _ in kept] == ["b"]
    assert asked == ["b", "c"]  # only what the other rules kept
    assert counts == {
        "candidates": 4,
        "edge_type": 3,
        "length": 2,
        "convexity": 1,
        "convexity_unread": 1,
    }
    assert er.no_match_message({}, {**counts, "convexity": 0}).endswith(
        "2 pass length; 0 pass convexity; 1 whose convexity didn't read."
    )


def test_without_a_reader_the_description_says_the_convexity() -> None:
    kept, _counts = er.filter_edges(
        [("a", {"convexity": "convex"}), ("b", {})], {"convexity": "convex"}
    )

    assert [edge for edge, _ in kept] == ["a"]


def test_a_box_that_left_faces_out_says_so() -> None:
    message = er.no_match_message(
        {"inside_box": [[0, 0, 0], [1, 1, 1]]},
        {"candidates": 3, "faces_left_out": 800, "inside_box": 0},
    )

    assert message == (
        "No matching edges found: the part has 3 edge(s) near inside_box (800 "
        "face(s) left out by their boxes); 0 pass inside_box."
    )


def test_rules_with_other_boxes_or_bodies_read_apart() -> None:
    reads: list[dict[str, Any]] = []

    def candidates(rule: dict[str, Any]) -> list[Any]:
        reads.append(rule)
        return []

    er.match_rules(
        [
            {"inside_box": [[0, 0, 0], [1, 1, 1]]},
            {"inside_box": [[0, 0, 0], [2, 2, 2]]},
            {"inside_box": [[0, 0, 0], [1, 1, 1]], "edge_type": "linear"},
            {"body": "Plate"},
        ],
        candidates,
    )

    assert len(reads) == 3


# ---- the sources, on the pocket plate --------------------------------------------


def test_edges_between_a_wall_and_its_floor() -> None:
    model, _log = pocket_plate()
    walls_and_floor = pocket_faces(model, 3, 4)

    kept, counts = _match(
        model,
        {"between_faces": [point_on(walls_and_floor[0]), point_on(walls_and_floor[4])]},
    )

    x0, y0 = pocket_corner(3, 4)
    (start, end) = kept[0][1]["start_mm"], kept[0][1]["end_mm"]
    assert len(kept) == 1
    assert sorted([start[0], end[0]]) == pytest.approx([x0, x0 + 4])
    assert start[1] == end[1] == pytest.approx(y0)
    assert start[2] == pytest.approx(7.0)
    assert counts == [{"candidates": 1}]


def test_edges_between_faces_without_the_identity_go_by_geometry() -> None:
    model, _log = pocket_plate()
    for face in model.bodies[0].faces:
        face.wrappers = "new"
    wall, floor = pocket_faces(model, 0, 0)[1], pocket_faces(model, 0, 0)[4]

    kept, _counts = _match(model, {"between_faces": [point_on(wall), point_on(floor)]})

    assert len(kept) == 1


def test_faces_that_share_nothing_give_no_edges() -> None:
    model, _log = pocket_plate()
    a, b = pocket_faces(model, 0, 0)[4], pocket_faces(model, 1, 1)[4]

    kept, counts = _match(model, {"between_faces": [point_on(a), point_on(b)]})

    assert kept == []
    assert counts == [{"candidates": 0}]


def test_two_points_on_one_face_are_refused() -> None:
    model, _log = pocket_plate()
    floor = pocket_faces(model, 0, 0)[4]
    nearby = [*point_on(floor)[:2], 7.0]
    nearby[0] += 0.5

    with pytest.raises(LookupError, match="Both points of between_faces are on one"):
        _match(model, {"between_faces": [point_on(floor), nearby]})


def test_a_cuts_edges_on_the_body_it_cut() -> None:
    model, _log = pocket_plate()

    kept, _counts = _match(model, {"feature": "Cut-Extrude1", "body": "Plate"})

    assert len(kept) == 12


def test_a_feature_with_no_faces_on_the_body_names_where_they_are() -> None:
    model, _log = pocket_plate()

    with pytest.raises(LookupError) as caught:
        _match(model, {"feature": "Cut-Extrude1", "body": "Block"})

    assert str(caught.value).startswith(
        "Feature 'Cut-Extrude1' made no faces on body 'Block'; its faces are on Plate."
    )


def test_a_feature_whose_bodies_do_not_read() -> None:
    model, _log = pocket_plate()
    for face in pocket_faces(model, 0, 0):
        face.body = None

    with pytest.raises(LookupError, match="its faces are on no body that reads"):
        _match(model, {"feature": "Cut-Extrude1", "body": "Plate"})


def test_one_bodys_edges_and_a_face_on_it() -> None:
    model, _log = pocket_plate()
    block_top = model.bodies[1].faces[0]

    whole, _ = _match(model, {"body": "Block"})
    on_top, _ = _match(model, {"face_point": point_on(block_top), "body": "Block"})
    side, _ = _match(model, {"side": "+x", "body": "Block"})

    assert len(whole) == 12
    assert len(on_top) == 4
    assert len(side) == 4
    assert all(abs(info["start_mm"][0] - 310.0) < 1e-9 for _e, info in side)


@pytest.mark.parametrize("rule", [{}, {"inside_box": [[0, 0, 0], [1, 1, 1]]}])
def test_a_part_without_bodies_has_no_edges(rule: dict[str, Any]) -> None:
    model, _log = pocket_plate()
    model.bodies = []

    with pytest.raises(LookupError, match="The active part has no solid body edges"):
        _match(model, rule)


def test_a_point_on_another_body_is_no_face_of_this_one() -> None:
    model, _log = pocket_plate()

    with pytest.raises(LookupError, match="No face found at point"):
        _match(model, {"face_point": [305, 5, 10], "body": "Plate"})


def test_a_large_source_with_a_box_is_narrowed_through_the_body() -> None:
    """Boss-Extrude1's top face carries all 640 rims: only 4 are read."""
    model, _log = pocket_plate()

    kept, counts = _match(
        model, {"feature": "Boss-Extrude1", "inside_box": pocket_box_mm(3, 4)}
    )
    selector_counts = counts[0]

    assert len(kept) == 4  # the pocket's rims
    assert selector_counts["candidates"] == 4
    assert selector_counts["faces_left_out"] == 5  # the plate's other faces


def test_a_feature_face_out_of_the_box_is_left_out() -> None:
    model, _log = pocket_plate()

    kept, counts = _match(
        model, {"feature": "Cut-Extrude1", "inside_box": [[0, 0, 6], [20, 20, 7.5]]}
    )

    # The floor and the walls reach z 6..7.5; the rims at z 10 don't pass.
    assert counts[0]["faces_left_out"] == 0
    assert len(kept) == 4


def test_rules_on_one_feature_with_other_boxes_read_its_faces_once() -> None:
    model, log = pocket_plate()

    kept, _counts = _match(
        model,
        {
            "feature": "Cut-Extrude1",
            "any_of": [
                {"inside_box": pocket_box_mm(0, 0)},
                {"inside_box": [[0, 0, 6], [20, 20, 7.5]]},
            ],
        },
    )

    assert len(kept) == 12
    assert log.calls["FeatureByName"] == 1
    assert log.calls["GetBox"] == 5  # the cut's five faces, once


def test_convex_and_concave_edges_of_a_pocket() -> None:
    model, _log = pocket_plate()
    box = pocket_box_mm(2, 2)

    convex, _ = _match(model, {"inside_box": box, "convexity": "convex"})
    concave, _ = _match(model, {"inside_box": box, "convexity": "concave"})

    assert len(convex) == 4  # the rims
    assert all(info["start_mm"][2] == pytest.approx(10.0) for _e, info in convex)
    assert len(concave) == 8  # the floor edges and the inside corners
    assert all(info["convexity"] == "concave" for _e, info in concave)


def test_the_plates_outer_edges_and_rims_are_convex() -> None:
    model, _log = pocket_plate()

    kept, counts = _match(model, {"feature": "Boss-Extrude1", "convexity": "convex"})

    assert len(kept) == 12 + 640
    assert counts == [{"candidates": 652, "convexity": 652}]


# ---- ids -------------------------------------------------------------------------


def test_edges_by_id_after_a_rebuild() -> None:
    model, _log = pocket_plate()
    adapter = _Adapter(model)
    first = er.EdgeRuleSelector(adapter)
    kept, _ = er.match_rules([{"inside_box": pocket_box_mm(1, 1)}], first.candidates)
    from solidworks_mcp.adapters.solidworks.persist_ids import persist_ids

    ids = persist_ids(adapter, model, [edge for edge, _ in kept])
    model.stamp = 9  # a rebuild: the kept index goes

    again, counts = _match(model, {"edge_ids": ids, "edge_type": "linear"})

    assert _ends(again) == _ends(kept)
    assert counts == [{"candidates": 12, "edge_type": 12}]


def test_an_id_whose_edge_has_gone_is_named() -> None:
    model, _log = pocket_plate()
    from solidworks_mcp.adapters.solidworks.persist_ids import persist_ids

    edges = model.bodies[0].edges[:3]
    ids = persist_ids(_Adapter(model), model, edges)
    model.Extension.gone.add(1)

    with pytest.raises(LookupError, match="edge_ids 1 no longer name an edge"):
        _match(model, {"edge_ids": ids})


def test_edge_identity_unchecked_ids_are_their_own_handles() -> None:
    model, _log = pocket_plate()
    from solidworks_mcp.adapters.solidworks.persist_ids import persist_ids

    ids = persist_ids(_Adapter(model), model, model.bodies[1].edges[:2])
    selector = er.EdgeRuleSelector(_Adapter(model))

    kept = selector.candidates({"edge_ids": ids})

    assert selector.index.edge_identity is None
    assert [edge for edge, _ in kept] == model.bodies[1].edges[:2]
