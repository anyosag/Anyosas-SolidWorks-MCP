"""Whether a blind cut went into the material: its floor area times its depth.

From the Cellvis dish rack: a cut sketched on an offset plane removed a
1.13 mm³ sliver where 9.05 mm³ was meant, and reported success. Most of its
depth was in air above the part. A blind cut's floor (its planar faces that
look back at the sketch) has the profile's area wherever the cut reached
material, so floor area times depth is what it should remove; far less means
the depth ran outside the part.

Only the floor counts. ``IFace2.Normal`` points out of the material, so a
planar face looking the way the cut goes is a ceiling: the sketch plane lies
inside the material. A slab cut through a bar has one as big as its floor.
Counting both (the Dipper's 0.8 mm slots through round bars: 90.6 mm² where
the section was 45.3) put every correct cut at half of what it "should"
remove, and each one was warned about.
"""

from __future__ import annotations

import math
from typing import Any

from .com_utils import as_list
from .com_utils import attempt as _attempt
from .com_utils import flag as _flag
from .sketch import axes_from_transform

#: A cut that removed less than this share of floor area x depth is reported.
#: Material whose section grows with depth gives less than floor x depth to a
#: correct cut: from the plane touching it, a cylinder gives 2/3 of it, a dome
#: about 1/2 and a cone 1/3. The dish rack's sliver gave 1/8.
SHORTFALL = 0.25
#: |cos| above which a face faces along the sketch normal (about 2.5 degrees).
_PARALLEL = 0.999


def sketch_normal(adapter: Any, sketch_feature: Any) -> list[float] | None:
    """The normal of a sketch feature's plane, in model coordinates."""
    _flag(adapter, sketch_feature, "GetSpecificFeature2")
    sketch = _attempt(adapter, lambda: sketch_feature.GetSpecificFeature2())
    transform = _attempt(adapter, lambda: sketch.ModelToSketchTransform)
    if transform is None:
        return None
    _flag(adapter, transform, "Inverse")
    inverse = _attempt(adapter, lambda: transform.Inverse())
    axes = axes_from_transform(_attempt(adapter, lambda: inverse.ArrayData))
    return None if axes is None else [float(c) for c in axes["normal"]]


def cut_toward(
    normal: list[float] | None, along: bool, both: bool = False
) -> tuple[str, list[float]] | None:
    """Which way a cut went in the model, from its sketch normal and side.

    Args:
        normal: The sketch normal in model coordinates (None when unread).
        along: The cut went along the normal (False: against it).
        both: It cut both ways (through all both ways, or mid-plane).

    Returns:
        tuple[str, list[float]] | None: ``("-Y", [0, -1, 0])``, or for both
        ways ``("both ways along Y", normal)``; a slanted direction is given
        as its numbers. None without a normal.
    """
    if normal is None:
        return None
    vector = [round(c if along or both else -c, 6) + 0.0 for c in normal]
    for index, letter in enumerate("XYZ"):
        if abs(abs(vector[index]) - 1.0) < 1e-6:
            if both:
                return f"both ways along {letter}", vector
            return ("+" if vector[index] > 0 else "-") + letter, vector
    shown = "(" + ", ".join(f"{c:.4g}" for c in vector) + ")"
    return (f"both ways along {shown}" if both else shown), vector


def facing_areas_mm2(
    adapter: Any, feature: Any, normal: list[float]
) -> tuple[float, float] | None:
    """The areas of the cut's planar faces facing along ``normal`` and against it.

    Args:
        adapter: The adapter (for ``_attempt``).
        feature: The cut's ``IFeature``.
        normal: The sketch normal (a unit vector).

    Returns:
        tuple[float, float] | None: ``(along, against)`` in mm², or None when
        the cut has no planar face parallel to the sketch or its faces can't
        be read.
    """
    _flag(adapter, feature, "GetFaces")
    along = against = 0.0
    found = False
    for face in as_list(_attempt(adapter, lambda: feature.GetFaces())):
        _flag(adapter, face, "GetArea")
        facing = [float(c) for c in (_attempt(adapter, lambda f=face: f.Normal) or [])]
        length = math.sqrt(sum(c * c for c in facing[:3]))
        if len(facing) < 3 or length == 0.0:
            continue  # not planar: IFace2.Normal is [0, 0, 0]
        cos = sum(a * b for a, b in zip(facing[:3], normal, strict=True)) / length
        if abs(cos) <= _PARALLEL:
            continue
        area = float(_attempt(adapter, lambda f=face: f.GetArea()) or 0.0) * 1e6
        if cos > 0:
            along += area
        else:
            against += area
        found = True
    return (along, against) if found else None


def floor_area_mm2(
    adapter: Any, feature: Any, normal: list[float], cut_along: bool
) -> float | None:
    """The area of the cut's floor, its planar faces that look back at the sketch, mm².

    Args:
        adapter: The adapter (for ``_attempt``).
        feature: The cut's ``IFeature``.
        normal: The sketch normal (a unit vector).
        cut_along: Whether the cut went along the sketch normal. Its floor
            then faces against the normal; a face looking along it is a
            ceiling and doesn't count.

    Returns:
        float | None: The area, or None when the cut has no floor (it went
        through) or its faces can't be read.
    """
    areas = facing_areas_mm2(adapter, feature, normal)
    if areas is None:
        return None
    along, against = areas
    floor = against if cut_along else along
    return floor if floor > 0.0 else None


def measure_floor(
    adapter: Any, feature: Any, sketch_feature: Any, cut_along: bool
) -> float | None:
    """The cut's floor area in mm², or None when it can't be told.

    Args:
        adapter: The adapter (for ``_attempt``).
        feature: The cut's ``IFeature``.
        sketch_feature: The sketch it cut from.
        cut_along: Whether it cut along the sketch normal.

    Returns:
        float | None: See :func:`floor_area_mm2`.
    """
    normal = sketch_normal(adapter, sketch_feature)
    if normal is None:
        return None
    return floor_area_mm2(adapter, feature, normal, cut_along)


def shortfall_warning(
    removed_mm3: float | None, floor_mm2: float | None, depth_mm: float
) -> str | None:
    """A warning when a blind cut removed far less than floor area x depth.

    Args:
        removed_mm3: The volume the cut removed (positive), if measured.
        floor_mm2: The cut's floor area, if measured.
        depth_mm: The cut's depth.

    Returns:
        str | None: The warning, or None when the cut removed about what it
        should, or either number is missing.
    """
    if removed_mm3 is None or not floor_mm2 or depth_mm <= 0:
        return None
    expected = floor_mm2 * depth_mm
    if removed_mm3 >= SHORTFALL * expected:
        return None
    return (
        f"The cut removed {removed_mm3:.4g} mm³, far less than its floor area "
        f"times its depth ({floor_mm2:.4g} mm² x {depth_mm:g} mm = "
        f"{expected:.4g} mm³): most of its depth is outside the part. Check "
        "the sketch plane and the cut direction, or use end_condition "
        "'UpToNext' or 'ThroughAll'."
    )
