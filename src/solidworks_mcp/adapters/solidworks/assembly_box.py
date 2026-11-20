"""A tight box of an assembly's solids: its components' bodies, placed in the assembly.

``IAssemblyDoc.GetBox(0)`` can be loose, and counts more than solids: an
assembly holding a part with a ``create_axis`` axis (a line in a 3D sketch)
read 10 mm tall where the solids are 7.2 mm (Aggrewell enclosure v2,
SOLIDWORKS 2026). A part's summary boxes its solid bodies by their extreme
points (``io._extreme_point_box``); :func:`solid_box` does the same for an
assembly:

* the components from the root down: ``GetComponents(True)``, then
  ``IComponent2.GetChildren`` into each sub-assembly, leaving out a
  suppressed component (``GetSuppression2``) or a hidden one (``Visible``)
  and all below it;
* each part component's solid bodies, ``IComponent2.GetBodies2(swSolidBody)``.
  They come in the part's own coordinates, as their faces' boxes do
  (``clearance``, probed on SOLIDWORKS 2026 SP4.1);
* the component's placement relative to the root, ``Transform2``, read as
  ``clearance`` reads it: a part point ``p`` is ``p . R + t`` in the assembly;
* each body's extreme point along each assembly axis. The axis is turned into
  the part's frame (a column of ``R``) for ``IBody2.GetExtremePoint``, and the
  point found is placed, so the box stays tight however the component turns.

Anything unreadable (a lightweight component, whose bodies aren't loaded; a
placement or an extreme point SolidWorks doesn't give) makes it None, and the
summary falls back to ``GetBox(0)``.
"""

from __future__ import annotations

from typing import Any

from . import limits
from .clearance import LIGHTWEIGHT, SOLID_BODY, SUPPRESSED, transform_point
from .com_utils import as_list
from .component_visibility import is_visible
from .io import _as_com, _box_from_metres, _byref_double

#: ``bounding_box_method`` of the box :func:`solid_box` measures.
METHOD = "component_extreme_points"
#: The most components one walk reads: a guard against a tree that loops.
MAX_COMPONENTS = 20000


class _Unreadable(Exception):
    """Part of the assembly can't be read, so its box would be a guess."""


def solid_box(adapter: Any, assembly: Any) -> dict[str, Any] | None:
    """The box of the shown, unsuppressed components' solid bodies, in mm.

    Args:
        adapter: A connected adapter, on the COM thread.
        assembly: The assembly, flagged for ``IAssemblyDoc``.

    Returns:
        dict[str, Any] | None: ``min``, ``max``, ``size`` and ``method``
        (:data:`METHOD`); None when the components can't all be read, or
        none shows a solid body.
    """
    top = adapter._attempt(lambda: assembly.GetComponents(True), default=None)
    if not isinstance(top, (list, tuple)):
        return None
    low, high = [float("inf")] * 3, [float("-inf")] * 3
    pending = list(top)
    read = 0
    try:
        while pending and read < MAX_COMPONENTS:
            limits.check("reading the assembly's solid bodies for its box")
            read += 1
            component = _as_com(adapter, pending.pop(), "IComponent2")
            if component is None:
                raise _Unreadable
            state = _suppression(adapter, component)
            if state == SUPPRESSED or is_visible(adapter, component) is False:
                continue  # and so is everything below it
            children = _children(adapter, component)
            if children:
                pending.extend(children)
                continue
            for value, axis, is_max in _extremes(adapter, component, state):
                if is_max:
                    high[axis] = max(high[axis], value)
                else:
                    low[axis] = min(low[axis], value)
    except _Unreadable:
        return None
    if pending or high[0] < low[0]:
        return None
    return _box_from_metres([*low, *high], METHOD)


def _suppression(adapter: Any, component: Any) -> Any:
    """``GetSuppression2``: 0 suppressed, 1 or 4 lightweight; None unread."""
    return adapter._attempt(lambda: component.GetSuppression2(), default=None)


def _children(adapter: Any, component: Any) -> list[Any]:
    """A sub-assembly's components (``GetChildren``); none for a part component."""
    return as_list(adapter._attempt(lambda: component.GetChildren(), default=None))


def _extremes(
    adapter: Any, component: Any, state: Any
) -> list[tuple[float, int, bool]]:
    """``(value_m, axis, is_max)`` for each solid body's six extremes, placed.

    Args:
        adapter: A connected adapter.
        component: A part component, shown and not suppressed.
        state: Its ``GetSuppression2``.

    Raises:
        _Unreadable: The bodies, the placement or an extreme point can't be
            read (a lightweight component's bodies aren't loaded).
    """
    raw, error = adapter._attempt_with_error(lambda: component.GetBodies2(SOLID_BODY))
    bodies = as_list(raw)
    if error is not None or (not bodies and state in LIGHTWEIGHT):
        raise _Unreadable
    if not bodies:
        return []  # a part with no solid body: a sketch or surfaces only
    rows, origin = _placement(adapter, component)
    found: list[tuple[float, int, bool]] = []
    for raw_body in bodies:
        body = _as_com(adapter, raw_body, "IBody2")
        for axis in range(3):
            column = [rows[k][axis] for k in range(3)]
            for is_max in (False, True):
                sign = 1.0 if is_max else -1.0
                point = _extreme_point(adapter, body, [sign * c for c in column])
                found.append((transform_point(point, rows, origin)[axis], axis, is_max))
    return found


def _placement(adapter: Any, component: Any) -> tuple[list[list[float]], list[float]]:
    """The component's rotation rows and translation (m), from ``Transform2``.

    Raises:
        _Unreadable: The transform can't be read.
    """
    transform = adapter._attempt(lambda: component.Transform2, default=None)
    data = adapter._attempt(lambda: transform.ArrayData, default=None)
    if not isinstance(data, (list, tuple)) or len(data) < 12:
        raise _Unreadable
    values = [float(v) for v in data[:12]]
    return [values[0:3], values[3:6], values[6:9]], values[9:12]


def _extreme_point(adapter: Any, body: Any, direction: list[float]) -> list[float]:
    """The body's farthest point along ``direction``, in its own coordinates (m).

    Raises:
        _Unreadable: SolidWorks found no point, or gave no number.
    """
    out = (_byref_double(), _byref_double(), _byref_double())
    found = adapter._attempt(
        lambda: body.GetExtremePoint(*direction, *out), default=None
    )
    point = [holder.value for holder in out]
    if not found or not all(isinstance(v, (int, float)) for v in point):
        raise _Unreadable
    return [float(v) for v in point]
