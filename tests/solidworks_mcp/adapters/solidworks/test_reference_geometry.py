"""Sketch frames, plane axes, and axes and planes through any point, on fake COM."""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import reference_geometry as rg

_IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
_TOP = [1, 0, 0, 0, 0, -1, 0, 1, 0, 0, 0.005, 0, 1, 0, 0, 0]


class _Transform:
    def __init__(self, data: list[float], inverse: list[float] | None = None) -> None:
        self.ArrayData = data
        self._inverse = inverse

    def Inverse(self) -> Any:  # noqa: N802
        return _Transform(self._inverse) if self._inverse is not None else None


class _Feature:
    def __init__(self, name: str, kind: str, specific: Any = None) -> None:
        self.Name, self.kind, self.specific = name, kind, specific
        self.selected: list[Any] = []

    def GetTypeName2(self) -> str:  # noqa: N802
        return self.kind

    def GetSpecificFeature2(self) -> Any:  # noqa: N802
        return self.specific

    def Select2(self, append: bool, mark: int) -> bool:  # noqa: N802
        self.selected.append((append, mark))
        return True


class _Segment:
    def __init__(self, name: str | None = "Line1") -> None:
        self.name = name

    def GetName(self) -> str | None:  # noqa: N802
        return self.name


class _Part:
    """Enough of an IModelDoc2 for a 3D sketch line, an axis and a plane."""

    def __init__(self) -> None:
        self.features: list[_Feature] = [
            _Feature(
                "Front Plane",
                "RefPlane",
                SimpleNamespace(Transform=_Transform(_IDENTITY)),
            ),
            _Feature(
                "Top Plane", "RefPlane", SimpleNamespace(Transform=_Transform(_TOP))
            ),
            _Feature("Boss-Extrude1", "Extrusion"),
        ]
        self.selections: list[tuple[Any, ...]] = []
        self.lines: list[tuple[float, ...]] = []
        self.deleted: list[int] = []
        self.active_sketch: Any = None
        self.axis_made = True
        self.plane_made = True
        self.select_line_by_name = True
        self.segment = _Segment()
        self.axis_params: Any = None
        self.plane_transform: list[float] | None = None
        self.in_sketch = False
        manager = self
        self.SketchManager = SimpleNamespace(
            Insert3DSketch=self._toggle,
            CreateLine=self._line,
            AddToDB=False,
        )
        self.SketchManager.__dict__["ActiveSketch"] = None
        self.Extension = SimpleNamespace(
            SelectByID2=self._select,
            GetLastFeatureAdded=lambda: manager.features[-1],
            DeleteSelection2=lambda options: manager.deleted.append(options) or True,
        )
        self.FeatureManager = SimpleNamespace(
            GetFeatureCount=lambda _top: len(manager.features),
            InsertRefPlane=self._plane,
        )

    # --- sketches
    def _toggle(self, _rebuild: bool) -> None:
        self.in_sketch = not self.in_sketch
        if self.in_sketch:
            name = f"3DSketch{sum(f.kind == '3DProfileFeature' for f in self.features) + 1}"
            self.SketchManager.ActiveSketch = SimpleNamespace(Name=name)
        else:
            sketch = self.SketchManager.ActiveSketch
            self.SketchManager.ActiveSketch = None
            if self.lines and sketch is not None:
                self.features.append(_Feature(sketch.Name, "3DProfileFeature"))

    def _line(self, *coordinates: float) -> Any:
        self.lines.append(coordinates)
        return self.segment

    def GetActiveSketch2(self) -> Any:  # noqa: N802
        return self.active_sketch

    def ClearSelection2(self, _all: bool) -> None:  # noqa: N802
        self.selections.append(("clear",))

    def _select(
        self,
        name: str,
        kind: str,
        x: float,
        y: float,
        z: float,
        append: bool,
        mark: int,
        _c: Any,
        _o: int,
    ) -> bool:
        self.selections.append(
            (name, kind, round(x, 9), round(y, 9), round(z, 9), append, mark)
        )
        if kind == "EXTSKETCHSEGMENT" and name:
            return self.select_line_by_name
        return True

    # --- features
    def InsertAxis2(self, _auto: bool) -> bool:  # noqa: N802
        if self.axis_made:
            axis = SimpleNamespace(GetRefAxisParams=lambda: self.axis_params)
            self.features.append(_Feature(f"Axis{len(self.features)}", "RefAxis", axis))
        return self.axis_made

    def _plane(self, *args: Any) -> Any:
        self.plane_args = args
        if not self.plane_made:
            return None
        data = self.plane_transform or _IDENTITY
        feature = _Feature(
            "Plane1", "RefPlane", SimpleNamespace(Transform=_Transform(data))
        )
        self.features.append(feature)
        return feature

    def FeatureByName(self, name: str) -> Any:  # noqa: N802
        return next((f for f in self.features if f.Name == name), None)

    def FeatureByPositionReverse(self, index: int) -> Any:  # noqa: N802
        return self.features[-1 - index]


class _Adapter(rg.SolidWorksReferenceGeometryMixin):
    def __init__(self, part: Any = None) -> None:
        self.currentModel = part if part is not None else _Part()
        self.currentSketch: Any = None
        self.operations: list[str] = []

    def _attempt(self, operation: Any, default: Any = None) -> Any:
        try:
            return operation()
        except Exception:  # noqa: BLE001
            return default

    def _get_attr_or_call(self, obj: Any, name: str) -> Any:
        value = getattr(obj, name)
        return value() if callable(value) else value

    def _handle_com_operation(self, name: str, operation: Any) -> AdapterResult:
        self.operations.append(name)
        try:
            return AdapterResult(status=AdapterResultStatus.SUCCESS, data=operation())
        except Exception as exc:  # noqa: BLE001
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=f"Error in {name}: {exc}"
            )


# --- the open sketch


async def test_the_open_sketch_frame_is_read_unrounded() -> None:
    adapter = _Adapter()
    third = 1 / 3
    inverse = [1, 0, 0, 0, 0, -1, 0, 1, 0, third / 1000, 0.002, 0, 1, 0, 0, 0]
    adapter.currentSketch = SimpleNamespace(
        ModelToSketchTransform=_Transform([0] * 16, inverse)
    )

    result = await adapter.get_open_sketch_axes()

    assert adapter.operations == ["get_open_sketch_axes"]
    assert result.data["origin_mm"] == [round(third, 9), 2.0, 0.0]
    assert result.data["summary"] == (
        "sketch x = model +X, sketch y = model -Z, normal = model +Y"
    )


async def test_the_active_sketch_is_read_when_none_is_tracked() -> None:
    part = _Part()
    part.SketchManager.ActiveSketch = SimpleNamespace(
        ModelToSketchTransform=_Transform([0] * 16, _IDENTITY)
    )

    result = await _Adapter(part).get_open_sketch_axes()

    assert result.data["normal"] == [0.0, 0.0, 1.0]


@pytest.mark.parametrize(
    ("sketch", "error"),
    [
        (None, "No sketch is open"),
        (SimpleNamespace(), "gave no transform"),
        (
            SimpleNamespace(ModelToSketchTransform=_Transform([0] * 16)),
            "gave no transform",
        ),
    ],
)
async def test_an_unreadable_sketch_is_said(sketch: Any, error: str) -> None:
    adapter = _Adapter()
    adapter.currentSketch = sketch

    result = await adapter.get_open_sketch_axes()

    assert not result.is_success
    assert error in result.error
    assert not result.error.startswith("Error in")


async def test_no_model_is_said_plainly() -> None:
    adapter = _Adapter()
    adapter.currentModel = None

    assert (await adapter.plane_axes("Top")).error == "No active model"
    with pytest.raises(RuntimeError, match="No active model"):
        rg.open_sketch_frame(adapter)


# --- plane axes


async def test_a_default_plane_is_read_by_its_short_name() -> None:
    result = await _Adapter().plane_axes("Top")

    assert result.data["plane"] == "Top Plane"
    assert result.data["method"] == "plane_transform"
    assert result.data["axes"]["origin_mm"] == [0.0, 5.0, 0.0]
    assert result.data["axes"]["summary"].startswith("sketch x = model +X")
    assert result.data["frame"]["y"] == [0.0, 0.0, -1.0]


@pytest.mark.parametrize(
    ("plane", "error"),
    [
        ("Plane9", "Plane 'Plane9' not found in the part"),
        ("Boss-Extrude1", "'Boss-Extrude1' is a Extrusion, not a plane"),
    ],
)
async def test_what_is_not_a_plane_is_refused(plane: str, error: str) -> None:
    result = await _Adapter().plane_axes(plane)

    assert error in result.error


async def test_a_plane_without_a_transform_is_said() -> None:
    part = _Part()
    part.features[0].specific = None

    result = await _Adapter(part).plane_axes("Front")

    assert result.error == "SolidWorks gave no transform for 'Front Plane'."
    assert rg.frame_of_named_plane(_Adapter(part), None) is None
    assert rg.frame_of_named_plane(_Adapter(part), "Nope") is None


# --- axis through two points


async def test_an_axis_is_drawn_on_a_3d_sketch_line_and_read_back() -> None:
    part = _Part()
    part.axis_params = [0.02, 0.0, 0.01, 0.02, 0.05, 0.01]
    adapter = _Adapter(part)

    result = await adapter.create_axis_through([20, 0, 10], [20, 10, 10])

    data = result.data
    assert adapter.operations == ["create_axis_through"]
    assert part.lines == [(0.02, 0.0, 0.01, 0.02, 0.01, 0.01)]
    assert part.SketchManager.AddToDB is False
    assert (
        "Line1@3DSketch1",
        "EXTSKETCHSEGMENT",
        0.0,
        0.0,
        0.0,
        False,
        0,
    ) in part.selections
    assert data["name"] == "Axis4"
    assert data["sketch"] == "3DSketch1"
    assert data["direction"] == [0.0, 1.0, 0.0]
    assert data["read_back"] == {
        "start_mm": [20.0, 0.0, 10.0],
        "end_mm": [20.0, 50.0, 10.0],
        "on_line": True,
        "same_sense": True,
    }
    assert (data["features_before"], data["features_after"]) == (3, 5)


async def test_a_line_found_only_by_its_midpoint_still_makes_the_axis() -> None:
    part = _Part()
    part.select_line_by_name = False
    part.axis_params = [0.0, 0.0, 0.0, 0.0, 0.0, -0.01]

    result = await _Adapter(part).create_axis_through([0, 0, 0], [0, 0, 10])

    assert ("", "EXTSKETCHSEGMENT", 0.0, 0.0, 0.005, False, 0) in part.selections
    assert result.data["read_back"]["same_sense"] is False


@pytest.mark.parametrize(
    "params", [None, [1, 2], [0, 0, 0, 0, 0, 0], [0, 0, 0, 0.001, 0.001, 0]]
)
async def test_an_axis_read_back_that_is_unreadable_or_off_is_reported(
    params: Any,
) -> None:
    part = _Part()
    part.axis_params = params

    result = await _Adapter(part).create_axis_through([0, 0, 0], [0, 0, 10])

    read_back = result.data["read_back"]
    assert read_back is None or read_back["on_line"] is False


async def test_no_axis_deletes_the_sketch_it_drew() -> None:
    part = _Part()
    part.axis_made = False

    result = await _Adapter(part).create_axis_through([0, 0, 0], [0, 0, 10])

    assert result.error == "SolidWorks made no axis from the 3D sketch line."
    assert part.deleted == [1]
    assert part.features[-1].selected == [(False, 0)]


async def test_an_axis_refuses_an_open_sketch_and_same_points() -> None:
    adapter = _Adapter()
    adapter.currentSketch = object()
    assert (
        "exit_sketch first"
        in (await adapter.create_axis_through([0, 0, 0], [0, 0, 1])).error
    )
    part = _Part()
    part.active_sketch = object()
    assert (
        "exit_sketch first"
        in (await _Adapter(part).create_axis_through([0, 0, 0], [0, 0, 1])).error
    )
    same = await _Adapter().create_axis_through([1, 1, 1], [1, 1, 1])
    assert "same point" in same.error


async def test_a_sketch_that_does_not_open_or_draw_is_said() -> None:
    part = _Part()
    part.SketchManager.Insert3DSketch = lambda _r: None
    assert (
        "opened no 3D sketch"
        in (await _Adapter(part).create_axis_through([0, 0, 0], [0, 0, 1])).error
    )
    part = _Part()
    part.SketchManager.CreateLine = lambda *_a: None
    result = await _Adapter(part).create_axis_through([0, 0, 0], [0, 0, 1])
    assert "drew no 3D sketch line" in result.error
    assert part.in_sketch is False


async def test_an_unreadable_count_or_unselectable_line_is_said() -> None:
    part = _Part()
    part.FeatureManager.GetFeatureCount = lambda _top: None
    assert (
        "feature count could not be read"
        in (await _Adapter(part).create_axis_through([0, 0, 0], [0, 0, 1])).error
    )
    part = _Part()
    part.Extension.SelectByID2 = lambda *_a: False
    assert (
        "could not be selected"
        in (await _Adapter(part).create_axis_through([0, 0, 0], [0, 0, 1])).error
    )


def test_a_sketch_name_that_does_not_read_comes_from_the_tree() -> None:
    part = _Part()
    toggle = part._toggle

    def nameless(rebuild: bool) -> None:
        toggle(rebuild)
        if part.in_sketch:
            part.SketchManager.ActiveSketch = SimpleNamespace(Name=None)
        else:
            part.features[-1].Name = "3DSketch7"

    part.SketchManager.Insert3DSketch = nameless
    line = rg.draw_line_3d(_Adapter(part), (0, 0, 0), (0, 0, 1))

    assert line.sketch == "3DSketch7"


def test_a_failed_sketch_with_no_name_or_feature_is_left() -> None:
    part = _Part()
    adapter = _Adapter(part)
    rg.remove_sketch(adapter, rg.Line3D(None, None, (0, 0, 0), (0, 0, 1)))
    rg.remove_sketch(adapter, rg.Line3D("Gone", None, (0, 0, 0), (0, 0, 1)))

    assert part.deleted == []


# --- plane through a point


_C, _S = math.cos(math.radians(30)), math.sin(math.radians(30))
#: A vertical plane through (20, 0, 10) whose normal is (sin 30, 0, cos 30).
_CAGE = [_C, 0, -_S, 0, 1, 0, _S, 0, _C, 0.02, 0, 0.01, 1, 0, 0, 0]


async def test_a_plane_is_square_to_a_line_from_its_point() -> None:
    part = _Part()
    part.plane_transform = _CAGE
    adapter = _Adapter(part)

    result = await adapter.create_plane_through([20, 0, 10], [_S * 2, 0, _C * 2])

    data = result.data
    start, end = part.lines[0][:3], part.lines[0][3:]
    assert start == (0.02, 0.0, 0.01)
    assert math.dist(start, end) == pytest.approx(0.01)
    assert part.plane_args == (2, 0.0, 4, 0.0, 0, 0.0)
    assert ("", "EXTSKETCHPOINT", 0.02, 0.0, 0.01, True, 1) in part.selections
    assert data["name"] == "Plane1"
    assert data["normal"] == pytest.approx([_S, 0, _C])
    assert data["normal_matches"] is True
    assert data["through_point"] is True
    assert data["axes"]["origin_mm"] == [20.0, 0.0, 10.0]


async def test_a_reversed_plane_through_its_point_is_reported() -> None:
    part = _Part()
    part.plane_transform = [1, 0, 0, 0, -1, 0, 0, 0, -1, 0, 0, 0, 1, 0, 0, 0]

    data = (await _Adapter(part).create_plane_through([0, 0, 0], [0, 0, 1])).data

    assert data["normal_matches"] is False
    assert data["through_point"] is True


async def test_a_plane_made_off_its_point_is_deleted_and_refused() -> None:
    # Dish Rack truss build: asked through X = -28.4, made through X = +28.4.
    part = _Part()
    part.plane_transform = [1, 0, 0, 0, -1, 0, 0, 0, -1, 0, 0, 0.05, 1, 0, 0, 0]

    result = await _Adapter(part).create_plane_through([0, 0, 0], [0, 0, 1])

    assert result.is_error
    assert "Plane1 50 mm from point [0.0, 0.0, 0.0], not through it" in result.error
    assert "deleted" in result.error
    assert part.deleted == [1]  # the 3D sketch, with the plane below it


class _StartPoint:
    """A 3D sketch line's start point, selected as an object."""

    def __init__(self) -> None:
        self.selected: list[tuple[bool, Any]] = []

    def Select4(self, append: bool, data: Any) -> bool:  # noqa: N802
        self.selected.append((append, getattr(data, "Mark", None)))
        return True


async def test_the_line_start_is_selected_as_itself_not_by_where_it_is() -> None:
    part = _Part()
    part.plane_transform = _CAGE
    point = _StartPoint()
    part.segment.GetStartPoint2 = lambda: point
    part.SelectionManager = SimpleNamespace(CreateSelectData=SimpleNamespace)

    result = await _Adapter(part).create_plane_through([20, 0, 10], [_S, 0, _C])

    assert result.is_success, result.error
    assert point.selected == [(True, 1)]  # appended to the line, mark 1
    assert not any(s[1:2] == ("EXTSKETCHPOINT",) for s in part.selections)


async def test_a_start_point_that_wont_select_falls_back_to_where_it_is() -> None:
    for selection_manager in (
        None,  # no selection manager
        SimpleNamespace(CreateSelectData=lambda: None),  # no select data
    ):
        part = _Part()
        part.plane_transform = _CAGE
        part.segment.GetStartPoint2 = _StartPoint
        part.SelectionManager = selection_manager

        result = await _Adapter(part).create_plane_through([20, 0, 10], [_S, 0, _C])

        assert result.is_success, result.error
        assert ("", "EXTSKETCHPOINT", 0.02, 0.0, 0.01, True, 1) in part.selections


async def test_a_plane_whose_frame_does_not_read_has_no_axes() -> None:
    part = _Part()
    part.plane_transform = [2] * 16

    data = (await _Adapter(part).create_plane_through([0, 0, 0], [0, 0, 1])).data

    assert data["axes"] is None and data["normal_matches"] is None


async def test_no_plane_deletes_its_sketch() -> None:
    part = _Part()
    part.plane_made = False

    result = await _Adapter(part).create_plane_through([0, 0, 0], [0, 0, 1])

    assert result.error == "SolidWorks made no plane from the 3D sketch line."
    assert part.deleted == [1]


async def test_an_unselectable_line_end_is_said() -> None:
    part = _Part()
    select = part._select
    part.Extension.SelectByID2 = lambda name, kind, *rest: (
        kind != "EXTSKETCHPOINT" and select(name, kind, *rest)
    )
    assert (
        "start of the line"
        in (await _Adapter(part).create_plane_through([0, 0, 0], [0, 0, 1])).error
    )
    part = _Part()
    part.Extension.SelectByID2 = lambda *_a: False
    assert (
        "could not be selected"
        in (await _Adapter(part).create_plane_through([0, 0, 0], [0, 0, 1])).error
    )


async def test_a_normal_of_no_length_is_refused() -> None:
    result = await _Adapter().create_plane_through([0, 0, 0], [0, 0, 0])

    assert "the normal [0.0, 0.0, 0.0] has no length" in result.error


def test_the_mixin_methods_live_on_the_pywin32_adapter() -> None:
    from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter

    for name in (
        "get_open_sketch_axes",
        "plane_axes",
        "create_axis_through",
        "create_plane_through",
    ):
        owner = next(k for k in PyWin32Adapter.__mro__ if name in vars(k))
        assert owner.__name__ == "SolidWorksReferenceGeometryMixin"


def test_an_axis_read_back_needs_a_name_a_feature_and_an_axis() -> None:
    part = _Part()
    adapter = _Adapter(part)
    start, end = (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)
    part.features.append(_Feature("Axis9", "RefAxis", None))

    assert rg._axis_read_back(adapter, None, start, end) is None
    assert rg._axis_read_back(adapter, "Gone", start, end) is None
    assert rg._axis_read_back(adapter, "Axis9", start, end) is None


def test_a_plane_without_a_transform_property_has_no_frame() -> None:
    feature = _Feature("Plane5", "RefPlane", SimpleNamespace())

    assert rg.plane_frame(_Adapter(), feature) is None
