"""``cut_side``: which side of a sketch plane the part's material is on."""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import cut_side
from solidworks_mcp.adapters.solidworks.plane_frame import Frame


class FakeCom:
    """Records the names flagged as methods, as late binding needs."""

    def __init__(self) -> None:
        self.flagged: set[str] = set()

    def _FlagAsMethod(self, *names: str) -> None:  # noqa: N802
        self.flagged.update(names)


class FakeTransform(FakeCom):
    def __init__(self, data: list[float] | None) -> None:
        super().__init__()
        self.ArrayData = data

    def Inverse(self) -> FakeTransform:  # noqa: N802
        # The data given is already sketch-to-model.
        return self


class FakeSketchFeature(FakeCom):
    def __init__(self, data: list[float] | None) -> None:
        super().__init__()
        transform = FakeTransform(data)
        self._sketch = type("Sketch", (), {"ModelToSketchTransform": transform})()

    def GetSpecificFeature2(self) -> Any:  # noqa: N802
        return self._sketch


class FakeBody(FakeCom):
    def __init__(self, box_mm: list[float] | None) -> None:
        super().__init__()
        self.box = None if box_mm is None else [c / 1000.0 for c in box_mm]

    def GetBodyBox(self) -> Any:  # noqa: N802
        if self.box is None:
            raise RuntimeError("no box")
        return tuple(self.box)


class FakeModel(FakeCom):
    def __init__(self, sketches: dict[str, Any], bodies: list[FakeBody]) -> None:
        super().__init__()
        self.sketches = sketches
        self.bodies = bodies

    def FeatureByName(self, name: str) -> Any:  # noqa: N802
        return self.sketches.get(name)

    def GetBodies2(self, kind: int, visible_only: bool) -> Any:  # noqa: N802
        assert kind == 0 and visible_only is False
        return tuple(self.bodies) if self.bodies else None


class FakeAdapter:
    def __init__(self, model: FakeModel) -> None:
        self.currentModel = model

    def _attempt(self, callback: Any, default: Any = None) -> Any:
        try:
            return callback()
        except Exception:
            return default


def plane_data(normal: list[float], origin_mm: list[float]) -> list[float]:
    """A sketch-to-model ``ArrayData`` for a plane through ``origin_mm``."""
    axes = {
        (0.0, 1.0, 0.0): ([1.0, 0.0, 0.0], [0.0, 0.0, -1.0]),
        (0.0, -1.0, 0.0): ([1.0, 0.0, 0.0], [0.0, 0.0, 1.0]),
        (0.0, 0.0, 1.0): ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0]),
    }
    x, y = axes[tuple(float(c) for c in normal)]
    return [*x, *y, *normal, *(c / 1000.0 for c in origin_mm), 1.0, 0.0, 0.0, 0.0]


#: A 40 x 10 x 40 block from y = 0 to y = 10.
BLOCK = [-20.0, 0.0, -20.0, 20.0, 10.0, 20.0]


def _side(data: list[float] | None, *boxes: list[float] | None) -> bool | None:
    model = FakeModel(
        {"Sketch1": FakeSketchFeature(data)}, [FakeBody(box) for box in boxes]
    )
    return cut_side.material_side(FakeAdapter(model), "Sketch1")


def test_a_sketch_on_the_top_face_has_the_part_against_its_normal() -> None:
    assert _side(plane_data([0, 1, 0], [0, 10, 0]), BLOCK) is False


def test_a_sketch_on_the_bottom_face_has_the_part_along_its_normal() -> None:
    assert _side(plane_data([0, 1, 0], [0, 0, 0]), BLOCK) is True


def test_a_plane_outside_the_part_points_toward_it_or_away() -> None:
    assert _side(plane_data([0, 1, 0], [0, -5, 0]), BLOCK) is True
    assert _side(plane_data([0, -1, 0], [0, -5, 0]), BLOCK) is False


def test_a_plane_through_the_part_straddles() -> None:
    assert _side(plane_data([0, 1, 0], [0, 5, 0]), BLOCK) is None


def test_a_box_within_the_tolerance_of_the_plane_counts_as_on_it() -> None:
    assert _side(plane_data([0, 1, 0], [0, 10.0005, 0]), BLOCK) is False


def test_every_body_counts() -> None:
    above = [-5.0, 20.0, -5.0, 5.0, 25.0, 5.0]
    plane = plane_data([0, 1, 0], [0, 15, 0])

    assert _side(plane, BLOCK, above) is None
    assert _side(plane_data([0, 0, 1], [0, 0, 30]), BLOCK, above) is False


def test_box_corners_in_any_order() -> None:
    flipped = [20.0, 10.0, 20.0, -20.0, 0.0, -20.0]

    assert _side(plane_data([0, 1, 0], [0, 10, 0]), flipped) is False


@pytest.mark.parametrize(
    "boxes",
    [
        (),  # no solid body
        (None,),  # a box that doesn't read
        ([1.0, 2.0],),  # too few numbers
        (["a", 0, 0, 1, 1, 1],),  # not numbers
    ],
)
def test_unread_bodies_give_no_side(boxes: tuple[Any, ...]) -> None:
    plane = plane_data([0, 1, 0], [0, 10, 0])
    model = FakeModel({"Sketch1": FakeSketchFeature(plane)}, [])
    model.bodies = [FakeBody(None) for _ in boxes]
    for body, box in zip(model.bodies, boxes, strict=True):
        body.box = box

    assert cut_side.material_side(FakeAdapter(model), "Sketch1") is None


def test_an_unread_plane_gives_no_side() -> None:
    assert _side(None, BLOCK) is None
    assert _side([1.0, 2.0], BLOCK) is None


def test_a_missing_sketch_gives_no_side() -> None:
    model = FakeModel({}, [FakeBody(BLOCK)])

    assert cut_side.material_side(FakeAdapter(model), "Sketch9") is None


def test_no_boxes_give_no_side() -> None:
    frame = Frame((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0))

    assert cut_side.side_of_boxes(frame, []) is None


def test_the_transform_is_flagged_before_it_is_inverted() -> None:
    feature = FakeSketchFeature(plane_data([0, 1, 0], [0, 10, 0]))

    frame = cut_side.sketch_frame(FakeAdapter(FakeModel({}, [])), feature)

    assert frame is not None and frame.normal == (0.0, 1.0, 0.0)
    assert "GetSpecificFeature2" in feature.flagged


def test_a_sketch_without_a_transform_gives_no_frame() -> None:
    feature = FakeSketchFeature(None)
    feature._sketch = object()

    assert cut_side.sketch_frame(FakeAdapter(FakeModel({}, [])), feature) is None
