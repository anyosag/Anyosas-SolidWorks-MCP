"""A plane's frame: where a sketch on it points, and model <-> sketch points.

The Dipper build drew 21 cuts on vertical planes at 30 and 60 degrees. A
plane made from a face point gives sketch axes nobody can guess (x = +Z,
y = (-0.5, 0.866, 0)), so the session read each plane's axes and projected
its 3D points into sketch coordinates by hand. This module does that
projection, for the ``coordinates: "model"`` input of the sketch tools.

SolidWorks gives a sketch's frame as ``ISketch.ModelToSketchTransform``
(inverted, it is sketch-to-model) and a reference plane's as
``IRefPlane.Transform``. Both read as ``IMathTransform.ArrayData``: 16
numbers applied to row vectors, so the first three rows of the rotation
block are where x, y and the normal land in the model, and elements 9 to 11
are the origin in metres (pitfall #41).

Pure maths: nothing here calls SolidWorks.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

Vector = tuple[float, float, float]

#: How far (mm) a model point may lie off the sketch plane and still be drawn
#: on it.
OFF_PLANE_TOLERANCE_MM = 0.01

#: A unit vector may be this far from length 1 (a transform's rows).
_UNIT_TOLERANCE = 1e-6

#: Two points closer than this (mm) don't make a line.
SAME_POINT_MM = 1e-3


def vector(value: Any, what: str = "a vector") -> Vector:
    """Three numbers as a float vector.

    Raises:
        ValueError: ``value`` isn't three numbers.
    """
    if not isinstance(value, Sequence) or isinstance(value, str) or len(value) != 3:
        raise ValueError(f"{what} must be three numbers [x, y, z], got {value!r}")
    try:
        x, y, z = (float(c) for c in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{what} must be three numbers [x, y, z], got {value!r}"
        ) from exc
    return (x, y, z)


def add(a: Vector, b: Vector) -> Vector:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vector, b: Vector) -> Vector:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a: Vector, factor: float) -> Vector:
    return (a[0] * factor, a[1] * factor, a[2] * factor)


def dot(a: Vector, b: Vector) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vector, b: Vector) -> Vector:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def length(a: Vector) -> float:
    return math.sqrt(dot(a, a))


def unit(a: Vector, what: str = "the direction") -> Vector:
    """``a`` scaled to length 1.

    Raises:
        ValueError: ``a`` has no length.
    """
    size = length(a)
    if size < 1e-12:
        raise ValueError(f"{what} {list(a)} has no length")
    return scale(a, 1.0 / size)


def distance_to_line(point: Vector, on_line: Vector, direction: Vector) -> float:
    """How far ``point`` is from the line through ``on_line`` along ``direction``."""
    return length(cross(sub(point, on_line), unit(direction)))


def two_points(first: Any, second: Any) -> tuple[Vector, Vector]:
    """Two model points as vectors, refusing two that are the same point.

    Raises:
        ValueError: A point isn't three numbers, or the two coincide.
    """
    start, end = vector(first, "point"), vector(second, "second point")
    if length(sub(end, start)) < SAME_POINT_MM:
        raise ValueError(
            f"The two points {list(start)} and {list(end)} mm are the same point; "
            "an axis needs two points apart."
        )
    return start, end


def _rounded(values: Sequence[float], places: int = 9) -> list[float]:
    # + 0.0 turns -0.0 into 0.0.
    return [round(float(v), places) + 0.0 for v in values]


@dataclass(frozen=True)
class Frame:
    """A sketch plane's origin and axes in model coordinates.

    Attributes:
        origin_mm: The sketch origin, model mm.
        x: Sketch x as a unit vector in the model.
        y: Sketch y as a unit vector in the model.
        normal: The plane's normal (sketch z) in the model.
    """

    origin_mm: Vector
    x: Vector
    y: Vector
    normal: Vector

    @classmethod
    def from_transform(cls, data: Any) -> Frame | None:
        """The frame of a sketch-to-model (or plane) ``ArrayData``.

        Args:
            data: The 16 numbers of ``IMathTransform.ArrayData``, or anything
                else when it couldn't be read.

        Returns:
            Frame | None: The frame, or None when ``data`` isn't a rotation
            plus a translation.
        """
        try:
            values = [float(v) for v in data]
        except (TypeError, ValueError):
            return None
        if len(values) < 12:
            return None
        rows = [(values[i], values[i + 1], values[i + 2]) for i in (0, 3, 6)]
        if any(abs(length(row) - 1.0) > _UNIT_TOLERANCE for row in rows):
            return None
        origin = (values[9] * 1000.0, values[10] * 1000.0, values[11] * 1000.0)
        return cls(origin, rows[0], rows[1], rows[2])

    @classmethod
    def from_mapping(cls, data: Any) -> Frame | None:
        """The frame of an ``axes`` dict (``x``, ``y``, ``normal``, ``origin_mm``).

        Returns:
            Frame | None: None when a key is missing or isn't three numbers,
            or an axis isn't a unit vector.
        """
        if not isinstance(data, Mapping):
            return None
        try:
            origin, x, y, normal = (
                vector(data.get(key)) for key in ("origin_mm", "x", "y", "normal")
            )
        except ValueError:
            return None
        if any(abs(length(a) - 1.0) > 1e-5 for a in (x, y, normal)):
            return None
        return cls(origin, x, y, normal)

    @classmethod
    def through(cls, point_mm: Vector, normal: Vector) -> Frame:
        """A frame on the plane through ``point_mm`` with ``normal``.

        Its x is the model axis least along the normal, made square to it,
        and y completes a right-handed frame. Only the plane is SolidWorks'
        own: where SolidWorks puts a sketch's x and origin on such a plane is
        read from the plane, never this.

        Raises:
            ValueError: ``normal`` has no length.
        """
        n = unit(normal, "the normal")
        axes = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        helper = min(axes, key=lambda a: abs(dot(a, n)))
        x = unit(sub(helper, scale(n, dot(helper, n))))
        return cls(point_mm, x, cross(n, x), n)

    def to_sketch(self, point_mm: Vector) -> Vector:
        """``point_mm`` in this frame: sketch x, sketch y and the height off the plane."""
        offset = sub(point_mm, self.origin_mm)
        return (dot(offset, self.x), dot(offset, self.y), dot(offset, self.normal))

    def to_model(self, x: float, y: float, z: float = 0.0) -> Vector:
        """The model point at sketch (``x``, ``y``) and ``z`` along the normal."""
        return add(
            self.origin_mm,
            add(scale(self.x, x), add(scale(self.y, y), scale(self.normal, z))),
        )

    def offset(self, distance_mm: float) -> Frame:
        """This frame moved ``distance_mm`` along its normal."""
        moved = add(self.origin_mm, scale(self.normal, distance_mm))
        return Frame(moved, self.x, self.y, self.normal)

    def as_dict(self) -> dict[str, list[float]]:
        """The frame as an ``axes``-shaped dict, to 1e-9 (mm, unit vectors)."""
        return {
            "x": _rounded(self.x),
            "y": _rounded(self.y),
            "normal": _rounded(self.normal),
            "origin_mm": _rounded(self.origin_mm),
        }

    def describe(self) -> dict[str, Any]:
        """The frame as ``create_sketch`` reports ``axes``, with its ``summary``."""
        from .sketch import describe_sketch_axes

        return describe_sketch_axes(self.x, self.y, self.normal, self.origin_mm)


class OffPlaneError(ValueError):
    """A model point given for a sketch lies off the sketch's plane."""


def project(
    frame: Frame,
    points: Sequence[Any],
    labels: Sequence[str] | None = None,
    tolerance_mm: float = OFF_PLANE_TOLERANCE_MM,
) -> tuple[list[tuple[float, float]], float]:
    """Model points as sketch (x, y), refusing any that are off the plane.

    Args:
        frame: The open sketch's frame.
        points: Model points ``[x, y, z]`` in mm.
        labels: What to call each point in an error, e.g. ``"center"``.
        tolerance_mm: How far off the plane a point may be.

    Returns:
        tuple[list[tuple[float, float]], float]: Each point's sketch (x, y),
        mm (to 1e-9), and the largest distance of any of them from the plane.

    Raises:
        OffPlaneError: A point lies more than ``tolerance_mm`` off the plane;
            the message names it, its distance and the plane's normal.
        ValueError: A point isn't three numbers.
    """
    projected: list[tuple[float, float]] = []
    largest = 0.0
    for index, raw in enumerate(points):
        label = labels[index] if labels and index < len(labels) else f"point {index}"
        point = vector(raw, label)
        x, y, height = frame.to_sketch(point)
        if abs(height) > tolerance_mm:
            raise OffPlaneError(
                f"{label} {_shown(point)} mm is {abs(height):.4g} mm "
                f"{'above' if height > 0 else 'below'} the sketch plane (normal "
                f"{_shown(frame.normal)} through {_shown(frame.origin_mm)} mm; "
                f"tolerance {tolerance_mm:g} mm). Give points on the plane, or "
                "sketch on a plane through them."
            )
        largest = max(largest, abs(height))
        projected.append((round(x, 9) + 0.0, round(y, 9) + 0.0))
    return projected, largest


def _shown(values: Sequence[float]) -> str:
    return "[" + ", ".join(f"{v:.6g}" for v in _rounded(values, 6)) + "]"
