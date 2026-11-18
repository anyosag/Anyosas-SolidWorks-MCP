"""The dimensions a sketch pattern adds, read from the open sketch's feature."""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

from solidworks_mcp.adapters.solidworks import sketch_pattern_dimensions as dims


def _attempt(fn: Any, default: Any = None) -> Any:
    try:
        return fn()
    except Exception:
        return default


class FakeDimension:
    """An ``IDimension``: its full name, type and value in metres or radians."""

    def __init__(self, full_name: str, kind: int, system_value: float) -> None:
        self.FullName = full_name
        self._kind = kind
        self.SystemValue = system_value

    def GetType(self) -> int:
        return self._kind


class FakeDisplay:
    def __init__(self, dimension: FakeDimension) -> None:
        self._dimension = dimension

    def GetDimension2(self, _index: int) -> FakeDimension:
        return self._dimension


class FakeSketchFeature:
    """A sketch feature whose display dimensions are a list we can grow."""

    def __init__(self, dimensions: list[FakeDimension]) -> None:
        self.dimensions = dimensions

    def _displays(self) -> list[FakeDisplay]:
        return [FakeDisplay(d) for d in self.dimensions]

    def GetFirstDisplayDimension(self) -> FakeDisplay | None:
        shown = self._displays()
        self._shown = shown
        return shown[0] if shown else None

    def GetNextDisplayDimension(self, display: FakeDisplay) -> FakeDisplay | None:
        index = self._shown.index(display)
        return self._shown[index + 1] if index + 1 < len(self._shown) else None


def _adapter(feature: Any, name: str | None = "Sketch2") -> SimpleNamespace:
    sketch = SimpleNamespace(Name=name) if name else None
    model = SimpleNamespace(FeatureByName=lambda n: feature if n == "Sketch2" else None)
    return SimpleNamespace(
        currentSketch=sketch,
        _last_sketch_name="Sketch2",
        currentModel=model,
        _attempt=_attempt,
    )


def test_short_name_drops_the_document() -> None:
    assert dims.short_name("D1@Sketch1@Part1.SLDPRT") == "D1@Sketch1"
    assert dims.short_name("D2@Sketch3") == "D2@Sketch3"
    assert dims.short_name("D1") is None
    assert dims.short_name(None) is None


def test_open_sketch_name_prefers_the_sketch_then_the_last_name() -> None:
    adapter = _adapter(None)
    assert dims.open_sketch_name(adapter) == "Sketch2"
    adapter.currentSketch = SimpleNamespace(Name="Sketch7")
    assert dims.open_sketch_name(adapter) == "Sketch7"
    adapter.currentSketch = None
    adapter._last_sketch_name = "Sketch4"
    assert dims.open_sketch_name(adapter) == "Sketch4"
    adapter._last_sketch_name = None
    assert dims.open_sketch_name(adapter) is None


def test_sketch_dimensions_reads_the_feature_chain() -> None:
    feature = FakeSketchFeature(
        [
            FakeDimension("D1@Sketch2@Part1.SLDPRT", 0, 0.0012),
            FakeDimension("D2@Sketch2@Part1.SLDPRT", 2, 6.0),
        ]
    )
    found = dims.sketch_dimensions(_adapter(feature))
    assert sorted(found) == ["D1@Sketch2", "D2@Sketch2"]


def test_sketch_dimensions_is_empty_without_a_sketch_or_feature() -> None:
    adapter = _adapter(None)
    assert dims.sketch_dimensions(adapter) == {}
    adapter.currentSketch = None
    adapter._last_sketch_name = None
    assert dims.sketch_dimensions(adapter) == {}
    no_model = _adapter(FakeSketchFeature([]))
    no_model.currentModel = None
    assert dims.sketch_dimensions(no_model) == {}


def test_new_dimensions_names_what_the_pattern_added() -> None:
    seed = FakeDimension("D1@Sketch2@Part1.SLDPRT", 0, 0.0012)
    feature = FakeSketchFeature([seed])
    adapter = _adapter(feature)
    before = dims.sketch_dimensions(adapter)
    feature.dimensions += [
        FakeDimension("D10@Sketch2@Part1.SLDPRT", 2, 6.0),
        FakeDimension("D2@Sketch2@Part1.SLDPRT", 0, 0.02),
        FakeDimension("D3@Sketch2@Part1.SLDPRT", 1, math.radians(360)),
        FakeDimension("D4@Sketch2@Part1.SLDPRT", 0, "unreadable"),
    ]

    added = dims.new_dimensions(adapter, before, dims.CIRCULAR_ROLES)

    assert [d["name"] for d in added] == [
        "D2@Sketch2",
        "D3@Sketch2",
        "D4@Sketch2",
        "D10@Sketch2",
    ]
    assert added[0] == {
        "name": "D2@Sketch2",
        "role": "radius",
        "value": 20.0,
        "units": "mm",
        "kind": "length",
    }
    assert added[1]["role"] == "angle" and added[1]["value"] == 360.0
    assert added[2]["value"] is None
    assert added[3] == {
        "name": "D10@Sketch2",
        "role": "count",
        "value": 6.0,
        "units": "count",
        "kind": "count",
    }


def test_linear_roles_call_a_length_the_spacing() -> None:
    feature = FakeSketchFeature([])
    adapter = _adapter(feature)
    feature.dimensions.append(FakeDimension("D1@Sketch2", 0, 0.005))
    added = dims.new_dimensions(adapter, {}, dims.LINEAR_ROLES)
    assert added[0]["role"] == "spacing" and added[0]["value"] == 5.0
