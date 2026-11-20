"""Which side of its sketch plane a cut has material on, from the bodies' boxes.

A cut left without a direction used to be tried on both sides of the sketch:
built along the normal, measured, deleted, built against it and measured. The
side with nothing to cut is a rebuild error, and SolidWorks plays its error
sound for it although the trial is deleted at once. Most cuts don't need the
trial: a sketch on a face, or on a reference plane outside the part, has all
the material on one side, and that side is the only one a cut can take.

:func:`material_side` reads the sketch's plane (origin and normal, from
``ISketch.ModelToSketchTransform`` inverted, as ``plane_frame`` reads it) and
each solid body's box (``IBody2.GetBodyBox``). When every corner of every box
lies on one side of the plane, within :data:`SIDE_TOLERANCE_MM`, all the
material is on that side: the box holds the body, so nothing of it can be
beyond. When a box straddles the plane the body may still not (a box is not
the body), so the cut is tried as before.

Nothing is selected or changed; any read that fails gives None, and the cut is
tried.
"""

from __future__ import annotations

from itertools import product
from typing import Any

from .com_utils import as_list
from .com_utils import attempt as _attempt
from .com_utils import flag as _flag
from .plane_frame import Frame

#: A box corner this close to the plane (mm) is on it: a sketch on a face.
SIDE_TOLERANCE_MM = 1e-3

#: ``swBodyType_e.swSolidBody``.
_SOLID_BODY = 0


def sketch_frame(adapter: Any, sketch_feature: Any) -> Frame | None:
    """The plane of a sketch feature in model mm, or None when it can't be read."""
    if sketch_feature is None:
        return None
    _flag(adapter, sketch_feature, "GetSpecificFeature2")
    sketch = _attempt(adapter, lambda: sketch_feature.GetSpecificFeature2())
    transform = _attempt(adapter, lambda: sketch.ModelToSketchTransform)
    if transform is None:
        return None
    _flag(adapter, transform, "Inverse")
    inverse = _attempt(adapter, lambda: transform.Inverse())
    return Frame.from_transform(_attempt(adapter, lambda: inverse.ArrayData))


def body_boxes_mm(adapter: Any, model: Any) -> list[list[float]] | None:
    """Each solid body's box as ``[x1, y1, z1, x2, y2, z2]`` in mm.

    Returns:
        list[list[float]] | None: The boxes; None when there is no solid body
        or a box can't be read (one unread body could be anywhere).
    """
    _flag(adapter, model, "GetBodies2")
    bodies = as_list(_attempt(adapter, lambda: model.GetBodies2(_SOLID_BODY, False)))
    if not bodies:
        return None
    boxes: list[list[float]] = []
    for body in bodies:
        _flag(adapter, body, "GetBodyBox")
        raw = _attempt(adapter, lambda b=body: list(b.GetBodyBox()))
        try:
            box = [float(c) * 1000.0 for c in (raw or [])[:6]]
        except (TypeError, ValueError):
            return None
        if len(box) < 6:
            return None
        boxes.append(box)
    return boxes


def side_of_boxes(frame: Frame, boxes: list[list[float]]) -> bool | None:
    """Which side of ``frame``'s plane every box lies on.

    Args:
        frame: The sketch plane.
        boxes: Boxes in model mm, two opposite corners each.

    Returns:
        bool | None: True when all of them lie along the normal, False when
        all lie against it, None when one straddles the plane (or they lie
        within the tolerance of it, as a flat sheet on it would).
    """
    # Every corner: x, y and z each from the first or the second corner.
    heights = [
        frame.to_sketch((box[3 * ix], box[1 + 3 * iy], box[2 + 3 * iz]))[2]
        for box in boxes
        for ix, iy, iz in product((0, 1), repeat=3)
    ]
    if not heights:
        return None
    low, high = min(heights), max(heights)
    if low >= -SIDE_TOLERANCE_MM and high > SIDE_TOLERANCE_MM:
        return True
    if high <= SIDE_TOLERANCE_MM and low < -SIDE_TOLERANCE_MM:
        return False
    return None


def material_side(adapter: Any, sketch_name: str) -> bool | None:
    """Whether all of the part's material lies along ``sketch_name``'s normal.

    Args:
        adapter: A connected adapter with a ``currentModel``.
        sketch_name: The sketch the cut is made from.

    Returns:
        bool | None: True along the normal, False against it, None when the
        part straddles the plane or anything can't be read.
    """
    model = adapter.currentModel
    frame = sketch_frame(
        adapter, _attempt(adapter, lambda: model.FeatureByName(sketch_name))
    )
    if frame is None:
        return None
    boxes = body_boxes_mm(adapter, model)
    return None if boxes is None else side_of_boxes(frame, boxes)
