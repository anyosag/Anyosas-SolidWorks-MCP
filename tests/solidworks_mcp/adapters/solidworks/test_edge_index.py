"""Reading a part for ``select_edges`` once, and only what a box can hold.

The Dipper's master (2 bodies, about 2,275 edges, SOLIDWORKS 2026 SP4.1) took
about 260 s for one ``select_edges`` with no source, however tight its
``inside_box``: every edge was described, about a dozen COM calls each.
``fake_pocket_plate`` is a part like it: 1,944 edges on two bodies, one face
carrying 640 of them. Every COM call the fakes see is counted.
"""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import edge_index as ei
from solidworks_mcp.adapters.solidworks import edge_rules as er
from solidworks_mcp.adapters.solidworks import limits
from solidworks_mcp.adapters.solidworks.face_selection import describe_edge
from solidworks_mcp.adapters.solidworks.selection import SolidWorksSelectionMixin

from .fake_pocket_plate import (
    Log,
    Model,
    pocket_box_mm,
    pocket_corner,
    pocket_plate,
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


def _old_path(model: Model) -> tuple[list[dict[str, Any]], int]:
    """What ``select_edges`` read before: every body's edges, each described."""
    log = model._log
    before = log.total
    adapter = _Adapter(model)
    model._FlagAsMethod("GetBodies2")
    infos = []
    for body in model.GetBodies2(0, False):
        body._FlagAsMethod("GetEdges")
        infos += [describe_edge(adapter, edge) for edge in body.GetEdges()]
    # describe_edge then flagged Select4 on every edge too: one more each.
    return infos, log.total - before + len(infos)


def _edges_mm(kept: list[tuple[Any, dict[str, Any]]]) -> set[tuple[Any, ...]]:
    return {er.edge_key(info) for _edge, info in kept}


# ---- the whole part, through a tight box ---------------------------------------


def test_a_boxed_rule_reads_a_sixteenth_of_what_every_edge_costs() -> None:
    """The measured reduction: 1,803 COM reads instead of 29,166."""
    model, log = pocket_plate()
    rule = {"inside_box": pocket_box_mm(3, 4), "edge_type": "linear"}
    old_infos, old_reads = _old_path(pocket_plate()[0])
    expected, _ = er.filter_edges([(None, info) for info in old_infos], rule)

    selector = er.EdgeRuleSelector(_Adapter(model))
    kept, counts = er.match_rules([rule], selector.candidates)

    assert _edges_mm(kept) == _edges_mm(expected)
    assert len(kept) == 12  # the pocket's rims, floor edges and corners
    assert old_reads == 29_166
    assert log.total == 1_803
    assert log.total * 16 < old_reads
    # Where they went: one box per face of the plate (the block is left out
    # by its body box), and 12 edges described instead of 1,944.
    assert log.calls["GetBox"] == 806
    assert log.calls["GetBodyBox"] == 2
    assert log.calls["GetCurve"] == 12
    assert selector.index.counts["edges_described"] == 12
    # The top face and the pocket's five reach the box; 800 faces don't.
    assert counts == [
        {"candidates": 12, "faces_left_out": 800, "edge_type": 12, "inside_box": 12}
    ]


def test_the_second_call_on_an_unchanged_part_reads_only_its_stamp() -> None:
    model, log = pocket_plate()
    adapter = _Adapter(model)
    rule = {"inside_box": pocket_box_mm(3, 4)}
    er.match_rules([rule], er.EdgeRuleSelector(adapter).candidates)
    first = log.total

    again = er.EdgeRuleSelector(adapter)
    kept, _counts = er.match_rules(
        [{"inside_box": pocket_box_mm(5, 5)}, rule], again.candidates
    )

    # The stamp (flag + call), then the other pocket's five faces' edges and
    # its 12 edges described; the face boxes, the top face's edges and the
    # first pocket's edges come from the kept index.
    assert again.index.reused
    assert log.total - first == 2 + 5 * 2 + 12 * 14
    assert len(kept) == 24


def test_a_rebuild_reads_the_part_again() -> None:
    model, log = pocket_plate()
    adapter = _Adapter(model)
    rule = {"inside_box": pocket_box_mm(0, 0)}
    first = er.EdgeRuleSelector(adapter)
    er.match_rules([rule], first.candidates)
    model.stamp = 8

    second = er.EdgeRuleSelector(adapter)

    assert second.index is not first.index
    assert not second.index.reused
    assert adapter._edge_index is second.index


def test_without_a_stamp_nothing_is_kept() -> None:
    model, _log = pocket_plate()
    model.stamp = None
    adapter = _Adapter(model)

    index = ei.index_for(adapter)

    assert adapter._edge_index is None
    assert ei.index_for(adapter) is not index


def test_another_document_gets_its_own_index() -> None:
    model, _log = pocket_plate()
    adapter = _Adapter(model)
    first = ei.index_for(adapter)
    adapter.currentModel = pocket_plate()[0]

    assert ei.index_for(adapter) is not first
    ei.forget(adapter)
    assert adapter._edge_index is None


# ---- when SolidWorks hands back new objects each time ----------------------------


def test_without_the_identity_every_near_edge_is_read_and_the_answer_holds() -> None:
    """A SolidWorks that gave a new COM object per read: correct, only slower."""
    model, _log = pocket_plate()
    for face in model.bodies[0].faces:
        face.wrappers = "new"
    rule = {"inside_box": pocket_box_mm(3, 4)}

    selector = er.EdgeRuleSelector(_Adapter(model))
    kept, _counts = er.match_rules([rule], selector.candidates)

    assert selector.index.edge_identity is False
    assert len(kept) == 12
    # The top face's 644 edges and the pocket's 20 listings are described.
    assert selector.index.counts["edges_described"] == 644 + 20


def test_new_wrappers_of_one_object_are_one_edge() -> None:
    """pywin32 wraps each read anew; ``same_com_object`` sees one object."""
    model, _log = pocket_plate()
    for face in model.bodies[0].faces:
        face.wrappers = "equal"
    selector = er.EdgeRuleSelector(_Adapter(model))

    kept, _counts = er.match_rules(
        [{"inside_box": pocket_box_mm(3, 4)}], selector.candidates
    )

    assert selector.index.edge_identity is True
    assert selector.index._edges.hashed is True
    assert selector.index.counts["edges_described"] == len(kept) == 12


def test_a_hash_that_disagrees_with_the_identity_compares_one_by_one() -> None:
    model, _log = pocket_plate()
    for face in model.bodies[0].faces:
        face.wrappers = "equal_unhashed"
    selector = er.EdgeRuleSelector(_Adapter(model))

    kept, _counts = er.match_rules(
        [{"inside_box": pocket_box_mm(0, 0)}], selector.candidates
    )

    assert selector.index.edge_identity is True
    assert selector.index._edges.hashed is False
    assert len(kept) == 12
    assert selector.index.counts["edges_described"] == 12


def test_the_identity_is_checked_on_the_shortest_list_once() -> None:
    model, log = pocket_plate()
    index = ei.PartIndex(_Adapter(model), model, 1)
    plate = index.bodies()[0]
    top, wall = index.faces(plate)[0], index.faces(plate)[6]

    index.read_edges([top, wall])
    index.read_edges([index.faces(plate)[7]])

    assert index.edge_identity is True
    # top once, the wall twice (the check), the next face once.
    assert log.calls["GetEdges"] == 4


def test_faces_without_edges_leave_the_identity_unchecked() -> None:
    model, _log = pocket_plate()
    index = ei.PartIndex(_Adapter(model), model, 1)
    face = index.faces(index.bodies()[0])[0]
    face.handle.edges.clear()

    index.read_edges([face])

    assert index.edge_identity is None
    assert index.face_edges(face) == []


# ---- the pieces ------------------------------------------------------------------


def test_a_body_whose_faces_do_not_read_gives_all_its_edges() -> None:
    model, _log = pocket_plate()
    model.bodies[1].faces = []  # nor its box, which comes from them here
    box = ei.box_from_mm([[299, -1, -1], [311, 11, 11]])
    index = ei.PartIndex(_Adapter(model), model, 1)

    reach = index.edges_in_reach(box)

    assert len(reach.edges) == 12
    assert reach.bodies_left_out == 1  # the plate


def test_bodies_by_name_and_a_missing_one() -> None:
    model, _log = pocket_plate()
    index = ei.PartIndex(_Adapter(model), model, 1)

    assert index.body_named("Block") is index.bodies()[1]
    with pytest.raises(LookupError, match="bodies are Plate, Block. A feature"):
        index.body_named("Boss-Extrude1")


def test_a_face_at_a_point_measures_only_the_faces_near_it() -> None:
    model, log = pocket_plate()
    index = ei.PartIndex(_Adapter(model), model, 1)
    x0, y0 = pocket_corner(2, 2)

    face = index.face_at([x0 + 2, y0 + 2, 7.0])

    assert face.handle.box[0][2] == pytest.approx(7e-3)  # the floor
    assert log.calls["GetClosestPointOn"] == 1
    with pytest.raises(LookupError, match=r"No face found at point \[5, 5, 5\] mm"):
        index.face_at([5, 5, 5])


def test_box_helpers() -> None:
    box = ei.box_from_mm([[0, 0, 0], [10, 10, 10]])

    assert ei.box_from_corners([0.01, 0, 0, 0, 0.01, 0.01]) == box
    assert ei.box_from_corners([1, 2]) is None
    assert ei.box_from_corners(["a"] * 6) is None
    assert ei.reaches(None, box) and not ei.holds(box, None)
    assert ei.holds(box, ((0.001, 0.001, 0.001), (0.002, 0.002, 0.002)))
    assert not ei.reaches(((0.02, 0, 0), (0.03, 0.01, 0.01)), box)
    assert ei.distance_to(None, (1, 2, 3)) == 0.0
    assert ei.distance_to(box, (0.013, 0.014, 0.005)) == pytest.approx(0.005)


def test_unhashable_wrappers_are_compared_one_by_one() -> None:
    canon = ei.Canon()
    first, second = [1], [1]  # lists can't be hashed, and aren't the same object

    assert ei.identity_hash(first) is None
    assert canon.get(first) is first
    assert canon.get(second) is second
    assert canon.get(first) is first


def test_an_unreadable_stamp_is_none() -> None:
    model, _log = pocket_plate()
    model.stamp = None

    assert ei.update_stamp(_Adapter(model), model) is None
    model.GetUpdateStamp = lambda: True  # type: ignore[method-assign]
    assert ei.update_stamp(_Adapter(model), model) is None


def test_reading_face_boxes_stops_when_the_client_has_gone() -> None:
    model, _log = pocket_plate()
    selector = er.EdgeRuleSelector(_Adapter(model))
    limits.mark_client_gone("test")

    with pytest.raises(limits.OperationStopped, match="Stopped reading face boxes"):
        selector.candidates({"inside_box": pocket_box_mm(0, 0)})


def test_a_distance_that_does_not_read_is_none() -> None:
    model, _log = pocket_plate()
    index = ei.PartIndex(_Adapter(model), model, 1)

    assert index.distance(object(), (0.0, 0.0, 0.0)) is None


def test_the_log_counts_flags_apart() -> None:
    log = Log()
    log.calls.update({"flag:GetBox": 2, "GetBox": 2})

    assert (log.total, log.flags) == (4, 2)
