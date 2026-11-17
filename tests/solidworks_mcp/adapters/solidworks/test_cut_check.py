"""A blind cut's floor area, and the warning for a cut whose depth was mostly air."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import cut_check

# Sketch on Top: x = +X, y = -Z, normal = +Y (inverse ArrayData rows).
_TOP = [1, 0, 0, 0, 0, -1, 0, 1, 0, 0, 0.005, 0, 1, 0, 0, 0]


class _Adapter:
    def _attempt(self, callback: Any, default: Any = None) -> Any:
        try:
            return callback()
        except Exception:
            return default


def _sketch_feature(data: Any = _TOP) -> Any:
    inverse = SimpleNamespace(ArrayData=data)
    transform = SimpleNamespace(Inverse=lambda: inverse)
    sketch = SimpleNamespace(ModelToSketchTransform=transform)
    return SimpleNamespace(GetSpecificFeature2=lambda: sketch)


def _face(normal: list[float], area_m2: float) -> Any:
    return SimpleNamespace(Normal=normal, GetArea=lambda: area_m2)


def _cut(*faces: Any) -> Any:
    return SimpleNamespace(GetFaces=lambda: list(faces))


def test_the_floor_is_the_faces_looking_back_at_the_sketch() -> None:
    # Cut against the +Y normal (down, from a face into the part): its floor
    # faces up, +Y.
    cut = _cut(
        _face([0, 1, 0], 1e-4),  # floor: 100 mm²
        _face([0, 1, 0], 2e-5),  # a lower step of the floor: 20 mm²
        _face([0, -1, 0], 4e-5),  # a ceiling: not the floor
        _face([1, 0, 0], 5e-5),  # wall
        _face([0, 0, 0], 3e-5),  # cylinder wall (not planar)
        _face([0.01, 0.9999, 0], 1e-6),  # within 2.5 degrees
    )

    assert cut_check.measure_floor(_Adapter(), cut, _sketch_feature(), False) == (
        pytest.approx(121.0)
    )
    assert cut_check.measure_floor(_Adapter(), cut, _sketch_feature(), True) == (
        pytest.approx(40.0)
    )


def test_a_slab_through_a_bar_counts_its_floor_once() -> None:
    # The Dipper's 0.8 mm slot through round bars: the sketch plane lies in the
    # bar, so the cut has a ceiling as big as its floor. Both were counted
    # (90.6 mm²), the cut removed exactly 45.3 x 0.8 mm³, and it was warned.
    slab = _cut(_face([0, -1, 0], 45.3e-6), _face([0, 1, 0], 45.3e-6))

    floor = cut_check.measure_floor(_Adapter(), slab, _sketch_feature(), True)

    assert floor == pytest.approx(45.3)
    assert cut_check.shortfall_warning(45.3 * 0.8, floor, 0.8) is None


def test_a_cut_without_a_floor_has_no_area() -> None:
    cut = _cut(_face([1, 0, 0], 5e-5), _face([0, 0, 0], 3e-5))
    through = _cut(_face([0, -1, 0], 1e-4))  # cut down: only a ceiling, it went through

    assert cut_check.measure_floor(_Adapter(), cut, _sketch_feature(), False) is None
    assert (
        cut_check.measure_floor(_Adapter(), through, _sketch_feature(), False) is None
    )
    assert (
        cut_check.measure_floor(
            _Adapter(), _cut(_face([], 1.0)), _sketch_feature(), False
        )
        is None
    )


@pytest.mark.parametrize(
    "feature",
    [
        _sketch_feature(data=[1, 2, 3]),
        SimpleNamespace(GetSpecificFeature2=lambda: SimpleNamespace()),
    ],
)
def test_an_unreadable_sketch_gives_no_area(feature: Any) -> None:
    cut = _cut(_face([0, 1, 0], 1e-4))

    assert cut_check.measure_floor(_Adapter(), cut, feature, False) is None


def test_a_cut_that_removed_far_less_is_named() -> None:
    warning = cut_check.shortfall_warning(1.13, 4.525, 2.0)

    assert warning is not None
    assert warning.startswith("The cut removed 1.13 mm³, far less than its floor")
    assert "4.525 mm² x 2 mm = 9.05 mm³" in warning


@pytest.mark.parametrize(
    ("removed", "floor", "depth"),
    [
        (9.05, 4.525, 2.0),  # all of it
        (5.0, 4.525, 2.0),  # over half
        (4.525, 4.525, 2.0),  # half: a dome cut from the plane touching it
        (3.0, 4.525, 2.0),  # a third: a cone's tip
        (None, 4.525, 2.0),
        (1.0, None, 2.0),
        (1.0, 4.525, 0.0),
    ],
)
def test_otherwise_nothing_is_said(
    removed: float | None, floor: float | None, depth: float
) -> None:
    assert cut_check.shortfall_warning(removed, floor, depth) is None
