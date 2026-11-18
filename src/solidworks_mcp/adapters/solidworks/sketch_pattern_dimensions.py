"""The dimensions a sketch pattern adds: its count, spacing, angle and radius.

``ISketchManager.CreateCircularSketchStepAndRepeat`` and
``CreateLinearSketchStepAndRepeat`` take flags that show the pattern's own
dimensions (``RadiusDim``, ``AngleDim``, ``CreateNumOfInstancesDim``; the
spacing and instance counts of a linear one). The API help: they "can be
modified by interactive users", which makes the pattern parametric. The
MMPLA well-plate build (SOLIDWORKS 2026 SP4.1) got plain copies instead:
every flag was passed False, so no count dimension appeared and nothing
could change the pattern afterwards.

These helpers read the open sketch's dimensions before and after the
pattern, so the reply names the new ones (``D3@Sketch2``) with what each
measures; ``set_dimension`` takes those names.
"""

from __future__ import annotations

import re
from typing import Any

from .. import sw_type_info as _sw_type_info
from .io import dimension_kind, from_system_value

#: The most display dimensions read from one sketch.
_WALK_LIMIT = 2000

#: What each kind of pattern dimension is (``IDimension.GetType``).
CIRCULAR_ROLES = {"count": "count", "angle": "angle", "length": "radius"}
LINEAR_ROLES = {"count": "count", "angle": "angle", "length": "spacing"}

_NUMBER = re.compile(r"(\d+)")


def short_name(full_name: Any) -> str | None:
    """``D1@Sketch1`` from ``IDimension.FullName`` (``D1@Sketch1@Part1.SLDPRT``)."""
    if not isinstance(full_name, str) or "@" not in full_name:
        return None
    return "@".join(full_name.split("@")[:2])


def open_sketch_name(adapter: Any) -> str | None:
    """The name of the sketch being edited: ``ISketch.Name``, else the last one."""
    sketch = getattr(adapter, "currentSketch", None)
    name = (
        adapter._attempt(lambda: sketch.Name, default=None)
        if sketch is not None
        else None
    )
    if isinstance(name, str) and name:
        return name
    last = getattr(adapter, "_last_sketch_name", None)
    return last if isinstance(last, str) and last else None


def sketch_dimensions(adapter: Any) -> dict[str, Any]:
    """The open sketch's dimensions, by short full name, from its feature.

    ``FeatureByName`` finds a sketch while it is open (pitfall #57), and its
    ``GetFirstDisplayDimension`` chain holds the sketch's dimensions.

    Returns:
        dict[str, Any]: ``IDimension`` dispatches by ``D1@Sketch1`` name;
        empty when the sketch or its feature can't be read.
    """
    name = open_sketch_name(adapter)
    model = getattr(adapter, "currentModel", None)
    if not name or model is None:
        return {}
    feature = adapter._attempt(lambda: model.FeatureByName(name), default=None)
    if feature is None:
        return {}
    feature = _sw_type_info.flagged(feature, "IFeature")
    found: dict[str, Any] = {}
    display = adapter._attempt(lambda: feature.GetFirstDisplayDimension(), default=None)
    steps = 0
    while display is not None and steps < _WALK_LIMIT:
        steps += 1
        shown = _sw_type_info.flagged(display, "IDisplayDimension")
        dim = adapter._attempt(lambda d=shown: d.GetDimension2(0), default=None)
        if dim is not None:
            dim = _sw_type_info.flagged(dim, "IDimension")
            full = short_name(adapter._attempt(lambda d=dim: d.FullName, default=None))
            if full:
                found[full] = dim
        display = adapter._attempt(
            lambda d=display: feature.GetNextDisplayDimension(d), default=None
        )
    return found


def _order(name: str) -> tuple[int, str]:
    """Sort ``D2@Sketch1`` before ``D10@Sketch1``."""
    match = _NUMBER.search(name)
    return (int(match.group(1)) if match else 0, name)


def new_dimensions(
    adapter: Any, before: dict[str, Any], roles: dict[str, str]
) -> list[dict[str, Any]]:
    """The dimensions the open sketch has now and didn't have in ``before``.

    Args:
        adapter: The connected adapter, with the sketch still open.
        before: :func:`sketch_dimensions` read before the pattern.
        roles: What a dimension of each kind is (:data:`CIRCULAR_ROLES`).

    Returns:
        list[dict[str, Any]]: ``name`` (for ``set_dimension``), ``role``
        (count, angle, radius or spacing), ``value`` in ``units`` (mm, deg
        or count) and ``kind``.
    """
    after = sketch_dimensions(adapter)
    added: list[dict[str, Any]] = []
    for name in sorted(set(after) - set(before), key=_order):
        dim = after[name]
        kind, unit, factor = dimension_kind(adapter, dim)
        raw = adapter._attempt(lambda d=dim: d.SystemValue, default=None)
        value = (
            from_system_value(float(raw), kind, factor)
            if isinstance(raw, int | float)
            else None
        )
        added.append(
            {
                "name": name,
                "role": roles.get(kind, kind),
                "value": value,
                "units": unit,
                "kind": kind,
            }
        )
    return added
