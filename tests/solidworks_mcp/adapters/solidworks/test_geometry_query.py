"""What lies near a point (``geometry_query``), and what it costs to find out.

The Dipper's cradle (about 1,300 edges) took 110-150 s per ``select_edges``
used as a geometry query. ``fake_query_plate`` is a part like it, 1,944
edges and 812 faces on two bodies, that can pick; every COM call it sees is
counted. Pocket (3, 4) spans x 46..50, y 58..62, z 7..10: from its middle at
z 8.5, its four walls are 2 mm away, its floor 1.5 mm, its floor edges and
rims 2.5 mm and its corners 2.83 mm.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import edge_index as ei
from solidworks_mcp.adapters.solidworks import geometry_query as gq
from solidworks_mcp.adapters.solidworks import limits
from solidworks_mcp.adapters.solidworks.face_selection import describe_edge
from solidworks_mcp.adapters.solidworks.selection import SolidWorksSelectionMixin

from .fake_pocket_plate import Body, Edge, Log, Wrapper
from .fake_query_plate import QueryModel, query_plate

POCKET = [48.0, 60.0, 8.5]
OTHER_POCKET = [72.0, 84.0, 8.5]  # pocket (5, 6)


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


def _old_reads(model: QueryModel) -> int:
    """What select_edges with no source read before dipper-edges: every edge."""
    before = model._log.total
    adapter = _Adapter(model)
    model._FlagAsMethod("GetBodies2")
    for body in model.GetBodies2(0, False):
        body._FlagAsMethod("GetEdges")
        for edge in body.GetEdges():
            describe_edge(adapter, edge)
    return model._log.total - before + len(model.edges())  # and a Select4 flag


def _kinds(entries: list[dict[str, Any]]) -> list[tuple[str, float]]:
    return [(e["kind"], e["distance_mm"]) for e in entries]


# ---- the read counts -------------------------------------------------------------


def test_the_first_query_starts_from_the_picks_and_reads_299_calls() -> None:
    model, log = query_plate()

    reply = gq.query_geometry(_Adapter(model), POCKET, 2.6)

    assert _old_reads(query_plate()[0]) == 29_166
    assert log.total == 299
    assert reply["method"] == "pick" and reply["complete"] is False
    assert (reply["faces_found"], reply["edges_found"]) == (6, 8)
    # The floor (and the fake top face, a rectangle without its pockets),
    # then the four walls.
    assert _kinds(reply["faces"]) == [("plane", 1.5)] * 2 + [("plane", 2.0)] * 4
    assert _kinds(reply["edges"]) == [("line", 2.5)] * 8
    assert all(e["length_mm"] == 4.0 for e in reply["edges"])
    # 7 face picks and 7 edge picks; no face box read; the selection empty.
    assert log.calls["SelectByRay"] == 12 and log.calls["SelectByID2"] == 2
    assert log.calls["GetBox"] == 6  # only the six faces listed: GetBox describes them
    assert reply["read"] == {
        "kept_from_earlier_call": False,
        "bodies_near": 1,
        "picks": 14,
        "face_boxes": 0,
        "measured": 18,
        "described": 14,
    }
    assert model.SelectionManager.items == []
    assert log.calls["ClearSelection2"] == 1


def test_read_all_reads_the_boxes_once_and_then_a_query_costs_almost_nothing() -> None:
    model, log = query_plate()
    adapter = _Adapter(model)

    full = gq.query_geometry(adapter, POCKET, 2.6, read_all=True)
    first = log.total
    other = gq.query_geometry(adapter, OTHER_POCKET, 2.6)
    second = log.total - first
    again = gq.query_geometry(adapter, OTHER_POCKET, 2.6)
    third = log.total - first - second

    assert full["method"] == "index" and full["complete"] is True
    assert (full["faces_found"], full["edges_found"]) == (6, 8)
    assert full["read"]["face_boxes"] == 806
    # 806 boxes (1,612 calls), 18 measured, 14 described with ids and bodies.
    assert first == 1_844
    # The other pocket without read_all: the kept boxes make it complete.
    assert other["method"] == "index" and other["complete"] is True
    assert other["read"]["kept_from_earlier_call"] is True
    assert other["read"]["face_boxes"] == 0
    assert second == 211  # 13 new descriptions with their ids
    # The same point again: the stamp, and one call per entity measured.
    assert third == 2 + 18
    assert again["read"]["described"] == 0
    assert log.calls["SelectByRay"] == 0


def test_after_select_edges_the_part_reads_as_kept() -> None:
    from .fake_pocket_plate import pocket_box_mm

    model, log = query_plate()
    adapter = _Adapter(model)
    adapter._edge_index = None
    from solidworks_mcp.adapters.solidworks import edge_rules as er

    er.match_rules(
        [{"inside_box": pocket_box_mm(3, 4)}], er.EdgeRuleSelector(adapter).candidates
    )
    before = log.total

    reply = gq.query_geometry(adapter, POCKET, 2.6)

    assert reply["method"] == "index" and reply["complete"] is True
    assert reply["read"]["face_boxes"] == 0
    assert log.total - before < 300


def test_a_selection_is_never_picked_over() -> None:
    model, log = query_plate()
    held = object()
    model.SelectionManager.items = [held]

    reply = gq.query_geometry(_Adapter(model), POCKET, 2.6)

    assert reply["method"] == "index" and reply["complete"] is True
    assert log.calls["SelectByRay"] == 0 and log.calls["ClearSelection2"] == 0
    assert model.SelectionManager.items == [held]


def test_nothing_near_reads_only_the_bodies() -> None:
    model, log = query_plate()

    reply = gq.query_geometry(_Adapter(model), [500.0, 500.0, 500.0], 5)

    assert reply["faces"] == [] and reply["edges"] == []
    assert (reply["faces_found"], reply["edges_found"]) == (0, 0)
    assert reply["complete"] is True and reply["read"]["bodies_near"] == 0
    # The stamp, the body list, two body boxes (flag and call each).
    assert log.total == 2 + 2 + 4


def test_picks_that_reach_nothing_fall_back_to_the_index() -> None:
    model, _log = query_plate()
    model.Extension.SelectByRay = lambda *_a: False  # type: ignore[method-assign]
    model.Extension.SelectByID2 = lambda *_a: False  # type: ignore[method-assign]

    reply = gq.query_geometry(_Adapter(model), POCKET, 2.6)

    assert reply["method"] == "index" and reply["complete"] is True
    assert reply["read"]["picks"] == 14 and reply["read"]["face_boxes"] == 806
    assert reply["edges_found"] == 8


def test_faces_only_picks_faces_only() -> None:
    model, log = query_plate()

    reply = gq.query_geometry(_Adapter(model), POCKET, 2.6, kinds="faces", limit=2)

    assert "edges" not in reply
    assert log.calls["SelectByRay"] == 6 and log.calls["SelectByID2"] == 1
    assert len(reply["faces"]) == 2 and reply["faces_found"] == 6
    assert log.calls["GetEdges"] == 0


def test_a_wider_ball_holds_whole_walls_and_the_limit_cuts_the_list() -> None:
    """At 4 mm each 4 x 3 mm wall lies wholly inside: its edges all count."""
    model, _log = query_plate()

    reply = gq.query_geometry(_Adapter(model), POCKET, 4.0, limit=2, read_all=True)

    # Walls, floor and top; the pocket's 12 edges and nothing else.
    assert reply["faces_found"] == 6 and len(reply["faces"]) == 2
    assert reply["edges_found"] == 12 and len(reply["edges"]) == 2
    assert reply["read"]["described"] == 4


def test_picks_beyond_the_radius_are_not_listed() -> None:
    """At 1 mm the rays still reach the walls (2 mm) and floor (1.5 mm): too far."""
    model, _log = query_plate()

    reply = gq.query_geometry(_Adapter(model), POCKET, 1.0)

    assert reply["read"]["picks"] == 14
    assert reply["method"] == "index" and reply["faces_found"] == 0


def test_edges_only_lists_edges_only() -> None:
    model, _log = query_plate()

    reply = gq.query_geometry(
        _Adapter(model), POCKET, 2.6, kinds="edges", read_all=True
    )

    assert "faces" not in reply and reply["edges_found"] == 8
    assert all(e["body"] == "Plate" and e["id"] for e in reply["edges"])


def test_without_one_object_per_edge_the_same_edge_is_listed_once() -> None:
    model, _log = query_plate()
    for face in model.bodies[0].faces:
        face.wrappers = "new"

    reply = gq.query_geometry(_Adapter(model), POCKET, 2.6, read_all=True)

    assert reply["edges_found"] == 8
    keys = {(tuple(e["start_mm"]), tuple(e["end_mm"])) for e in reply["edges"]}
    assert len(keys) == 8


def test_a_face_picked_through_two_wrappers_is_listed_once() -> None:
    model, log = query_plate()
    adapter = _Adapter(model)
    index = ei.index_for(adapter)
    floor = model.bodies[0].faces[6 + 5 * (3 * 10 + 4) + 4]
    twins = [Wrapper(log, floor, True, object()) for _ in range(2)]
    point = tuple(c / 1000 for c in POCKET)
    hits = [gq.measure(adapter, index, twin, point) for twin in twins]

    faces, count = gq._faces_reply(
        adapter, index, [h for h in hits if h], point, 10, unique=False
    )

    assert count == 1 and faces[0]["distance_mm"] == 1.5


def test_a_stop_mid_picks_still_clears_the_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, _log = query_plate()
    seen: list[str] = []

    def check(doing: str) -> None:
        seen.append(doing)
        if seen.count("picking around the point") == 3:
            raise limits.OperationStopped("Stopped picking")

    monkeypatch.setattr(gq, "limits", SimpleNamespace(check=check))

    with pytest.raises(limits.OperationStopped):
        gq.query_geometry(_Adapter(model), POCKET, 2.6)
    assert model.SelectionManager.items == []


def test_a_selection_that_does_not_read_is_left_alone() -> None:
    model, _log = query_plate()
    model.SelectionManager = None  # type: ignore[assignment]

    reply = gq.query_geometry(_Adapter(model), POCKET, 2.6)

    assert reply["method"] == "index"


def test_no_extension_means_no_picks() -> None:
    model, _log = query_plate()
    model.Extension = None  # type: ignore[assignment]

    assert (
        gq.pick_search(_Adapter(model), _index(model), (0.0, 0.0, 0.0), 1e-3, "both")
        is None
    )


def test_a_body_without_faces_has_its_edges_measured() -> None:
    model, _log = query_plate()
    block = model.bodies[1]
    block_box = block.GetBodyBox()
    block.faces = []
    block.GetBodyBox = lambda: block_box  # type: ignore[method-assign]

    reply = gq.query_geometry(
        _Adapter(model), [305.0, 5.0, 10.0], 0.5, kinds="edges", read_all=True
    )

    # The two top edges through x 305 are 5 mm away; none within 0.5 mm but
    # the middle of nothing: the top face is gone, the edges stay.
    assert reply["edges_found"] == 0
    near = gq.query_geometry(
        _Adapter(model), [305.0, 0.0, 10.0], 0.5, kinds="edges", read_all=True
    )
    assert near["edges_found"] == 1 and near["edges"][0]["body"] == "Block"


def _index(model: QueryModel) -> ei.PartIndex:
    return ei.PartIndex(_Adapter(model), model, 1)


# ---- describing ------------------------------------------------------------------


class _Surface:
    """A surface of one kind, with its parameters."""

    def __init__(self, kind: str, **params: Any) -> None:
        self._kind = kind
        for name, value in params.items():
            setattr(self, name, value)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("Is"):
            return lambda: name[2:].lower() == self._kind
        raise AttributeError(name)


def _surface(kind: str, **params: Any) -> dict[str, Any]:
    face = SimpleNamespace(Normal=params.pop("Normal", (0.0, 0.0, 0.0)))
    return gq.surface_of(_Adapter(None), face, _Surface(kind, **params))


def test_each_surface_kind_and_its_parameters() -> None:
    plane = _surface("plane", Normal=(0.0, 0.0, 2.0))
    flat = _surface("plane", PlaneParams=(0.0, -1.0, 0.0, 0.0, 0.0, 0.0))
    cylinder = _surface("cylinder", CylinderParams=(0.01, 0, 0, 0, 0, 2, 0.0025))
    cone = _surface("cone", ConeParams=(0, 0, 0, 0, 1, 0, 0.004, math.radians(30)))
    sphere = _surface("sphere", SphereParams=(0, 0.01, 0, -0.003))
    torus = _surface("torus", TorusParams=(0, 0, 0, 1, 0, 0, 0.01, 0.002))

    assert plane == {"kind": "plane", "normal": [0.0, 0.0, 1.0]}
    assert flat["normal"] == [0.0, -1.0, 0.0]
    assert cylinder == {
        "kind": "cylinder",
        "axis": [0.0, 0.0, 1.0],
        "origin": [0.01, 0.0, 0.0],
        "radius": 0.0025,
    }
    assert cone["half_angle"] == pytest.approx(math.radians(30))
    assert sphere == {"kind": "sphere", "center": [0.0, 0.01, 0.0], "radius": 0.003}
    assert torus["minor_radius"] == 0.002 and torus["center"] == [0.0, 0.0, 0.0]
    assert _surface("parametric") == {"kind": "spline"}
    assert _surface("swept") == {"kind": "other"}
    assert _surface("cylinder", CylinderParams=(1, 2)) == {"kind": "cylinder"}
    assert _surface("cylinder", CylinderParams=("a",) * 7) == {"kind": "cylinder"}
    assert _surface("plane")["normal"] is None


def test_a_hole_reads_its_centre_on_the_axis_at_the_point() -> None:
    facts = {
        "kind": "cylinder",
        "axis": [0.0, 0.0, 1.0],
        "origin": [0.01, 0.02, 0.0],
        "radius": 0.0025,
        "area_mm2": 78.5,
    }
    hit = gq.Hit(None, 0.0025, (0.0125, 0.02, 0.004))

    entry = gq.face_entry(facts, hit, (0.01, 0.02, 0.004))

    assert entry["center_mm"] == [10.0, 20.0, 4.0]
    assert entry["radius_mm"] == 2.5 and entry["axis"] == [0.0, 0.0, 1.0]
    cone = gq.face_entry(
        {**facts, "kind": "cone", "half_angle": math.radians(45)},
        hit,
        (0.01, 0.02, 0.004),
    )
    assert cone["origin_mm"] == [10.0, 20.0, 0.0] and cone["half_angle_deg"] == 45.0
    torus = gq.face_entry(
        {"kind": "torus", "center": [0, 0, 0], "minor_radius": 0.001}, hit, (0, 0, 0)
    )
    assert torus["minor_radius_mm"] == 1.0 and torus["center_mm"] == [0.0, 0.0, 0.0]


class _Curve:
    def __init__(self, kind: str, length: Any = 0.00785) -> None:
        self._kind, self._length = kind, length
        self.CircleParams = (0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.005)

    def IsCircle(self) -> bool:  # noqa: N802
        return self._kind in ("circle", "arc")

    def IsLine(self) -> bool:  # noqa: N802
        return False

    def IsEllipse(self) -> bool:  # noqa: N802
        return self._kind == "ellipse"

    def IsBcurve(self) -> bool:  # noqa: N802
        return self._kind == "spline"

    def GetLength3(self, start: float, end: float) -> Any:  # noqa: N802
        assert (start, end) == (0.0, math.pi / 2)
        return self._length


class _Vertex:
    def __init__(self, point: tuple[float, ...]) -> None:
        self._point = point

    def GetPoint(self) -> tuple[float, ...]:  # noqa: N802
        return self._point


class _CurvedEdge:
    def __init__(self, kind: str, ends: bool = True, length: Any = 0.00785) -> None:
        self._curve = _Curve(kind, length)
        self._ends = ends

    def GetCurve(self) -> _Curve:  # noqa: N802
        return self._curve

    def GetStartVertex(self) -> Any:  # noqa: N802
        return _Vertex((0.005, 0.0, 0.0)) if self._ends else None

    def GetEndVertex(self) -> Any:  # noqa: N802
        return _Vertex((0.0, 0.005, 0.0)) if self._ends else None

    def GetCurveParams2(self) -> tuple[float, ...]:  # noqa: N802
        return (0.005, 0, 0, 0, 0.005, 0, 0.0, math.pi / 2, 0, 0, 1)


def _edge(kind: str, ends: bool = True, length: Any = 0.00785) -> dict[str, Any]:
    adapter = _Adapter(None)
    return gq.edge_facts(
        adapter, ei.PartIndex(adapter, None, 1), _CurvedEdge(kind, ends, length)
    )


def test_each_edge_kind_and_its_length() -> None:
    arc = _edge("arc")
    circle = _edge("circle", ends=False)

    assert arc["kind"] == "arc" and arc["length_mm"] == 7.85
    assert arc["center_mm"] == [0.0, 0.0, 0.0] and arc["radius_mm"] == 5.0
    assert circle["kind"] == "circle"
    assert circle["length_mm"] == pytest.approx(2 * math.pi * 5.0)
    assert _edge("ellipse")["kind"] == "ellipse"
    assert _edge("spline")["kind"] == "spline"
    assert _edge("other")["kind"] == "other"
    assert "length_mm" not in _edge("spline", length=None)
    assert "length_mm" not in _edge("spline", length=-1.0)


def test_a_curve_that_does_not_read_is_other() -> None:
    adapter = _Adapter(None)
    edge = SimpleNamespace(
        GetCurve=lambda: None, GetStartVertex=lambda: None, GetEndVertex=lambda: None
    )

    facts = gq.edge_facts(adapter, ei.PartIndex(adapter, None, 1), edge)

    assert facts["kind"] == "other" and "length_mm" not in facts


def test_a_face_without_a_surface_or_area_still_has_its_box() -> None:
    adapter = _Adapter(None)
    face = SimpleNamespace(
        GetSurface=lambda: None,
        GetArea=lambda: None,
        GetBox=lambda: (0.0, 0.0, 0.0, 0.001, 0.002, 0.0),
    )

    facts = gq.face_facts(adapter, ei.PartIndex(adapter, None, 1), face, None)

    assert facts == {"kind": "other", "box_mm": [0.0, 0.0, 0.0, 1.0, 2.0, 0.0]}


def test_a_body_found_through_get_body_but_not_listed_reads_its_name() -> None:
    model, log = query_plate()
    adapter = _Adapter(model)
    index = ei.index_for(adapter)
    stray = Body(log, "Stray", [], [])
    edge = Edge(log, (0, 0, 0), (0.001, 0, 0))
    edge.GetBody = lambda: stray  # type: ignore[attr-defined]

    assert gq._body_name(adapter, index, gq.Hit(edge, 0.0, (0, 0, 0))) == "Stray"
    edge.GetBody = lambda: None  # type: ignore[attr-defined]
    assert gq._body_name(adapter, index, gq.Hit(edge, 0.0, (0, 0, 0))) is None


def test_an_entity_that_does_not_measure_is_left_out() -> None:
    model, _log = query_plate()
    adapter = _Adapter(model)

    assert gq.measure(adapter, ei.index_for(adapter), object(), (0.0, 0.0, 0.0)) is None


# ---- pure pieces -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "fragment"),
    [
        (([1, 2], 1, "both", 10), "point_mm"),
        (([1, 2, True], 1, "both", 10), "point_mm"),
        (([1, 2, 3], 0, "both", 10), "radius_mm"),
        (([1, 2, 3], True, "both", 10), "radius_mm"),
        (([1, 2, 3], 2000, "both", 10), "radius_mm"),
        (([1, 2, 3], 1, "vertices", 10), "kinds"),
        (([1, 2, 3], 1, "both", 0), "limit"),
        (([1, 2, 3], 1, "both", 2.5), "limit"),
    ],
)
def test_a_malformed_query_is_named(args: tuple[Any, ...], fragment: str) -> None:
    problem = gq.query_problem(*args)

    assert problem is not None and problem.startswith(fragment)
    with pytest.raises(ValueError, match=fragment):
        gq.query_geometry(_Adapter(None), *args)


def test_the_geometry_helpers() -> None:
    box = ((0.0, 0.0, 0.0), (0.003, 0.004, 0.0))

    assert gq.query_problem([1, 2, 3], 1, "both", 10) is None
    assert gq.farthest(None, (0, 0, 0)) == math.inf
    assert gq.farthest(box, (0.0, 0.0, 0.0)) == pytest.approx(0.005)
    assert gq.within(None, (9, 9, 9), 0.0)
    assert not gq.within(box, (0.1, 0.0, 0.0), 0.001)
    assert gq.unit([0, 0, 0]) is None and gq.unit([1, 2]) is None
    assert gq.nearest_on_axis([0, 0, 0], [0, 0, 1], (1, 1, 5)) == [0, 0, 5]
    assert gq.mm([0.001, -0.0]) == [1.0, 0.0]


def test_kept_descriptions_go_with_their_index() -> None:
    model, _log = query_plate()
    index = ei.PartIndex(_Adapter(model), model, 1)

    assert gq.kept_for(index) is gq.kept_for(index)
    assert gq.kept_for(ei.PartIndex(_Adapter(model), model, 1)) is not gq.kept_for(
        index
    )


def test_the_log_still_counts_every_call() -> None:
    log = Log()
    log.calls.update({"SelectByRay": 3})

    assert log.total == 3
