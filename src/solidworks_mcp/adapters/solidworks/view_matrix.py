"""View orientations as matrices: a direction to look from, and Z-up standard views.

``IModelView.Orientation3`` is an ``IMathTransform`` that takes model
coordinates to view coordinates. A model point ``p`` is at ``p . R`` in the
view (row vectors, SolidWorks' convention for its transforms, pitfall #41),
where the view's x is screen right, its y screen up and its z out of the
screen, toward the viewer. So ``R``'s three columns are, in model
coordinates, the directions shown as screen right, screen up and toward the
viewer. The front view is the identity. The isometric view, from (1, 1, 1), is
``[[0.7071, -0.4082, 0.5774], [0, 0.8165, 0.5774], [-0.7071, -0.4082, 0.5774]]``,
the matrix SolidWorks users report for it.

The standard views are Y-up, so a part modelled Z-up (The Dipper, a lab part
with a hinged cover) stands on end in every one of them. The session turned it
by rotating the part -90 degrees about X in a throwaway assembly. ``Z_UP_TURN``
is that rotation: the model's +Z goes where a standard view has +Y, and +Y
goes to -Z. ``z_up(R)`` is the standard view ``R`` seen that way: the front
view then looks from -Y with +Z up, the top view from +Z with +Y up, and the
isometric view from (1, -1, 1).

Pure Python, no COM: ``view_orientation`` puts these on SolidWorks' view.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

Vector = list[float]
Rows = list[list[float]]

#: How far apart two orientation entries may be and still be the same view.
TOLERANCE = 1e-6

#: Below this length, a cross product means the two vectors are parallel.
_PARALLEL = 1e-9

#: The model axis shown as screen up, by ``up_axis``.
UP_AXES: dict[str, tuple[float, float, float]] = {
    "y": (0.0, 1.0, 0.0),
    "z": (0.0, 0.0, 1.0),
}

#: Rows of the turn that puts a Z-up model where a Y-up one would be: X stays,
#: Y goes to -Z, Z goes to +Y (-90 degrees about X).
Z_UP_TURN: Rows = [[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]]

#: ``export_image``'s standard views -> ``swStandardViews_e`` for
#: ``IModelDoc2.ShowNamedView2`` (SOLIDWORKS 2026 swconst: trimetric is 8,
#: dimetric 9).
STANDARD_VIEW_IDS: dict[str, int] = {
    "front": 1,
    "back": 2,
    "left": 3,
    "right": 4,
    "top": 5,
    "bottom": 6,
    "isometric": 7,
    "trimetric": 8,
    "dimetric": 9,
}

#: SolidWorks' standard views as (toward the viewer, screen up), model
#: coordinates. The top view shows the front at the bottom of the screen, so
#: its up is -Z. Dimetric and trimetric are SolidWorks' own angles and are read
#: from SolidWorks instead.
STANDARD_VIEWS: dict[str, tuple[tuple[float, float, float], tuple[float, ...]]] = {
    "front": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "back": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    "left": ((-1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    "right": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    "top": ((0.0, 1.0, 0.0), (0.0, 0.0, -1.0)),
    "bottom": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "isometric": ((1.0, 1.0, 1.0), (0.0, 1.0, 0.0)),
}


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(float(x) * float(y) for x, y in zip(a, b, strict=True))


def _cross(a: Sequence[float], b: Sequence[float]) -> Vector:
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def unit(vector: Sequence[float], name: str = "a direction") -> Vector:
    """``vector`` scaled to length 1.

    Raises:
        ValueError: It isn't three numbers, or it is the zero vector.
    """
    values = [float(v) for v in vector]
    if len(values) != 3:
        raise ValueError(f"{name} is three numbers [x, y, z]")
    length = math.sqrt(_dot(values, values))
    if length < 1e-12:
        raise ValueError(f"{name} must not be the zero vector")
    return [v / length for v in values]


def parallel(a: Sequence[float], b: Sequence[float]) -> bool:
    """Whether two non-zero vectors point along the same line."""
    return math.sqrt(sum(c * c for c in _cross(unit(a), unit(b)))) < _PARALLEL


def default_up(direction: Sequence[float], up_axis: str = "y") -> Vector:
    """The screen-up direction for a view from ``direction``.

    The ``up_axis`` axis, unless the view looks along it: then, as SolidWorks'
    top and bottom views do, the axis that keeps the front of the model at the
    bottom of the screen (-Z from above a Y-up model, +Y from above a Z-up one).

    Raises:
        ValueError: ``up_axis`` isn't ``"y"`` or ``"z"``, or ``direction`` is
            the zero vector.
    """
    if up_axis not in UP_AXES:
        raise ValueError(f"up_axis is 'y' or 'z', not {up_axis!r}")
    axis = list(UP_AXES[up_axis])
    if not parallel(direction, axis):
        return axis
    above = _dot(unit(direction), axis) > 0
    if up_axis == "y":
        return [0.0, 0.0, -1.0] if above else [0.0, 0.0, 1.0]
    return [0.0, 1.0, 0.0] if above else [0.0, -1.0, 0.0]


def look_from(direction: Sequence[float], up: Sequence[float]) -> Rows:
    """The orientation of a view from ``direction`` with ``up`` shown upward.

    Args:
        direction: From the model toward the viewer, model coordinates:
            ``[0, 0, 1]`` is the front view, ``[1, 1, 1]`` the isometric.
        up: The model direction to show as screen up; only its part at right
            angles to ``direction`` counts.

    Returns:
        Rows: ``R`` with columns screen right, screen up and toward the
        viewer, so a model point ``p`` is at ``p . R`` in the view.

    Raises:
        ValueError: Either is the zero vector, or ``up`` lies along
            ``direction``.
    """
    toward = unit(direction, "view_direction")
    right = _cross(unit(up, "up"), toward)
    length = math.sqrt(_dot(right, right))
    if length < _PARALLEL:
        raise ValueError(
            "up lies along view_direction, so it can't say which way is up; "
            "give an up at an angle to it"
        )
    right = [c / length for c in right]
    screen_up = _cross(toward, right)
    return [[right[i], screen_up[i], toward[i]] for i in range(3)]


def custom_rows(
    direction: Sequence[float], up: Sequence[float] | None, up_axis: str = "y"
) -> Rows:
    """``export_image``'s view from ``direction``, ``up`` or else ``up_axis`` upward.

    Raises:
        ValueError: As :func:`default_up` and :func:`look_from`.
    """
    chosen = default_up(direction, up_axis) if up is None else up
    return look_from(direction, chosen)


def picture_rows(
    orientation: str,
    direction: Sequence[float] | None,
    up: Sequence[float] | None,
    up_axis: str,
) -> Rows | None:
    """The orientation ``export_image`` sets from its own maths, checked first.

    Args:
        orientation: ``view_orientation``, lower case.
        direction: ``view_direction``, or None.
        up: ``up``, or None.
        up_axis: ``"y"`` or ``"z"``.

    Returns:
        Rows | None: :func:`custom_rows` for a ``direction``; None for a
        standard view or ``current`` (a Z-up standard view is read from
        SolidWorks and turned with :func:`z_up`).

    Raises:
        ValueError: An unknown ``up_axis``, a bad ``direction`` or ``up``,
            ``up`` without ``direction``, or ``up_axis`` "z" without a
            standard view to turn.
    """
    if up_axis not in UP_AXES:
        raise ValueError(f"up_axis is 'y' or 'z', not {up_axis!r}")
    if direction is not None:
        return custom_rows(direction, up, up_axis)
    if up is not None:
        raise ValueError("up goes with view_direction; for a standard view use up_axis")
    if up_axis == "z" and orientation not in STANDARD_VIEW_IDS:
        raise ValueError(
            f"up_axis 'z' turns a standard view ({', '.join(STANDARD_VIEW_IDS)}); "
            f"{orientation!r} isn't one"
        )
    return None


def standard_rows(name: str) -> Rows | None:
    """A standard view's orientation, or None for dimetric, trimetric or another name."""
    view = STANDARD_VIEWS.get(name)
    return look_from(*view) if view else None


def times(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> Rows:
    """The 3 x 3 product ``a . b``."""
    return [
        [sum(float(a[i][k]) * float(b[k][j]) for k in range(3)) for j in range(3)]
        for i in range(3)
    ]


def z_up(rows: Sequence[Sequence[float]]) -> Rows:
    """The view ``rows`` turned so the model's +Z shows where +Y did.

    A model point ``p`` is turned first (``p . Z_UP_TURN``), then viewed, so the
    orientation is ``Z_UP_TURN . R``.
    """
    return times(Z_UP_TURN, rows)


def axes(rows: Sequence[Sequence[float]]) -> dict[str, list[float]]:
    """The model directions a view shows as screen right, screen up and toward you."""
    return {
        name: [round(float(rows[i][column]), 6) + 0.0 for i in range(3)]
        for column, name in enumerate(("right", "up", "toward_viewer"))
    }


def rows_from_array(data: Any) -> Rows | None:
    """The rotation of a ``MathTransform``'s ``ArrayData``, or None when unreadable."""
    if not isinstance(data, (list, tuple)) or len(data) < 9:
        return None
    try:
        values = [float(v) for v in data[:9]]
    except (TypeError, ValueError):
        return None
    return [values[0:3], values[3:6], values[6:9]]


def with_rows(data: Any, rows: Sequence[Sequence[float]]) -> list[float]:
    """``ArrayData`` for an orientation ``rows``, keeping the rest of ``data``.

    A view's transform has 16 values: the rotation, a translation, a scale and
    three unused. Only the rotation changes; without 16 readable values the
    rest is no translation and a scale of 1.
    """
    rotation = [float(x) for row in rows for x in row]
    try:
        values = [float(v) for v in data] if isinstance(data, (list, tuple)) else []
    except (TypeError, ValueError):
        values = []
    if len(values) != 16:
        values = [0.0] * 9 + [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
    return rotation + values[9:]


def same(
    a: Sequence[Sequence[float]],
    b: Sequence[Sequence[float]],
    tolerance: float = TOLERANCE,
) -> bool:
    """Whether two orientations are the same within ``tolerance`` per entry."""
    return all(
        abs(float(a[i][j]) - float(b[i][j])) <= tolerance
        for i in range(3)
        for j in range(3)
    )
