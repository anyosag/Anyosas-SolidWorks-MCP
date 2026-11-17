"""The volume of a part on each side of a plane, without changing the part.

"The Dipper" build (SOLIDWORKS 2026 SP4.1) needed fill-line volumes: it cut
the part at the line, measured, and deleted the cut, twice per revision.
``volume_by_plane`` does it on temporary bodies instead:

* a big cube on each side of the plane, from ``IModeler.CreateBodyFromBox3``
  (centre, axis = the plane's normal, three equal sides). Whether that centre
  is the box's centre or its base's, the cube is moved by the difference
  between where its centre of mass should be and where it is
  (``GetMassProperties``, ``ApplyTransform``), so one face lies on the plane;
* each body, copied, intersected with each cube (``bodies.intersect_mm3``:
  ``Operations2`` on copies, which consumes only the copies);
* with ``enclosed``, each cube less every body (``Operations2`` cut) leaves
  the space outside the part and, apart from it, each pocket the part
  encloses on that side, open only at the plane: what a cup holds filled to
  that line. A pocket counts when its box lies inside the bodies' box and its
  far point toward the plane (``IBody2.GetExtremePoint``) is on the plane, so
  a sealed void inside a wall doesn't.

"Above" is the side the plane's normal points to: +Y for Top Plane.

Mass: ``density_g_cm3`` when given, else the part's own density from
``IModelDocExtension.CreateMassProperty`` (``Density``, or ``Mass / Volume``).
The ``tests/scripts/probe_dipper_analysis.py`` probe runs these calls on
scratch parts.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from typing import Any

from . import limits
from .bodies import (
    BODY_CUT,
    NO_INTERSECT,
    _box_m,
    _mass_m,
    _math_utility,
    _wrapped,
    boolean,
    box_inside,
    find_bodies,
    intersect_mm3,
    solid_bodies,
    temp_copy,
    translation_data,
    volume_mm3,
)
from .clearance import InvalidRequest, box_distance
from .com_utils import attempt as _attempt
from .com_utils import double_array as _double_array
from .com_utils import flag as _flag
from .placement import unit_vector

#: A body's two sides may differ from its volume by this share before a warning.
CHECK_SHARE = 1e-4
#: How close (m) a pocket's far point must come to the plane to be open there.
_ON_PLANE_M = 1e-6
#: How far (m) past the bodies' box a pocket's box may reach.
_INSIDE_PAD_M = 1e-6
#: Margin (m) added to the cubes' size.
_MARGIN_M = 0.01


# ---- pure pieces -------------------------------------------------------------------


def box_volume_below(
    box: Sequence[float], normal: Sequence[float], offset: float
) -> float:
    """The exact volume of ``box`` where ``normal . x <= offset``.

    The box is ``[x1, y1, z1, x2, y2, z2]``. Each axis the normal runs
    against is mirrored so every component is positive, axes it doesn't touch
    only scale the result, and the rest is the inclusion-exclusion formula
    for a simplex cut from a box: ``sum (-1)^|S| max(0, c - n . L_S)^d / (d!
    prod n)`` over the corners ``S``.
    """
    lengths = [max(0.0, float(box[k + 3]) - float(box[k])) for k in range(3)]
    c = float(offset) - sum(float(normal[k]) * float(box[k]) for k in range(3))
    scale, n, size = 1.0, [], []
    for k in range(3):
        nk = float(normal[k])
        if abs(nk) < 1e-15:
            scale *= lengths[k]
            continue
        if nk < 0:
            c -= nk * lengths[k]
            nk = -nk
        n.append(nk)
        size.append(lengths[k])
    d = len(n)
    if d == 0:
        return scale if c >= 0 else 0.0
    total = 0.0
    for corner in itertools.product((0, 1), repeat=d):
        reach = c - sum(n[i] * size[i] for i in range(d) if corner[i])
        if reach > 0:
            total += (-1) ** sum(corner) * reach**d
    return scale * total / (math.factorial(d) * math.prod(n))


def box_corners(box: Sequence[float]) -> list[list[float]]:
    """A box's eight corners."""
    return [
        [float(box[a + 3]) if (i >> a) & 1 else float(box[a]) for a in range(3)]
        for i in range(8)
    ]


def cube_size_m(
    box: Sequence[float], origin: Sequence[float], normal: Sequence[float]
) -> tuple[float, list[float]]:
    """The cubes' side, and the point on the plane their faces centre on.

    The point is the box centre's foot on the plane, so the cubes reach every
    corner of the box sideways; the side is twice the farthest corner's
    distance from it, plus :data:`_MARGIN_M`.
    """
    centre = [(float(box[k]) + float(box[k + 3])) / 2.0 for k in range(3)]
    height = sum((centre[k] - float(origin[k])) * normal[k] for k in range(3))
    foot = [centre[k] - height * normal[k] for k in range(3)]
    reach = max(math.dist(corner, foot) for corner in box_corners(box))
    return 2.0 * reach + _MARGIN_M, foot


def plane_problem(
    plane: str | None, point_mm: Any | None, normal: Any | None
) -> str | None:
    """Why a request doesn't name exactly one plane, or None."""
    if plane and (point_mm is not None or normal is not None):
        return "give plane, or point_mm and normal, not both."
    if not plane and (point_mm is None or normal is None):
        return "give plane (e.g. 'Top Plane' or 'Fill line'), or point_mm and normal."
    return None


def check_plane(
    plane: str | None, point_mm: Sequence[float] | None, normal: Sequence[float] | None
) -> None:
    """Refuse what :func:`plane_problem` finds.

    Raises:
        InvalidRequest: Saying what to give.
    """
    problem = plane_problem(plane, point_mm, normal)
    if problem:
        raise InvalidRequest(problem)


def mass_g(volume: float | None, density_g_cm3: float | None) -> float | None:
    """The mass (g) of ``volume`` mm3 at ``density_g_cm3``."""
    if volume is None or density_g_cm3 is None:
        return None
    return round(volume * density_g_cm3 / 1000.0, 6) + 0.0


# ---- COM -------------------------------------------------------------------------


def named_plane(
    adapter: Any, model: Any, name: str
) -> tuple[list[float], list[float], str]:
    """A reference plane's point and normal (metres), and the name that resolved.

    ``Top``, ``Front`` and ``Right`` find the default planes in any language
    (``sketch.plane_candidates_for``). The point and normal come from
    ``IRefPlane.Transform``: its translation and its third row.

    Raises:
        LookupError: No plane has the name, or it isn't a plane, or its
            geometry can't be read.
    """
    from .face_selection import plane_from_transform
    from .sketch import plane_candidates_for

    _flag(adapter, model, "FeatureByName")
    for candidate in plane_candidates_for(name)[1]:
        feature = _attempt(adapter, lambda c=candidate: model.FeatureByName(c))
        if feature is None:
            continue
        _flag(adapter, feature, "GetTypeName2", "GetSpecificFeature2")
        kind = _attempt(adapter, lambda f=feature: f.GetTypeName2())
        if kind != "RefPlane":
            raise LookupError(f"{candidate!r} is a {kind or 'feature'}, not a plane.")
        ref = _attempt(adapter, lambda f=feature: f.GetSpecificFeature2())
        data = _attempt(adapter, lambda r=ref: list(r.Transform.ArrayData))
        if not data or len(data) < 12:
            raise LookupError(f"The geometry of plane {candidate!r} could not be read.")
        origin, normal = plane_from_transform(data)
        return list(origin), unit_vector(normal), candidate
    raise LookupError(
        f"Plane not found: {name}. Use a plane's name from list_features, such as "
        "Top Plane, or give point_mm and normal."
    )


def _modeler(adapter: Any) -> Any:
    """``ISldWorks.GetModeler()`` with ``CreateBodyFromBox3`` flagged.

    Raises:
        RuntimeError: SolidWorks gave no modeler.
    """
    app = adapter.swApp
    _flag(adapter, app, "GetModeler")
    modeler = _attempt(adapter, lambda: app.GetModeler())
    if modeler is None:
        raise RuntimeError("ISldWorks.GetModeler gave nothing.")
    _flag(adapter, modeler, "CreateBodyFromBox3")
    return modeler


def half_space(
    adapter: Any,
    modeler: Any,
    utility: Any,
    foot: Sequence[float],
    normal: Sequence[float],
    side: float,
    size: float,
) -> Any:
    """A temporary cube of side ``size`` on one side of the plane, a face on it.

    Raises:
        RuntimeError: SolidWorks made no box, or it couldn't be placed.
    """
    centre = [float(foot[k]) + side * size / 2.0 * float(normal[k]) for k in range(3)]
    raw = _attempt(
        adapter,
        lambda: modeler.CreateBodyFromBox3(
            _double_array([*centre, *map(float, normal), size, size, size])
        ),
    )
    if raw is None:
        raise RuntimeError("IModeler.CreateBodyFromBox3 made no box.")
    cube = _wrapped(adapter, raw, "Copy", "ApplyTransform", "GetMassProperties")
    props = _mass_m(adapter, cube)
    if len(props) < 3:
        raise RuntimeError("The cube's centre could not be read (GetMassProperties).")
    shift = [centre[k] - props[k] for k in range(3)]
    if math.sqrt(sum(s * s for s in shift)) > 1e-9:
        xform = _attempt(
            adapter,
            lambda: utility.CreateTransform(_double_array(translation_data(shift))),
        )
        if xform is None or not _attempt(
            adapter, lambda: cube.ApplyTransform(xform), False
        ):
            raise RuntimeError("The cube could not be moved onto the plane.")
    return cube


def _extreme(adapter: Any, body: Any, direction: Sequence[float]) -> list[float] | None:
    """``IBody2.GetExtremePoint`` along ``direction``, metres, or None."""
    from .io import _byref_double

    _flag(adapter, body, "GetExtremePoint")
    out = (_byref_double(), _byref_double(), _byref_double())
    found = _attempt(adapter, lambda: body.GetExtremePoint(*direction, *out))
    values = [getattr(v, "value", None) for v in out]
    if not found or any(not isinstance(v, (int, float)) for v in values):
        return None
    return [float(v) for v in values]


def enclosed_mm3(
    adapter: Any,
    cube: Any,
    bodies: Sequence[tuple[str, Any]],
    bodies_box: Sequence[float],
    origin: Sequence[float],
    normal: Sequence[float],
    side: float,
) -> tuple[float, int]:
    """The volume the bodies enclose on one side, open only at the plane.

    The cube less every body (copies, ``Operations2`` cut) leaves the outside
    and the pockets apart; a piece is a pocket when its box lies within
    ``bodies_box`` and its farthest point toward the plane is on it.

    Returns:
        tuple[float, int]: The volume (mm3) and the number of pockets.

    Raises:
        RuntimeError: A cut failed.
    """
    pieces = [temp_copy(adapter, cube)]
    for name, body in bodies:
        body_box = _box_m(adapter, body)
        kept = []
        for piece in pieces:
            limits.check("cutting the bodies from one side of the plane")
            piece_box = _box_m(adapter, piece)
            if piece_box and body_box and box_distance(piece_box, body_box) > 0:
                kept.append(piece)
                continue
            cut, code, error = boolean(
                adapter, temp_copy(adapter, piece), temp_copy(adapter, body), BODY_CUT
            )
            if error is not None:
                raise RuntimeError(
                    f"IBody2.Operations2 (cut) failed on {name}: {error}"
                )
            kept.extend(cut if cut or code != NO_INTERSECT else [piece])
        pieces = kept
    toward = [-side * float(v) for v in normal]
    total, count = 0.0, 0
    for piece in pieces:
        box = _box_m(adapter, piece)
        if box is None or not box_inside(box, bodies_box, _INSIDE_PAD_M):
            continue
        far = _extreme(adapter, piece, toward)
        if far is None:
            continue
        height = sum((far[k] - float(origin[k])) * normal[k] for k in range(3))
        if abs(height) > _ON_PLANE_M:
            continue
        total += volume_mm3(adapter, piece) or 0.0
        count += 1
    return round(total, 6) + 0.0, count


def part_density_g_cm3(adapter: Any, model: Any) -> float | None:
    """The density SolidWorks uses for the part (g/cm3), or None.

    ``IModelDocExtension.CreateMassProperty``'s ``Density`` (kg/m3), else its
    ``Mass / Volume``. Empty while something is selected (pitfall #33).
    """
    extension = _attempt(adapter, lambda: model.Extension)
    _flag(adapter, extension, "CreateMassProperty")
    props = _attempt(adapter, lambda: extension.CreateMassProperty())
    if props is None:
        return None
    density = _attempt(adapter, lambda: props.Density)
    if not isinstance(density, (int, float)) or density <= 0:
        mass = _attempt(adapter, lambda: props.Mass)
        volume = _attempt(adapter, lambda: props.Volume)
        if not all(isinstance(v, (int, float)) and v > 0 for v in (mass, volume)):
            return None
        density = float(mass) / float(volume)
    return round(float(density) / 1000.0, 9) + 0.0


def volume_by_plane(
    adapter: Any,
    plane: str | None = None,
    point_mm: Sequence[float] | None = None,
    normal: Sequence[float] | None = None,
    bodies: Sequence[str] | None = None,
    enclosed: bool = False,
    density_g_cm3: float | None = None,
) -> dict[str, Any]:
    """The active part's volume (and mass) above and below a plane.

    Returns:
        dict[str, Any]: ``plane`` (``name``, ``origin_mm``, ``normal``);
        ``above`` and ``below`` (``volume_mm3``, ``mass_g`` and, with
        ``enclosed``, ``enclosed_mm3`` and ``pockets``); ``bodies`` (each
        one's ``volume_mm3``, ``above_mm3``, ``below_mm3``); ``total_volume_mm3``;
        ``check_mm3`` (above + below - total, about 0); ``density_g_cm3`` and
        ``density_source`` (``input``, ``solidworks`` or None).

    Raises:
        InvalidRequest: Not exactly one plane, or a body named twice.
        LookupError: The plane or a body isn't in the part.
        RuntimeError: The part has no solid body, or a temporary body failed.
    """
    check_plane(plane, point_mm, normal)
    model = adapter.currentModel
    if plane:
        origin, n, name = named_plane(adapter, model, plane)
    else:
        origin = [float(c) / 1000.0 for c in point_mm or ()]
        n, name = unit_vector(normal or ()), None
    if bodies:
        if len(set(bodies)) != len(bodies):
            raise InvalidRequest("bodies names a body twice.")
        found = find_bodies(adapter, model, list(bodies))
        chosen = [(b, found[b]) for b in bodies]
    else:
        chosen = solid_bodies(adapter, model)
    if not chosen:
        raise RuntimeError("The part has no solid body to measure.")
    boxes = [box for _n, body in chosen if (box := _box_m(adapter, body)) is not None]
    if len(boxes) != len(chosen):
        raise RuntimeError("A body's box could not be read (GetBodyBox).")
    union = [min(b[k] for b in boxes) for k in range(3)] + [
        max(b[k + 3] for b in boxes) for k in range(3)
    ]
    size, foot = cube_size_m(union, origin, n)
    modeler, utility = _modeler(adapter), _math_utility(adapter)
    cubes = {
        side: half_space(adapter, modeler, utility, foot, n, sign, size)
        for side, sign in (("above", 1.0), ("below", -1.0))
    }
    rows = []
    for body_name, body in chosen:
        limits.check(f"measuring {body_name} on each side of the plane")
        rows.append(
            {
                "name": body_name,
                "volume_mm3": volume_mm3(adapter, body),
                "above_mm3": intersect_mm3(adapter, body, cubes["above"]),
                "below_mm3": intersect_mm3(adapter, body, cubes["below"]),
            }
        )
    total = round(sum(r["volume_mm3"] or 0.0 for r in rows), 6) + 0.0
    density, source = density_g_cm3, "input" if density_g_cm3 is not None else None
    if density is None:
        density = part_density_g_cm3(adapter, model)
        source = "solidworks" if density is not None else None
    sides: dict[str, dict[str, Any]] = {}
    for side, sign in (("above", 1.0), ("below", -1.0)):
        volume = round(sum(r[f"{side}_mm3"] for r in rows), 6) + 0.0
        sides[side] = {"volume_mm3": volume, "mass_g": mass_g(volume, density)}
        if enclosed:
            pocket, count = enclosed_mm3(
                adapter, cubes[side], chosen, union, origin, n, sign
            )
            sides[side].update(
                enclosed_mm3=pocket,
                enclosed_ml=round(pocket / 1000.0, 6) + 0.0,
                pockets=count,
            )
    check = sides["above"]["volume_mm3"] + sides["below"]["volume_mm3"] - total
    return {
        "plane": {
            "name": name,
            "origin_mm": [round(v * 1000.0, 6) + 0.0 for v in origin],
            "normal": [round(v, 9) + 0.0 for v in n],
        },
        **sides,
        "bodies": rows,
        "total_volume_mm3": total,
        "check_mm3": round(check, 6) + 0.0,
        "check_ok": abs(check) <= max(1e-3, CHECK_SHARE * total),
        "density_g_cm3": density,
        "density_source": source,
    }
