"""The minimum distance between two components (``clearance``).

The fake assembly holds blocks made of six flat faces each. ``GetBox`` pads
every face by 0.15 mm, as SolidWorks pads a plane, so the boxes are only a
lower bound, and the fake ``ClosestDistance`` measures the exact faces in
assembly coordinates, the way probe 1 found SolidWorks does.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import clearance as cl
from solidworks_mcp.adapters.solidworks import io as io_module
from solidworks_mcp.adapters.solidworks import limits

_IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
#: A quarter turn about Y: the component's X axis points along -Z.
_QUARTER_Y = [[0.0, 0.0, -1.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]]
_PAD_M = 0.00015


@pytest.fixture(autouse=True)
def _plain_com(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake objects need no method flagging or dispatch wrapping."""
    monkeypatch.setattr(cl, "_sw_type_info", SimpleNamespace(flagged=lambda o, *_: o))
    monkeypatch.setattr(io_module, "_as_com", lambda _adapter, obj, _iface: obj)


# ---- pure pieces ---------------------------------------------------------------


def test_a_point_turns_by_the_rows_then_moves() -> None:
    assert cl.transform_point([1, 2, 3], _IDENTITY, [10, 0, 0]) == [11, 2, 3]
    # X goes to -Z, Z to +X.
    assert cl.transform_point([1, 0, 0], _QUARTER_Y, [0, 0, 0]) == [0, 0, -1]
    assert cl.transform_point([0, 0, 1], _QUARTER_Y, [0, 0, 0]) == [1, 0, 0]


def test_a_box_is_boxed_again_after_a_turn() -> None:
    """The probe's 20 x 6 x 10 block, turned about Y and moved to x = 100."""
    box = [0, 0, -10, 20, 6, 0]

    assert cl.transform_box(box, _QUARTER_Y, [100, 0, 0]) == [90, 0, -20, 100, 6, 0]
    # Corners may come in any order.
    assert cl.transform_box([20, 6, 0, 0, 0, -10], _IDENTITY, [0, 0, 0]) == [
        0,
        0,
        -10,
        20,
        6,
        0,
    ]


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ([0, 0, 0, 1, 1, 1], [3, 0, 0, 4, 1, 1], 2.0),
        ([0, 0, 0, 1, 1, 1], [4, 5, 0, 5, 6, 1], 5.0),  # 3-4-5 corner to corner
        ([0, 0, 0, 2, 2, 2], [1, 1, 1, 3, 3, 3], 0.0),  # overlap
        ([0, 0, 0, 1, 1, 1], [1, 0, 0, 2, 1, 1], 0.0),  # touching
        ([3, 0, 0, 4, 1, 1], [0, 0, 0, 1, 1, 1], 2.0),  # either order
    ],
)
def test_box_distance(a: list[float], b: list[float], expected: float) -> None:
    assert cl.box_distance(a, b) == pytest.approx(expected)


def test_pairs_come_nearest_first() -> None:
    boxes_a = [[0, 0, 0, 1, 1, 1], [10, 0, 0, 11, 1, 1]]
    boxes_b = [[2, 0, 0, 3, 1, 1], [20, 0, 0, 21, 1, 1]]

    # Box distances 1, 7, 9 and 19.
    assert [(i, j) for _d, i, j in cl.pair_order(boxes_a, boxes_b)] == [
        (0, 0),
        (1, 0),
        (1, 1),
        (0, 1),
    ]


def test_the_search_stops_once_no_pair_can_be_nearer() -> None:
    boxes_a = [[0, 0, 0, 1, 1, 1]]
    boxes_b = [[2, 0, 0, 3, 1, 1], [5, 0, 0, 6, 1, 1], [9, 0, 0, 10, 1, 1]]
    measured: list[tuple[int, int]] = []

    def measure(i: int, j: int) -> tuple[float, list[float], list[float]]:
        measured.append((i, j))
        # The true distance is a little more than the box distance.
        return cl.box_distance(boxes_a[i], boxes_b[j]) + 0.5, [i], [j]

    nearest = cl.nearest_pair(boxes_a, boxes_b, measure)

    assert nearest is not None
    assert (nearest.distance, nearest.pair) == (1.5, (0, 0))
    # 1.5 beats the next bound (4), so only the first pair is measured.
    assert measured == [(0, 0)] and nearest.pairs_checked == 1
    assert (nearest.point_a, nearest.point_b) == ([0], [0])


def test_a_pair_nearer_than_its_box_order_is_still_found() -> None:
    boxes_a = [[0, 0, 0, 1, 1, 1]]
    boxes_b = [[1.5, 0, 0, 3, 1, 1], [2, 0, 0, 3, 1, 1]]
    true = {(0, 0): 3.0, (0, 1): 1.2}

    nearest = cl.nearest_pair(boxes_a, boxes_b, lambda i, j: (true[(i, j)], [], []))

    assert nearest is not None
    assert nearest.pair == (0, 1) and nearest.pairs_checked == 2


def test_a_touching_pair_ends_the_search() -> None:
    boxes = [[0, 0, 0, 1, 1, 1]] * 3
    calls: list[int] = []

    def measure(i: int, j: int) -> tuple[float, list[float], list[float]]:
        calls.append(j)
        return 0.0, [], []

    nearest = cl.nearest_pair(boxes[:1], boxes, measure, touching=1e-9)

    assert nearest is not None and nearest.distance == 0.0
    assert calls == [0]


def test_pairs_that_cannot_be_measured_are_passed_over() -> None:
    boxes_a = [[0, 0, 0, 1, 1, 1]]
    boxes_b = [[2, 0, 0, 3, 1, 1], [4, 0, 0, 5, 1, 1]]
    checks: list[str] = []

    nearest = cl.nearest_pair(
        boxes_a,
        boxes_b,
        lambda i, j: None if j == 0 else (3.0, [], []),
        check=lambda: checks.append("check"),
    )

    assert nearest is not None and nearest.pair == (0, 1)
    assert checks == ["check", "check"]
    assert cl.nearest_pair(boxes_a, boxes_b, lambda i, j: None) is None


# ---- the COM walk, on fakes ----------------------------------------------------


def _faces_of(block_mm: list[float]) -> list[list[float]]:
    """The six faces of a block as flat boxes, metres."""
    lo, hi = [v / 1000 for v in block_mm[:3]], [v / 1000 for v in block_mm[3:]]
    faces = []
    for axis in range(3):
        for level in (lo[axis], hi[axis]):
            face_lo, face_hi = list(lo), list(hi)
            face_lo[axis] = face_hi[axis] = level
            faces.append(face_lo + face_hi)
    return faces


class _Surface:
    def __init__(self, kind: str) -> None:
        self._kind = kind

    def IsPlane(self) -> bool:  # noqa: N802
        return self._kind == "plane"

    def IsCylinder(self) -> bool:  # noqa: N802
        return self._kind == "cylinder"


class _Face:
    """A part face: its exact box (m, part coordinates) and its padded GetBox."""

    def __init__(self, box_m: list[float], kind: str = "plane") -> None:
        self.box_m, self._kind = box_m, kind

    def GetBox(self) -> tuple[float, ...]:  # noqa: N802
        return tuple(
            [v - _PAD_M for v in self.box_m[:3]] + [v + _PAD_M for v in self.box_m[3:]]
        )

    def GetSurface(self) -> Any:  # noqa: N802
        return None if self._kind == "none" else _Surface(self._kind)


class _Twin:
    """The face in the assembly: its exact box placed by the component."""

    def __init__(self, face: _Face, component: _Component) -> None:
        self.box_m = cl.transform_box(face.box_m, component.rows, component.origin_m)


class _Component:
    def __init__(
        self,
        name: str,
        block_mm: list[float] | None,
        origin_mm: list[float] = (0.0, 0.0, 0.0),  # type: ignore[assignment]
        rows: list[list[float]] = _IDENTITY,
        suppression: int = 2,
        kind: str = "plane",
    ) -> None:
        self.Name2, self.rows, self.suppression = name, rows, suppression
        self.origin_m = [v / 1000 for v in origin_mm]
        self.faces = (
            [] if block_mm is None else [_Face(f, kind) for f in _faces_of(block_mm)]
        )
        flat = [x for row in rows for x in row]
        self.Transform2 = SimpleNamespace(
            ArrayData=(*flat, *self.origin_m, 1.0, 0.0, 0.0, 0.0)
        )

    def GetSuppression2(self) -> int:  # noqa: N802
        return self.suppression

    def GetBodies2(self, kind: int) -> Any:  # noqa: N802
        assert kind == cl.SOLID_BODY
        if not self.faces:
            return None
        return [SimpleNamespace(GetFaces=lambda: self.faces)]

    def GetCorrespondingEntity(self, face: _Face) -> _Twin:  # noqa: N802
        return _Twin(face, self)


def _nearest_points(a: list[float], b: list[float]) -> tuple[list[float], ...]:
    pa, pb = [], []
    for k in range(3):
        if a[k + 3] < b[k]:
            pa.append(a[k + 3])
            pb.append(b[k])
        elif b[k + 3] < a[k]:
            pa.append(a[k])
            pb.append(b[k + 3])
        else:
            middle = (max(a[k], b[k]) + min(a[k + 3], b[k + 3])) / 2
            pa.append(middle)
            pb.append(middle)
    return pa, pb


class _Model:
    """An assembly whose ``ClosestDistance`` measures faces only.

    Asked for two components whole, it gives no distance (-1), as a failed
    call does, so the face search runs; ``_WholeModel`` measures them.

    Attributes:
        measured: Face pairs measured.
        whole: Calls that asked for the two components whole.
    """

    def __init__(self, *components: _Component, fails: bool = False) -> None:
        self.components, self.fails = list(components), fails
        self.measured = 0
        self.whole = 0

    def GetComponents(self, _top_level: bool) -> list[_Component]:  # noqa: N802
        return self.components

    def ClosestDistance(self, a: _Twin, b: _Twin, p1: Any, p2: Any) -> float:  # noqa: N802
        if isinstance(a, _Component):
            self.whole += 1
            return -1.0
        self.measured += 1
        if self.fails:
            return -1.0
        pa, pb = _nearest_points(a.box_m, b.box_m)
        p1.value, p2.value = tuple(pa), tuple(pb)
        return math.dist(pa, pb)


class _Adapter:
    def __init__(self, model: _Model) -> None:
        self.currentModel = model

    def _attempt(self, operation: Any, default: Any = None) -> Any:
        try:
            return operation()
        except Exception:  # noqa: BLE001 - mirror the real adapter
            return default


def _latch() -> _Model:
    """A 10 mm block, and another 0.25 mm beyond it in X."""
    return _Model(
        _Component("tongue-1", [0, 0, 0, 10, 10, 10]),
        _Component("slot-1", [0, 0, 0, 10, 10, 10], origin_mm=[10.25, 0, 0]),
    )


def test_two_blocks_a_quarter_millimetre_apart() -> None:
    model = _latch()

    reply = cl.measure_clearance(_Adapter(model), "tongue-1", "slot-1")

    assert reply["clearance_mm"] == pytest.approx(0.25, abs=1e-6)
    assert reply["touching"] is False
    assert reply["point_a_mm"][0] == 10.0 and reply["point_b_mm"][0] == 10.25
    assert reply["face_a"] == {
        "kind": "plane",
        "box_mm": [9.85, -0.15, -0.15, 10.15, 10.15, 10.15],
    }
    assert reply["face_b"]["box_mm"][0] == pytest.approx(10.1)
    # The components whole gave no distance, so the faces were measured:
    # the facing pair first; the stop rule leaves most of the 36 unmeasured.
    assert model.whole == 1 and reply["method"] == "face_pairs"
    assert reply["pairs_total"] == 36
    assert reply["pairs_checked"] == model.measured
    assert reply["pairs_checked"] < 36
    assert (reply["component_a"], reply["component_b"]) == ("tongue-1", "slot-1")


def test_a_turned_component_is_measured_where_it_is() -> None:
    """A 20 x 6 x 10 block turned about Y lands at x 90..100; the gap is 5 mm."""
    model = _Model(
        _Component("plate-1", [0, 0, -20, 85, 6, 0]),
        _Component("block-1", [0, 0, -10, 20, 6, 0], [100, 0, 0], _QUARTER_Y),
    )

    reply = cl.measure_clearance(_Adapter(model), "plate-1", "block-1")

    assert reply["clearance_mm"] == pytest.approx(5.0)
    assert reply["point_b_mm"][0] == pytest.approx(90.0)


def test_overlapping_components_read_zero_and_touching() -> None:
    model = _Model(
        _Component("a-1", [0, 0, 0, 10, 10, 10]),
        _Component("b-1", [0, 0, 0, 10, 10, 10], origin_mm=[9, 0, 0]),
    )

    reply = cl.measure_clearance(_Adapter(model), "a-1", "b-1")

    assert reply["clearance_mm"] == 0.0 and reply["touching"] is True


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("cylinder", "cylinder"), ("other", "other"), ("none", "other")],
)
def test_the_face_kind_comes_from_its_surface(kind: str, expected: str) -> None:
    model = _Model(
        _Component("pin-1", [0, 0, 0, 10, 10, 10], kind=kind),
        _Component("hole-1", [0, 0, 0, 10, 10, 10], origin_mm=[12, 0, 0]),
    )

    reply = cl.measure_clearance(_Adapter(model), "pin-1", "hole-1")

    assert reply["face_a"]["kind"] == expected
    assert reply["face_b"]["kind"] == "plane"


def test_the_same_component_twice_is_an_invalid_request() -> None:
    with pytest.raises(cl.InvalidRequest, match="are both 'tongue-1'"):
        cl.measure_clearance(_Adapter(_latch()), "tongue-1", "tongue-1")


def test_an_unknown_component_lists_the_assembly() -> None:
    with pytest.raises(Exception, match="not found: pin-1") as caught:
        cl.measure_clearance(_Adapter(_latch()), "tongue-1", "pin-1")
    assert "tongue-1, slot-1" in str(caught.value)


@pytest.mark.parametrize(
    ("suppression", "block", "fragment"),
    [
        (0, [0, 0, 0, 1, 1, 1], "component_b 'slot-1' is suppressed"),
        (1, None, "component_b 'slot-1' is lightweight"),
        (2, None, "component_b 'slot-1' holds no solid body"),
    ],
)
def test_a_component_with_nothing_to_measure_is_named(
    suppression: int, block: list[float] | None, fragment: str
) -> None:
    model = _Model(
        _Component("tongue-1", [0, 0, 0, 10, 10, 10]),
        _Component("slot-1", block, suppression=suppression),
    )

    with pytest.raises(cl.InvalidRequest) as caught:
        cl.measure_clearance(_Adapter(model), "tongue-1", "slot-1")
    assert str(caught.value).startswith("Invalid request: ")
    assert fragment in str(caught.value)


def test_an_unreadable_placement_says_so() -> None:
    model = _latch()
    model.components[0].Transform2 = None

    with pytest.raises(RuntimeError, match="placement of component_a 'tongue-1'"):
        cl.measure_clearance(_Adapter(model), "tongue-1", "slot-1")


def test_faces_without_a_twin_or_a_box_are_left_out() -> None:
    model = _latch()
    component = model.components[1]
    component.GetCorrespondingEntity = lambda _face: None  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="measured none of the 36 face pairs"):
        cl.measure_clearance(_Adapter(model), "tongue-1", "slot-1")
    assert model.measured == 0  # no pair had both twins

    model = _latch()
    for face in model.components[1].faces:
        face.GetBox = lambda: None  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="No face of component_b 'slot-1'"):
        cl.measure_clearance(_Adapter(model), "tongue-1", "slot-1")

    model = _latch()
    model.components[0].faces[0].GetBox = lambda: None  # type: ignore[method-assign]
    reply = cl.measure_clearance(_Adapter(model), "tongue-1", "slot-1")
    assert reply["pairs_total"] == 30


def test_nothing_measured_is_an_error() -> None:
    model = _Model(*_latch().components, fails=True)

    with pytest.raises(RuntimeError, match="measured none of the 36 face pairs"):
        cl.measure_clearance(_Adapter(model), "tongue-1", "slot-1")


def test_a_distance_without_its_points_is_not_used() -> None:
    model = _latch()
    model.ClosestDistance = lambda *_args: 0.001  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="measured none"):
        cl.measure_clearance(_Adapter(model), "tongue-1", "slot-1")


def test_reading_faces_stops_when_the_client_has_gone() -> None:
    limits.mark_client_gone("test")

    with pytest.raises(limits.OperationStopped, match="reading faces"):
        cl.measure_clearance(_Adapter(_latch()), "tongue-1", "slot-1")


def test_limits_are_checked_between_measurements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _latch()
    checked: list[str] = []

    def check(doing: str) -> None:
        checked.append(doing)
        if doing == "measuring clearance" and model.measured == 2:
            raise limits.OperationStopped(f"Stopped {doing}")

    monkeypatch.setattr(cl, "limits", SimpleNamespace(check=check))

    with pytest.raises(limits.OperationStopped, match="Stopped measuring"):
        cl.measure_clearance(_Adapter(model), "tongue-1", "slot-1")
    assert model.measured == 2
    assert checked.count("reading faces for the clearance") == 12


# ---- coarse and timeout_s ------------------------------------------------------


def test_coarse_order_breaks_ties_by_the_box_centres() -> None:
    boxes_a = [[0, 0, 0, 10, 10, 10]]
    # Both overlap the first box (bound 0); the second's centre is nearer.
    boxes_b = [[8, 8, 8, 30, 30, 30], [4, 4, 4, 6, 6, 6]]

    assert [(i, j) for _d, i, j in cl.pair_order(boxes_a, boxes_b)] == [(0, 0), (0, 1)]
    assert [(i, j) for _d, i, j in cl.coarse_order(boxes_a, boxes_b)] == [
        (0, 1),
        (0, 0),
    ]


def test_max_pairs_stops_with_the_next_box_as_the_bound() -> None:
    boxes_a = [[0, 0, 0, 1, 1, 1]]
    boxes_b = [[2, 0, 0, 3, 1, 1], [3, 0, 0, 4, 1, 1], [4, 0, 0, 5, 1, 1]]

    # Every true distance is 2.5 beyond its box: 3.5, 4.5, 5.5.
    nearest = cl.nearest_pair(
        boxes_a,
        boxes_b,
        lambda i, j: (cl.box_distance(boxes_a[i], boxes_b[j]) + 2.5, [], []),
        max_pairs=1,
    )

    assert nearest is not None
    assert (nearest.distance, nearest.pairs_checked) == (3.5, 1)
    assert nearest.complete is False and nearest.stopped_by == "coarse"
    # The next pair's box is 2 away: nothing unmeasured is nearer than that.
    assert nearest.lower_bound == nearest.at_least == 2.0


def test_a_search_that_runs_to_its_stop_is_complete() -> None:
    boxes_a = [[0, 0, 0, 1, 1, 1]]
    boxes_b = [[2, 0, 0, 3, 1, 1], [9, 0, 0, 10, 1, 1]]

    nearest = cl.nearest_pair(
        boxes_a, boxes_b, lambda i, j: (1.0 + j, [], []), max_pairs=1
    )

    assert nearest is not None and nearest.complete is True
    assert nearest.at_least == 1.0 and nearest.stopped_by is None


def test_the_time_stops_only_after_a_pair_has_a_distance() -> None:
    boxes_a = [[0, 0, 0, 1, 1, 1]]
    boxes_b = [[2, 0, 0, 3, 1, 1], [2.5, 0, 0, 3, 1, 1], [3, 0, 0, 4, 1, 1]]
    measured: list[int] = []

    def measure(i: int, j: int) -> tuple[float, list[float], list[float]] | None:
        measured.append(j)
        return None if j == 0 else (9.0, [], [])

    nearest = cl.nearest_pair(boxes_a, boxes_b, measure, out_of_time=lambda: True)

    # The first pair gave nothing, so the second was measured before stopping.
    assert measured == [0, 1]
    assert nearest is not None and nearest.stopped_by == "timeout"
    assert nearest.lower_bound == 2.0


def _comb(name: str, teeth: int, origin_x: float) -> _Component:
    """A component of ``teeth`` 2 mm cubes along Y, 4 mm apart."""
    component = _Component(name, [0, 0, 0, 2, 2, 2], origin_mm=[origin_x, 0, 0])
    component.faces = [
        _Face(face)
        for k in range(teeth)
        for face in _faces_of([0, 4 * k, 0, 2, 4 * k + 2, 2])
    ]
    return component


def test_coarse_measures_a_dozen_pairs_and_bounds_the_rest() -> None:
    full = cl.measure_clearance(
        _Adapter(_Model(_comb("a-1", 8, 0), _comb("b-1", 8, 2.5))), "a-1", "b-1"
    )
    model = _Model(_comb("a-1", 8, 0), _comb("b-1", 8, 2.5))

    coarse = cl.measure_clearance(_Adapter(model), "a-1", "b-1", coarse=True)

    assert full["complete"] is True and full["clearance_mm"] == pytest.approx(0.5)
    assert full["at_least_mm"] == full["clearance_mm"]
    assert full["pairs_checked"] > cl.COARSE_PAIRS
    # One call asked for the components whole first, and gave nothing here.
    assert model.whole == 1
    assert coarse["pairs_checked"] == model.measured == cl.COARSE_PAIRS
    assert coarse["method"] == "face_pairs"
    assert coarse["pairs_total"] == 48 * 48
    assert coarse["complete"] is False and coarse["stopped_by"] == "coarse"
    # The true gap lies between the two bounds.
    assert coarse["at_least_mm"] <= 0.5 + 1e-9 <= coarse["clearance_mm"] + 1e-9


def test_coarse_measures_the_facing_pair_first() -> None:
    """Many latch pairs have overlapping boxes; the centres put the facing one first."""
    reply = cl.measure_clearance(_Adapter(_latch()), "tongue-1", "slot-1", coarse=True)

    assert reply["clearance_mm"] == pytest.approx(0.25)
    assert reply["pairs_checked"] <= cl.COARSE_PAIRS
    assert reply["at_least_mm"] <= reply["clearance_mm"]


def test_a_timeout_returns_the_best_so_far() -> None:
    model = _Model(_comb("a-1", 8, 0), _comb("b-1", 8, 2.5))
    ticks = iter(range(10_000))

    reply = cl.measure_clearance(
        _Adapter(model),
        "a-1",
        "b-1",
        timeout_s=2.5,
        clock=lambda: float(next(ticks)),
    )

    # Started at tick 0; before the 2nd, 3rd and 4th pair the clock reads
    # 1, 2 and 3: the fourth is past 2.5 s.
    assert reply["pairs_checked"] == model.measured == 3
    assert reply["complete"] is False and reply["stopped_by"] == "timeout"
    assert reply["at_least_mm"] <= reply["clearance_mm"]


def test_touching_is_complete_however_it_was_asked() -> None:
    model = _Model(
        _Component("a-1", [0, 0, 0, 10, 10, 10]),
        _Component("b-1", [0, 0, 0, 10, 10, 10], origin_mm=[9, 0, 0]),
    )

    reply = cl.measure_clearance(
        _Adapter(model), "a-1", "b-1", coarse=True, timeout_s=0.001
    )

    assert reply["touching"] is True and reply["complete"] is True
    assert reply["at_least_mm"] == 0.0


# ---- the components measured whole ---------------------------------------------


class _WholeModel(_Model):
    """A model whose ``ClosestDistance`` also measures two components whole."""

    def ClosestDistance(self, a: Any, b: Any, p1: Any, p2: Any) -> float:  # noqa: N802
        if isinstance(a, _Component):
            self.whole += 1
            boxes = [cl.transform_box(_box_of(c), c.rows, c.origin_m) for c in (a, b)]
            pa, pb = _nearest_points(*boxes)
            p1.value, p2.value = tuple(pa), tuple(pb)
            return math.dist(pa, pb)
        return super().ClosestDistance(a, b, p1, p2)


def _box_of(component: _Component) -> list[float]:
    boxes = [face.box_m for face in component.faces]
    return [min(b[k] for b in boxes) for k in range(3)] + [
        max(b[k + 3] for b in boxes) for k in range(3)
    ]


@pytest.mark.parametrize("ask", [{}, {"coarse": True}, {"timeout_s": 5.0}])
def test_the_components_are_measured_whole_in_one_call(ask: dict[str, Any]) -> None:
    model = _WholeModel(*_latch().components)

    reply = cl.measure_clearance(_Adapter(model), "tongue-1", "slot-1", **ask)

    # One call, and no face read or measured.
    assert (model.whole, model.measured) == (1, 0)
    assert reply["clearance_mm"] == pytest.approx(0.25)
    assert reply["at_least_mm"] == reply["clearance_mm"]
    assert reply["complete"] is True and reply["method"] == "components"
    assert reply["face_a"] is None and reply["face_b"] is None
    assert (reply["pairs_checked"], reply["pairs_total"]) == (0, None)
    assert reply["point_a_mm"][0] == 10.0 and reply["point_b_mm"][0] == 10.25


def test_faces_ask_for_the_face_search_and_skip_the_whole_call() -> None:
    model = _WholeModel(*_latch().components)

    reply = cl.measure_clearance(_Adapter(model), "tongue-1", "slot-1", faces=True)

    assert model.whole == 0
    assert reply["method"] == "face_pairs" and reply["complete"] is True
    assert reply["face_a"] == {
        "kind": "plane",
        "box_mm": [9.85, -0.15, -0.15, 10.15, 10.15, 10.15],
    }
    assert reply["clearance_mm"] == pytest.approx(0.25)
    assert model.measured == reply["pairs_checked"]


def test_faces_with_coarse_bound_the_face_search() -> None:
    model = _WholeModel(_comb("a-1", 8, 0), _comb("b-1", 8, 2.5))

    reply = cl.measure_clearance(_Adapter(model), "a-1", "b-1", coarse=True, faces=True)

    assert model.whole == 0
    assert reply["method"] == "face_pairs" and reply["stopped_by"] == "coarse"
    assert reply["pairs_checked"] == model.measured == cl.COARSE_PAIRS
    assert reply["complete"] is False


def test_components_touching_whole_read_zero() -> None:
    model = _WholeModel(
        _Component("a-1", [0, 0, 0, 10, 10, 10]),
        _Component("b-1", [0, 0, 0, 10, 10, 10], origin_mm=[9, 0, 0]),
    )

    reply = cl.measure_clearance(_Adapter(model), "a-1", "b-1")

    assert reply["touching"] is True and reply["clearance_mm"] == 0.0
    assert reply["at_least_mm"] == 0.0
    assert reply["method"] == "components"


@pytest.mark.parametrize("ask", [{}, {"coarse": True}])
def test_a_suppressed_component_is_refused_before_the_whole_call(
    ask: dict[str, Any],
) -> None:
    model = _WholeModel(*_latch().components)
    model.components[1].suppression = cl.SUPPRESSED

    with pytest.raises(cl.InvalidRequest, match="component_b 'slot-1' is suppressed"):
        cl.measure_clearance(_Adapter(model), "tongue-1", "slot-1", **ask)
    assert (model.whole, model.measured) == (0, 0)


def test_a_bodiless_component_is_refused_with_faces_too() -> None:
    model = _WholeModel(
        _Component("tongue-1", [0, 0, 0, 10, 10, 10]), _Component("slot-1", None)
    )

    with pytest.raises(cl.InvalidRequest, match="'slot-1' holds no solid body"):
        cl.measure_clearance(_Adapter(model), "tongue-1", "slot-1", faces=True)
    assert model.whole == 0
