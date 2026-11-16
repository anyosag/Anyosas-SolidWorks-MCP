"""Reading features and sketches without changing them (``inspection``)."""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import inspection as ins


def _raises(*_a: Any) -> Any:
    raise AttributeError("not on this interface")


class _Adapter:
    def __init__(self, model: Any = None) -> None:
        self.currentModel = model

    def _attempt(self, operation: Any, default: Any = None) -> Any:
        try:
            return operation()
        except Exception:  # noqa: BLE001
            return default


class _Feature:
    """``IFeature``: its type, definition, sketch, parents and dimensions."""

    def __init__(
        self,
        name: str,
        kind: str,
        data: Any = None,
        sketch: Any = None,
        parents: list[Any] | None = None,
        dimensions: list[Any] | None = None,
        subs: list[Any] | None = None,
        older: str | None = None,
    ) -> None:
        self.Name, self._kind, self._data, self._sketch = name, kind, data, sketch
        self._parents = parents or []
        self._dimensions = dimensions or []
        self._subs = subs or []
        self._older = older
        self.edited = False

    def GetTypeName2(self) -> str:  # noqa: N802
        return self._kind

    def GetTypeName(self) -> str | None:  # noqa: N802
        return self._older

    def GetDefinition(self) -> Any:  # noqa: N802
        return self._data

    def ModifyDefinition(self, *_a: Any) -> bool:  # noqa: N802
        self.edited = True
        return True

    def IsSuppressed(self) -> bool:  # noqa: N802
        return False

    def GetSpecificFeature2(self) -> Any:  # noqa: N802
        return self._sketch

    def GetParents(self) -> list[Any]:  # noqa: N802
        return self._parents

    def GetFirstDisplayDimension(self) -> Any:  # noqa: N802
        return self._dimensions[0] if self._dimensions else None

    def GetNextDisplayDimension(self, current: Any) -> Any:  # noqa: N802
        index = self._dimensions.index(current) + 1
        return self._dimensions[index] if index < len(self._dimensions) else None

    def GetFirstSubFeature(self) -> Any:  # noqa: N802
        return self._subs[0] if self._subs else None

    def GetNextSubFeature(self) -> Any:  # noqa: N802
        return None


class _Model:
    def __init__(self, *features: _Feature) -> None:
        self.features = {f.Name: f for f in features}
        self.calls: list[str] = []

    def FeatureByName(self, name: str) -> Any:  # noqa: N802
        return self.features.get(name)

    def EditSketch(self) -> None:  # noqa: N802
        self.calls.append("EditSketch")


def _display(full_name: str, system: Any, kind: int = 0) -> Any:
    dimension = SimpleNamespace(
        FullName=full_name, SystemValue=system, GetType=lambda: kind
    )
    return SimpleNamespace(GetDimension2=lambda _i: dimension)


# ---- the definitions ------------------------------------------------------------


class _Extrude:
    """``IExtrudeFeatureData2``: 10 mm blind with a 3 deg draft, 4 mm back."""

    def __init__(self, boss: Any = True, both: bool = True) -> None:
        self.BothDirections, self.ReverseDirection = both, False
        self.Merge, self.FlipSideToCut = True, True
        self._boss = boss

    def GetDepth(self, forward: bool) -> float:  # noqa: N802
        return 0.01 if forward else 0.004

    def GetEndCondition(self, forward: bool) -> int:  # noqa: N802
        return 0 if forward else 1

    def GetDraftWhileExtruding(self, forward: bool) -> bool:  # noqa: N802
        return forward

    def GetDraftAngle(self, forward: bool) -> float:  # noqa: N802
        return math.radians(3)

    def GetDraftOutward(self, forward: bool) -> bool:  # noqa: N802
        return True

    def IsThinFeature(self) -> bool:  # noqa: N802
        return True

    def GetWallThickness(self, forward: bool) -> float:  # noqa: N802
        return 0.0015

    def IsBossFeature(self) -> Any:  # noqa: N802
        if self._boss is None:
            raise AttributeError("not readable")
        return self._boss


def test_an_extrude_reads_both_directions_draft_and_wall() -> None:
    values = ins.read_extrude(_Adapter(), _Extrude())

    assert values == {
        "direction1": {
            "depth_mm": 10.0,
            "end_condition": "Blind",
            "draft": True,
            "draft_angle_deg": 3.0,
            "draft_outward": True,
        },
        "both_directions": True,
        "reverse_direction": False,
        "direction2": {
            "depth_mm": 4.0,
            "end_condition": "ThroughAll",
            "draft": False,
        },
        "thin": True,
        "wall_thickness_mm": 1.5,
        "cut": False,
        "merge": True,
    }


def test_a_cut_reads_its_side_and_one_without_a_boss_flag_says_nothing() -> None:
    cut = ins.read_extrude(_Adapter(), _Extrude(boss=False, both=False))
    unknown = ins.read_extrude(_Adapter(), _Extrude(boss=None, both=False))

    assert cut["cut"] is True and cut["flip_side_to_cut"] is True
    assert "direction2" not in cut
    assert "cut" not in unknown and "merge" not in unknown


def test_a_revolve_reads_its_angles() -> None:
    def data(kind: int) -> Any:
        return SimpleNamespace(
            Type=kind,
            ReverseDirection=True,
            GetRevolutionAngle=lambda forward: math.pi if forward else math.pi / 2,
            IsThinFeature=lambda: False,
        )

    two = ins.read_revolve(_Adapter(), data(2))
    one = ins.read_revolve(_Adapter(), data(0))

    assert two == {
        "angle_deg": 180.0,
        "revolve_type": "two_direction",
        "reverse_direction": True,
        "angle2_deg": 90.0,
        "thin": False,
    }
    assert one["revolve_type"] == "one_direction" and "angle2_deg" not in one


def test_a_fillet_reads_its_radius_and_edges() -> None:
    data = SimpleNamespace(
        DefaultRadius=0.002,
        GetEdgeCount=lambda: 4,
        Type=0,
        IsMultipleRadius=False,
        PropagateToTangentFaces=True,
    )

    assert ins.read_fillet(_Adapter(), data) == {
        "radius_mm": 2.0,
        "edge_count": 4,
        "fillet_type": "constant_radius",
        "multiple_radius": False,
        "tangent_propagation": True,
    }


def _chamfer(kind: Any) -> Any:
    return SimpleNamespace(
        Type=kind,
        GetEdgeCount=lambda: 2,
        EdgeChamferAngle=math.radians(30),
        GetEdgeChamferDistance=lambda side: 0.001 * (side + 1),
        GetVertexChamferDistance=lambda side: 0.0005 * (side + 1),
    )


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (1, {"chamfer_type": "angle_distance", "distance_mm": 1.0, "angle_deg": 30.0}),
        (
            2,
            {
                "chamfer_type": "distance_distance",
                "distance_mm": 1.0,
                "distance2_mm": 2.0,
            },
        ),
        (16, {"chamfer_type": "equal_distance", "distance_mm": 1.0}),
        (3, {"chamfer_type": "vertex", "vertex_distances_mm": [0.5, 1.0, 1.5]}),
        (
            None,
            {
                "chamfer_type": None,
                "distance_mm": 1.0,
                "angle_deg": 30.0,
                "distance2_mm": 2.0,
            },
        ),
    ],
)
def test_a_chamfer_reads_what_its_type_has(kind: Any, expected: dict) -> None:
    assert ins.read_chamfer(_Adapter(), _chamfer(kind)) == {
        "edge_count": 2,
        **expected,
    }


def test_a_linear_pattern_reads_one_or_two_directions() -> None:
    def data(two: bool) -> Any:
        return SimpleNamespace(
            D1TotalInstances=4,
            D1Spacing=0.025,
            D1ReverseDirection=False,
            D2TotalInstances=2,
            D2Spacing=0.01,
            D2ReverseDirection=True,
            IsDirection2Specified=lambda: two,
            GetPatternFeatureCount=lambda: 1,
            GetSkippedItemCount=lambda: 0,
        )

    two = ins.read_linear_pattern(_Adapter(), data(True))
    one = ins.read_linear_pattern(_Adapter(), data(False))

    assert two == {
        "direction1": {"count": 4, "spacing_mm": 25.0, "reverse_direction": False},
        "direction2": {"count": 2, "spacing_mm": 10.0, "reverse_direction": True},
        "patterned_features": 1,
        "skipped_instances": 0,
    }
    assert "direction2" not in one


def test_a_circular_pattern_says_what_its_angle_is() -> None:
    data = SimpleNamespace(
        TotalInstances=6,
        Spacing=2 * math.pi,
        EqualSpacing=True,
        ReverseDirection=False,
        Direction2=True,
        TotalInstances2=3,
        Spacing2=math.pi / 2,
        Symmetric=False,
        GetPatternFeatureCount=lambda: 2,
        GetSkippedItemCount=_raises,
    )

    values = ins.read_circular_pattern(_Adapter(), data)
    stepped = ins.read_circular_pattern(
        _Adapter(), SimpleNamespace(EqualSpacing=False, Spacing=math.pi / 3)
    )
    unknown = ins.read_circular_pattern(_Adapter(), SimpleNamespace())

    assert values == {
        "count": 6,
        "spacing_deg": 360.0,
        "equal_spacing": True,
        "spacing_is": "total",
        "reverse_direction": False,
        "direction2": {"count": 3, "spacing_deg": 90.0, "symmetric": False},
        "patterned_features": 2,
        "skipped_instances": None,
    }
    assert stepped["spacing_is"] == "step" and stepped["spacing_deg"] == 60.0
    assert unknown["spacing_is"] is None and unknown["count"] is None


def test_numbers_that_are_not_numbers_read_as_none() -> None:
    assert ins._mm(True) is None and ins._mm("x") is None
    assert ins._deg(None) is None
    assert ins._count(False) is None
    assert ins._named(ins.END_CONDITIONS, "odd") == "odd"
    assert ins._named(ins.END_CONDITIONS, 42) == 42


# ---- dimensions and text --------------------------------------------------------


def test_a_features_dimensions_come_by_set_dimension_name() -> None:
    feature = _Feature(
        "Boss-Extrude1",
        "Extrusion",
        dimensions=[
            _display("D1@Boss-Extrude1@Part1.Part", 0.012),
            SimpleNamespace(GetDimension2=lambda _i: None),
            _display("D2@Boss-Extrude1@Part1.Part", "n/a", kind=1),
        ],
    )

    assert ins.feature_dimensions(_Adapter(), feature) == [
        {"name": "D1@Boss-Extrude1", "value": 12.0, "units": "mm"},
        {"name": "D2@Boss-Extrude1", "value": None, "units": "deg"},
    ]


class _TextFormat:
    TypeFaceName, CharHeight, CharHeightInPts = "Arial", 0.005, 14
    Escapement, Bold, Italic = math.radians(90), True, False

    def __init__(self, points: bool) -> None:
        self._points = points

    def IsHeightSpecifiedInPts(self) -> bool:  # noqa: N802
        return self._points


class _Text:
    """``ISketchText`` (``swSketchTEXT`` segment, type 4)."""

    def __init__(self, text: Any = "CELLVIS", fmt: Any = None) -> None:
        self.Text, self._format = text, fmt
        self.ConstructionGeometry = False

    def GetType(self) -> int:  # noqa: N802
        return 4

    def GetCoordinates(self) -> tuple[float, ...]:  # noqa: N802
        return (0.012, -0.003, 0.0)

    def GetUseDocTextFormat(self) -> bool:  # noqa: N802
        return False

    def GetTextFormat(self) -> Any:  # noqa: N802
        return self._format


def test_a_text_reads_its_string_font_height_and_place() -> None:
    assert ins.describe_text(_Adapter(), _Text(fmt=_TextFormat(points=True))) == {
        "text": "CELLVIS",
        "position_mm": [12.0, -3.0],
        "uses_document_font": False,
        "font": "Arial",
        "height_mm": 5.0,
        "height_points": 14,
        "angle_deg": 90.0,
        "bold": True,
        "italic": False,
    }


def test_a_text_without_a_format_or_place_gives_what_reads() -> None:
    text = SimpleNamespace(Text=7, GetCoordinates=lambda: None)

    assert ins.describe_text(_Adapter(), text) == {
        "text": None,
        "position_mm": None,
        "uses_document_font": None,
    }
    in_mm = ins.describe_text(_Adapter(), _Text(fmt=_TextFormat(points=False)))
    assert "height_points" not in in_mm


class _Line:
    def __init__(self) -> None:
        self.ConstructionGeometry = True

    def GetType(self) -> int:  # noqa: N802
        return 0

    def GetStartPoint2(self) -> Any:  # noqa: N802
        return SimpleNamespace(X=0.0, Y=0.0)

    def GetEndPoint2(self) -> Any:  # noqa: N802
        return SimpleNamespace(X=0.01, Y=0.0)


class _Sketch:
    def __init__(self, segments: list[Any], texts: list[Any] | None = None) -> None:
        self._segments, self._texts = segments, texts or []
        self.ModelToSketchTransform: Any = None

    def GetSketchSegments(self) -> list[Any]:  # noqa: N802
        return self._segments

    def GetSketchTextSegments(self) -> list[Any]:  # noqa: N802
        return self._texts


def test_segments_carry_their_text_and_need_no_edit() -> None:
    segments = ins.sketch_segments(_Adapter(), _Sketch([_Line(), _Text()]))

    assert segments[0] == {
        "index": 0,
        "type": "line",
        "start_mm": [0.0, 0.0],
        "end_mm": [10.0, 0.0],
        "construction": True,
    }
    assert segments[1]["type"] == "text"
    assert segments[1]["text"] == "CELLVIS"


def test_text_missing_from_the_segments_is_read_from_the_text_list() -> None:
    segments = ins.sketch_segments(_Adapter(), _Sketch([_Line()], [_Text("A1")]))

    assert [s["type"] for s in segments] == ["line", "text"]
    assert segments[1]["index"] == 1 and segments[1]["text"] == "A1"


def test_a_raw_dispatch_is_wrapped_before_it_is_read(monkeypatch) -> None:
    class PyIDispatch:  # the name pywin32 gives an unwrapped array item
        pass

    wrapped = _Line()
    monkeypatch.setattr(
        "solidworks_mcp.adapters.solidworks.io._as_com",
        lambda _adapter, obj, interface: wrapped,
    )

    raw = PyIDispatch()
    assert ins._dispatchable(_Adapter(), raw) is wrapped
    plain = _Line()
    assert ins._dispatchable(_Adapter(), plain) is plain
    monkeypatch.setattr(
        "solidworks_mcp.adapters.solidworks.io._as_com", lambda *_a: None
    )
    assert ins._dispatchable(_Adapter(), raw) is raw


def test_the_axes_come_from_the_inverse_transform() -> None:
    top = [1, 0, 0, 0, 0, -1, 0, 1, 0, 0, 0.005, 0, 1, 0, 0, 0]
    inverse = SimpleNamespace(ArrayData=top)
    sketch = _Sketch([])
    sketch.ModelToSketchTransform = SimpleNamespace(Inverse=lambda: inverse)

    axes = ins.sketch_axes(_Adapter(), sketch)
    no_inverse = _Sketch([])
    no_inverse.ModelToSketchTransform = SimpleNamespace(Inverse=lambda: None)

    assert axes["summary"] == (
        "sketch x = model +X, sketch y = model -Z, normal = model +Y"
    )
    assert axes["origin_mm"] == [0.0, 5.0, 0.0]
    assert ins.sketch_axes(_Adapter(), _Sketch([])) is None
    assert ins.sketch_axes(_Adapter(), no_inverse) is None


# ---- whole sketches and features ------------------------------------------------


def _plane(name: str) -> Any:
    return SimpleNamespace(Name=name, GetTypeName2=lambda: "RefPlane")


def test_a_sketch_is_read_without_editing_it() -> None:
    sketch = _Sketch([_Line(), _Line(), _Text()])
    feature = _Feature(
        "Sketch1", "ProfileFeature", sketch=sketch, parents=[_plane("Top Plane")]
    )
    model = _Model(feature)

    data = ins.read_sketch(_Adapter(model), "Sketch1")

    assert data["sketch"] == "Sketch1" and data["kind"] == "2D"
    assert data["plane"] == "Top Plane"
    assert data["references"] == [{"name": "Top Plane", "type": "RefPlane"}]
    assert data["axes"] is None
    assert data["segment_counts"] == {"line": 2, "text": 1}
    assert model.calls == []  # EditSketch never ran


def test_a_sketch_on_a_face_has_no_plane() -> None:
    face_owner = SimpleNamespace(Name="Boss-Extrude1", GetTypeName2=lambda: "Extrusion")
    feature = _Feature(
        "Sketch2", "3DProfileFeature", sketch=_Sketch([]), parents=[face_owner]
    )

    data = ins.read_sketch(_Adapter(_Model(feature)), "Sketch2")

    assert data["kind"] == "3D" and data["plane"] is None


def test_read_sketch_refuses_what_is_not_a_sketch() -> None:
    model = _Model(_Feature("Fillet1", "Fillet"), _Feature("Sketch9", "ProfileFeature"))

    with pytest.raises(ValueError, match="'Fillet1' is a Fillet, not a sketch"):
        ins.read_sketch(_Adapter(model), "Fillet1")
    with pytest.raises(RuntimeError, match="no sketch for 'Sketch9'"):
        ins.read_sketch(_Adapter(model), "Sketch9")
    with pytest.raises(LookupError, match="not found"):
        ins.read_sketch(_Adapter(model), "Nope")


def test_an_extrude_definition_names_its_sketch_and_text() -> None:
    sketch_feature = _Feature(
        "Sketch3", "ProfileFeature", sketch=_Sketch([_Text("LID")])
    )
    extrude = _Feature(
        "Boss-Extrude1",
        "ICE",
        data=_Extrude(both=False),
        subs=[sketch_feature],
        older="Extrusion",
        dimensions=[_display("D1@Boss-Extrude1@Part1.Part", 0.01)],
    )
    model = _Model(extrude, sketch_feature)

    data = ins.feature_definition(_Adapter(model), "Boss-Extrude1")

    assert data["type"] == "Extrusion" and data["known_type"] is True
    assert data["suppressed"] is False
    assert data["parameters"]["direction1"]["depth_mm"] == 10.0
    assert data["sketch"] == {"name": "Sketch3", "texts": ["LID"]}
    assert data["dimensions"] == [
        {"name": "D1@Boss-Extrude1", "value": 10.0, "units": "mm"}
    ]
    assert extrude.edited is False  # no ModifyDefinition


def test_a_feature_with_no_definition_says_so() -> None:
    sketch_feature = _Feature("Sketch4", "ProfileFeature")
    cut = _Feature("Cut-Extrude1", "Cut", data=None, subs=[sketch_feature])

    data = ins.feature_definition(_Adapter(_Model(cut, sketch_feature)), "Cut-Extrude1")

    assert data["note"] == "SolidWorks gave no definition for 'Cut-Extrude1'."
    assert data["parameters"] == {}
    assert data["sketch"] == {"name": "Sketch4"}


def test_the_built_from_sketch_is_named_even_when_it_cannot_be_read() -> None:
    gone = _Feature("Sketch5", "ProfileFeature")
    fillet = _Feature(
        "Fillet1", "Fillet", data=SimpleNamespace(DefaultRadius=0.001), subs=[gone]
    )
    adapter = _Adapter(_Model(fillet))  # Sketch5 isn't found by name

    data = ins.feature_definition(adapter, "Fillet1")

    assert data["sketch"] == {"name": "Sketch5"}
    assert data["parameters"]["radius_mm"] == 1.0


def test_a_sketch_definition_gives_its_plane_and_text() -> None:
    feature = _Feature(
        "Sketch1",
        "ProfileFeature",
        sketch=_Sketch([_Line(), _Text("A")]),
        parents=[_plane("Front Plane")],
    )

    data = ins.feature_definition(_Adapter(_Model(feature)), "Sketch1")

    assert data["parameters"]["plane"] == "Front Plane"
    assert data["parameters"]["segment_counts"] == {"line": 1, "text": 1}
    assert data["parameters"]["texts"][0]["text"] == "A"
    assert "index" not in data["parameters"]["texts"][0]


def test_another_type_gives_its_type_and_dimensions() -> None:
    shell = _Feature(
        "Shell1", "Shell", dimensions=[_display("D1@Shell1@Part1.Part", 0.002)]
    )

    data = ins.feature_definition(_Adapter(_Model(shell)), "Shell1")

    assert data["known_type"] is False
    assert data["parameters"] == {}
    assert data["note"].startswith("get_feature_definition reads the parameters of")
    assert data["dimensions"][0]["value"] == 2.0


def test_a_feature_built_from_no_sketch_names_none() -> None:
    chamfer = _Feature("Chamfer1", "Chamfer", data=_chamfer(16))

    data = ins.feature_definition(_Adapter(_Model(chamfer)), "Chamfer1")

    assert "sketch" not in data
    assert data["parameters"]["chamfer_type"] == "equal_distance"
