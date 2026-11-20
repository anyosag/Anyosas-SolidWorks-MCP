"""Tests for solidworks_mcp.adapters.solidworks.sketch_points (fake COM)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import sketch_points


class _Point:
    def __init__(self, x: float, y: float) -> None:
        self.X, self.Y, self.Z = x, y, 0.0
        self.selected: list[bool] = []

    def Select2(self, append: bool, _mark: int) -> bool:
        self.selected.append(append)
        return True


class _Segment:
    """A sketch line (kind 0) or arc (kind 1), positions in metres."""

    def __init__(
        self,
        kind: int,
        start: tuple[float, float] | None = None,
        end: tuple[float, float] | None = None,
        center: tuple[float, float] | None = None,
        circle: bool = False,
    ) -> None:
        self._kind, self._circle = kind, circle
        self.start = _Point(*start) if start else None
        self.end = _Point(*end) if end else None
        self.center = _Point(*center) if center else None

    def GetType(self) -> int:
        return self._kind

    def IsCircle(self) -> bool:
        return self._circle

    def GetStartPoint2(self) -> Any:
        return self.start

    def GetEndPoint2(self) -> Any:
        return self.end

    def GetCenterPoint2(self) -> Any:
        return self.center


class _Feature:
    def __init__(self, kind: str, following: Any = None, sketch: Any = None) -> None:
        self._kind, self._following, self._sketch = kind, following, sketch

    def GetTypeName2(self) -> str:
        return self._kind

    def GetNextFeature(self) -> Any:
        return self._following

    def GetSpecificFeature2(self) -> Any:
        return self._sketch


class _Adapter:
    def __init__(self, entities: dict[str, Any], first_feature: Any = None) -> None:
        self._sketch_entities = entities
        self._sketch_entity_centers: dict[str, tuple[float, float]] = {}
        self.currentModel = SimpleNamespace(FirstFeature=lambda: first_feature)
        self._sketch_geometry = SimpleNamespace(read_segment_endpoints=lambda _e: None)

    def _attempt(self, callback, default=None):
        try:
            return callback()
        except Exception:
            return default

    def _get_attr_or_call(self, obj: Any, name: str) -> Any:
        value = getattr(obj, name)
        return value() if callable(value) else value


def _origin_tree(point: Any) -> _Feature:
    origin_sketch = SimpleNamespace(GetSketchPoints2=lambda: (point,))
    origin = _Feature("OriginProfileFeature", sketch=origin_sketch)
    return _Feature("RefPlane", following=_Feature("RefPlane", following=origin))


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("Arc_1", ("Arc_1", None)),
        (" Arc_1.center ", ("Arc_1", "center")),
        ("Arc_1.Centre", ("Arc_1", "center")),
        ("Line_2.start", ("Line_2", "start")),
        ("Line_2.END", ("Line_2", "end")),
    ],
)
def test_split_reference(ref: str, expected: tuple[str, str | None]) -> None:
    assert sketch_points.split_reference(ref) == expected


def test_split_reference_refuses_an_unknown_point() -> None:
    with pytest.raises(ValueError, match="Unknown point 'mid'.*Line_2.center"):
        sketch_points.split_reference("Line_2.mid")


def test_origin_resolves_to_the_origin_profile_feature_point() -> None:
    point = _Point(0.0, 0.0)
    adapter = _Adapter({}, first_feature=_origin_tree(point))

    target = sketch_points.resolve(adapter, "origin")

    assert target.ref == "Origin" and target.is_origin and target.is_point
    assert target.obj is point and target.xy == (0.0, 0.0)


def test_origin_without_an_origin_feature_is_an_error() -> None:
    adapter = _Adapter({}, first_feature=_Feature("RefPlane"))

    with pytest.raises(Exception, match="no origin point"):
        sketch_points.resolve(adapter, "Origin")


def test_points_of_entities_resolve_with_their_positions() -> None:
    arc = _Segment(1, (0.01, 0.0), (0.0, 0.01), (0.0, 0.0))
    line = _Segment(0, (0.0, 0.0), (0.05, 0.0))
    adapter = _Adapter({"Arc_1": arc, "Line_2": line})

    centre = sketch_points.resolve(adapter, "Arc_1.center")
    end = sketch_points.resolve(adapter, "Line_2.end")

    assert (centre.ref, centre.obj, centre.xy) == ("Arc_1.center", arc.center, (0, 0))
    assert centre.owner is arc and centre.entity_id == "Arc_1"
    assert (end.ref, end.obj, end.xy) == ("Line_2.end", line.end, (0.05, 0.0))


def test_a_centre_that_does_not_read_uses_the_point_recorded_at_creation() -> None:
    circle = _Segment(1, center=(0.0, 0.0), circle=True)
    circle.center = SimpleNamespace()  # no X / Y
    adapter = _Adapter({"Circle_1": circle})
    adapter._sketch_entity_centers["Circle_1"] = (8.0, 3.0)

    target = sketch_points.resolve(adapter, "Circle_1.center")

    assert target.xy == pytest.approx((0.008, 0.003))


@pytest.mark.parametrize(
    ("entities", "ref", "fragment"),
    [
        ({}, "Arc_9", "Unknown sketch entity 'Arc_9'. Use the ids"),
        ({}, "Arc_9.center", "Unknown sketch entity 'Arc_9.center'"),
        ({"Rect_1": (object(), object())}, "Rect_1.start", "several segments"),
        ({"Line_1": _Segment(0)}, "Line_1.center", "is a line: it has no centre"),
        ({"Circle_1": _Segment(1, circle=True)}, "Circle_1.start", "full circle"),
        ({"Line_1": _Segment(0)}, "Line_1.start", "gave no start point"),
        ({"Line_1": _Segment(0)}, "Line_1.middle", "Unknown point 'middle'"),
    ],
)
def test_references_that_name_nothing(
    entities: dict[str, Any], ref: str, fragment: str
) -> None:
    with pytest.raises(Exception, match=fragment.replace(".", r"\.")):
        sketch_points.resolve(_Adapter(entities), ref)


@pytest.mark.parametrize(
    ("entity", "kind"),
    [
        (_Segment(0), "line"),
        (_Segment(1), "arc"),
        (_Segment(1, circle=True), "circle"),
        # Live, ISketchArc.IsCircle answers the long 1 / 0 (pitfall #60).
        (_Segment(1, circle=1), "circle"),
        (_Segment(1, circle=0), "arc"),
        (_Segment(3), "spline"),
        ((object(), object()), "group"),
        (SimpleNamespace(), "segment"),
    ],
)
def test_segment_kind(entity: Any, kind: str) -> None:
    assert sketch_points.segment_kind(_Adapter({}), entity) == kind


def test_a_plain_entity_resolves_to_itself_and_a_curve_to_its_centre() -> None:
    circle = _Segment(1, center=(0.008, 0.003), circle=True)
    line = _Segment(0, (0.0, 0.0), (0.01, 0.0))
    adapter = _Adapter({"Circle_1": circle, "Line_2": line})

    whole = sketch_points.resolve(adapter, "Circle_1")
    centre = sketch_points.as_centre(adapter, whole)
    kept = sketch_points.as_centre(adapter, sketch_points.resolve(adapter, "Line_2"))

    assert (whole.kind, whole.obj, whole.xy) == ("circle", circle, None)
    assert centre.ref == "Circle_1.center" and centre.obj is circle.center
    assert kept.obj is line


def test_position_reads_a_point_the_recorded_point_or_the_ends() -> None:
    line = _Segment(0, (0.0, 0.0), (0.02, 0.0))
    adapter = _Adapter({"Line_1": line, "Line_2": line})
    adapter._sketch_entity_centers["Line_1"] = (5.0, 0.0)
    adapter._sketch_geometry = SimpleNamespace(
        read_segment_endpoints=lambda _e: ((0.0, 0.0, 0.0), (0.02, 0.0, 0.0))
    )

    recorded = sketch_points.resolve(adapter, "Line_1")
    from_ends = sketch_points.resolve(adapter, "Line_2")
    point = sketch_points.resolve(adapter, "Line_1.end")

    assert sketch_points.position(adapter, recorded) == pytest.approx((0.005, 0.0))
    assert sketch_points.position(adapter, from_ends) == pytest.approx((0.01, 0.0))
    assert sketch_points.with_position(adapter, point) is point
    assert sketch_points.with_position(adapter, from_ends).xy == (0.01, 0.0)


def test_position_is_none_when_nothing_reads() -> None:
    adapter = _Adapter({"Line_1": SimpleNamespace()})

    target = sketch_points.resolve(adapter, "Line_1")

    assert sketch_points.position(adapter, target) is None


def test_signature_follows_the_points() -> None:
    circle = _Segment(1, center=(0.008, 0.003), circle=True)
    adapter = _Adapter({"Circle_1": circle, "Rect_1": (object(),)})

    whole = sketch_points.resolve(adapter, "Circle_1")
    centre = sketch_points.resolve(adapter, "Circle_1.center")
    before = sketch_points.signature(adapter, whole)
    circle.center.Y = 0.0

    assert before == (0.008, 0.003)
    assert sketch_points.signature(adapter, whole) == (0.008, 0.0)
    assert sketch_points.signature(adapter, centre) == (0.008, 0.0)
    assert (
        sketch_points.signature(adapter, sketch_points.resolve(adapter, "Rect_1"))
        is None
    )
    unread = sketch_points.Target("P", "point", SimpleNamespace())
    assert sketch_points.signature(adapter, unread) is None
    assert sketch_points.point_xy(adapter, None) is None


def test_raw_dispatches_are_wrapped(monkeypatch) -> None:
    class PyIDispatch:  # the name pywin32 gives an array item
        pass

    raw = PyIDispatch()
    wrapped = _Point(0.0, 0.0)
    from solidworks_mcp.adapters.solidworks import io

    monkeypatch.setattr(io, "_dynamic", SimpleNamespace(Dispatch=lambda _o: wrapped))

    assert sketch_points._wrapped(_Adapter({}), raw) is wrapped
    assert sketch_points._wrapped(_Adapter({}), wrapped) is wrapped


def test_position_of_a_placed_target_is_its_own() -> None:
    placed = sketch_points.Target("P", "point", object(), (0.001, 0.002))

    assert sketch_points.position(_Adapter({}), placed) == (0.001, 0.002)
