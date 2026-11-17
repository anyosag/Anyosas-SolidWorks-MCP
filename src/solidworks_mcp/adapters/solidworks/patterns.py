"""Linear patterns of features or bodies (``pattern_linear``).

``IFeatureManager.FeatureLinearPattern5`` reads its directions and what to
repeat from the selection, by mark (API help Remarks):

* Direction 1 at mark 1, Direction 2 at mark 2;
* the features to pattern at mark 4, or the solid bodies at mark 256.

A direction is a straight edge, a reference axis, or a plane (the pattern
then follows its normal). ``DName1`` and ``DName2`` name a dimension that
defines a direction; with the direction selected they are left empty, as in
the help's FeatureLinearPattern4 example. Spacings are metres.

The selection order is ``pattern_circular``'s, which runs on SolidWorks 2026:
the directions first, then what to pattern. A direction given as a point on
an edge is picked after one ``ForceRebuild3`` (pitfall #13), made before
anything is selected because a rebuild clears the selection (pitfall #23).

From the Dipper build (SOLIDWORKS 2026 SP4.1): 8 identical slot cuts and 16
drafted lead-in cuts in rows, with only a circular feature pattern to hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .. import sw_type_info
from ..base import AdapterResult, AdapterResultStatus
from .com_utils import strip_op_prefix

#: Selection marks ``FeatureLinearPattern5`` reads (API help Remarks).
MARK_DIRECTION_1 = 1
MARK_DIRECTION_2 = 2
MARK_FEATURES = 4
MARK_BODIES = 256

#: The default plane whose normal is each model axis: the Right Plane's is
#: +X, the Top Plane's +Y and the Front Plane's +Z.
_AXIS_PLANES = {"x": "right", "y": "top", "z": "front"}

_PRINCIPAL = re.compile(r"^([+-]?)([xyz])$")
_EDGE_PREFIX = "edge:"

#: What ``direction`` may be, for the errors.
DIRECTION_FORMS = (
    "'x', 'y' or 'z' (with '-' for the negative way), a reference axis or "
    "plane by name (the pattern follows the axis, or the plane's normal), or "
    "'x,y,z' in mm on a straight edge"
)


@dataclass(frozen=True)
class Direction:
    """A pattern direction as given.

    Attributes:
        spec: The text it was given as.
        kind: ``"principal"`` (a model axis), ``"edge"`` (a point on an edge)
            or ``"named"`` (an axis, plane or edge by name).
        flip: The negative way along a model axis (``"-x"``).
        plane_key: For a model axis, the default plane whose normal it is.
        point_mm: For an edge, the point on it in mm.
        name: For a named entity, its name.
    """

    spec: str
    kind: str
    flip: bool = False
    plane_key: str | None = None
    point_mm: tuple[float, float, float] | None = None
    name: str | None = None


def parse_direction(spec: str) -> Direction:
    """Read a direction: a model axis, a point on an edge, or a name.

    Args:
        spec: ``"x"``, ``"-y"``, ``"+z"``; ``"12,0,-5"`` or ``"edge:12,0,-5"``
            (mm on a straight edge); or an axis or plane name (``"Axis1"``,
            ``"Top Plane"``).

    Returns:
        Direction: What it names.

    Raises:
        ValueError: For an empty direction, or an ``edge:`` point that isn't
            three numbers.
    """
    text = str(spec or "").strip()
    if not text:
        raise ValueError(f"A pattern direction is required: {DIRECTION_FORMS}.")
    principal = _PRINCIPAL.match(text.lower().replace(" ", ""))
    if principal:
        sign, axis = principal.groups()
        return Direction(
            text, "principal", flip=sign == "-", plane_key=_AXIS_PLANES[axis]
        )
    is_edge = text.lower().startswith(_EDGE_PREFIX)
    body = text[len(_EDGE_PREFIX) :] if is_edge else text
    parts = body.split(",")
    if len(parts) == 3:
        try:
            point = (float(parts[0]), float(parts[1]), float(parts[2]))
        except ValueError:
            point = None
        if point is not None:
            return Direction(text, "edge", point_mm=point)
    if is_edge:
        raise ValueError(
            f"Direction {text!r}: an edge is given as 'x,y,z' in mm on the edge."
        )
    return Direction(text, "named", name=text)


def _select_direction(
    adapter: Any, direction: Direction, mark: int, append: bool, picker: Any
) -> str:
    """Select one direction under ``mark``; say what was selected.

    Args:
        adapter: A connected adapter with a part open.
        direction: The direction.
        mark: :data:`MARK_DIRECTION_1` or :data:`MARK_DIRECTION_2`.
        append: Add to the selection (the second direction).
        picker: The ``_BodyEntityPicker`` for an edge point.

    Returns:
        str: What the pattern follows, for the reply.

    Raises:
        Exception: Nothing could be selected for it.
        LookupError: No edge passes through an edge point.
    """
    from .features import _null_callout, _select_edge_by_coord, _select_named_feature
    from .sketch import plane_candidates_for

    if direction.kind == "principal":
        _key, names = plane_candidates_for(str(direction.plane_key))
        for name in names:
            if _select_named_feature(adapter, name, mark, append):
                return f"normal of {name}"
        raise Exception(
            f"Failed to select the {direction.plane_key} plane for direction "
            f"{direction.spec!r}; use an axis (create_axis) or a plane name instead."
        )
    if direction.kind == "edge":
        x, y, z = (c / 1000.0 for c in direction.point_mm or (0.0, 0.0, 0.0))
        if _select_edge_by_coord(adapter, x, y, z, append, mark, "EDGE", picker):
            return f"edge at {list(direction.point_mm or ())} mm"
        raise Exception(
            f"Failed to select the edge at {list(direction.point_mm or ())} mm "
            f"for direction {direction.spec!r}."
        )
    name = str(direction.name)
    if _select_named_feature(adapter, name, mark, append):
        return name
    callout = _null_callout()
    for entity_type in ("AXIS", "PLANE", "EDGE"):
        selected = adapter._attempt(
            lambda t=entity_type: adapter.currentModel.Extension.SelectByID2(
                name, t, 0.0, 0.0, 0.0, append, mark, callout, 0
            ),
            default=False,
        )
        if selected:
            return name
    raise Exception(
        f"Failed to select '{name}' as a pattern direction. Give {DIRECTION_FORMS}."
    )


def _select_seed(adapter: Any, name: str, bodies: bool) -> bool:
    """Append a feature (mark 4) or a solid body (mark 256) to the selection."""
    from .features import _null_callout, _select_named_feature

    entity_type, mark = (
        ("SOLIDBODY", MARK_BODIES) if bodies else ("BODYFEATURE", MARK_FEATURES)
    )
    callout = _null_callout()
    selected = bool(
        adapter._attempt(
            lambda: adapter.currentModel.Extension.SelectByID2(
                name, entity_type, 0.0, 0.0, 0.0, True, mark, callout, 0
            ),
            default=False,
        )
    )
    if not selected and not bodies:
        selected = _select_named_feature(adapter, name, mark, append=True)
    return selected


def linear_pattern_refusal(
    features: list[str],
    count: int,
    spacing: float,
    direction2: str | None,
    count2: int,
    spacing2: float,
) -> str | None:
    """Why a linear pattern can't be made as asked, or None when it can.

    Shared by the live and mock adapters so both refuse the same requests.

    Returns:
        str | None: The refusal, or None.
    """
    if not [n for n in features or [] if n]:
        return "pattern_linear requires at least one feature (or body) name"
    if int(count) < 2:
        return (
            f"pattern_linear needs a count of at least 2 (got {count}); a "
            "pattern of one is just the original"
        )
    if not float(spacing) > 0:
        return f"pattern_linear needs a positive spacing in mm (got {spacing})"
    if direction2 is not None:
        if int(count2) < 2:
            return f"direction2 needs a count2 of at least 2 (got {count2})"
        if not float(spacing2) > 0:
            return f"direction2 needs a positive spacing2 in mm (got {spacing2})"
    return None


def pattern_linear(
    adapter: Any,
    features: list[str],
    direction: str,
    count: int,
    spacing: float,
    direction2: str | None = None,
    count2: int = 1,
    spacing2: float = 0.0,
    reverse_direction: bool = False,
    reverse_direction2: bool = False,
    bodies: bool = False,
) -> AdapterResult[dict[str, Any]]:
    """Select the directions and the seeds, then call FeatureLinearPattern5.

    Whether any instance landed on the part is checked by the tool, from the
    volume before and after.

    Args:
        adapter: A connected ``PyWin32Adapter``.
        features: Features to pattern, or bodies with ``bodies``.
        direction: Direction 1 (:func:`parse_direction`).
        count: Instances along it, the original included.
        spacing: Millimetres between neighbours along it.
        direction2: Direction 2, or None for a one-way pattern.
        count2: Instances along Direction 2.
        spacing2: Millimetres between neighbours along it.
        reverse_direction: Go the other way along Direction 1.
        reverse_direction2: Go the other way along Direction 2.
        bodies: Pattern solid bodies (mark 256) instead of features (mark 4).

    Returns:
        AdapterResult[dict[str, Any]]: ``name``, ``patterned``, ``bodies``,
        ``direction`` and ``direction2`` (each ``spec``, ``follows``,
        ``count``, ``spacing_mm`` and ``flipped``, the ``FlipDir`` sent) and
        ``instances``.
    """
    from .features import _BodyEntityPicker

    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")
    refusal = linear_pattern_refusal(
        features, count, spacing, direction2, count2, spacing2
    )
    if refusal is None:
        try:
            first = parse_direction(direction)
            second = parse_direction(direction2) if direction2 is not None else None
        except ValueError as exc:
            refusal = str(exc)
    if refusal is not None:
        return AdapterResult(
            status=AdapterResultStatus.ERROR, error=f"Invalid request: {refusal}"
        )
    names = [n for n in features if n]
    flip1 = first.flip != bool(reverse_direction)
    flip2 = bool(second and second.flip) != bool(reverse_direction2)
    number2, gap2 = (int(count2), float(spacing2)) if second else (1, 0.0)

    def _pattern_operation() -> dict[str, Any]:
        model = adapter.currentModel
        picker = _BodyEntityPicker(adapter)
        if "edge" in (first.kind, second.kind if second else None):
            picker.rebuild()  # before anything is selected: it clears it
        adapter._attempt(lambda: model.ClearSelection2(True), default=None)
        follows = _select_direction(adapter, first, MARK_DIRECTION_1, False, picker)
        follows2 = (
            _select_direction(adapter, second, MARK_DIRECTION_2, True, picker)
            if second
            else None
        )
        for name in names:
            if not _select_seed(adapter, name, bodies):
                what = "body" if bodies else "feature"
                where = "list_bodies" if bodies else "list_features"
                raise Exception(
                    f"Failed to select {what} to pattern: {name} ({where} names them)"
                )

        feature_manager = sw_type_info.flagged(model.FeatureManager, "IFeatureManager")
        feature, error = adapter._attempt_with_error(
            lambda: feature_manager.FeatureLinearPattern5(
                int(count),  # Num1
                float(spacing) / 1000.0,  # Spacing1 (m)
                number2,  # Num2
                gap2 / 1000.0,  # Spacing2 (m)
                flip1,  # FlipDir1
                flip2,  # FlipDir2
                "",  # DName1: the direction is selected
                "",  # DName2
                False,  # GeometryPattern: each instance solved as a feature
                False,  # VaryInstance
                False,  # HasOffset1
                False,  # HasOffset2
                False,  # CtrlByNum1
                False,  # CtrlByNum2
                False,  # FromCentroid1
                False,  # FromCentroid2
                False,  # RevOffset1
                False,  # RevOffset2
                0.0,  # Offset1
                0.0,  # Offset2
                False,  # D2PatternSeedOnly
                False,  # SyncSubAssemblies
            )
        )
        if not feature:
            reason = f": {error}" if error is not None else ""
            raise Exception(
                f"FeatureLinearPattern5 returned nothing (seeds={names}, "
                f"direction={first.spec!r}"
                + (f", direction2={second.spec!r}" if second else "")
                + f"){reason}"
            )
        pattern_name = adapter._attempt(
            lambda: adapter._get_attr_or_call(feature, "Name"), default=None
        )
        return {
            "name": str(pattern_name) if pattern_name else None,
            "patterned": names,
            "bodies": bool(bodies),
            "direction": {
                "spec": first.spec,
                "follows": follows,
                "count": int(count),
                "spacing_mm": float(spacing),
                "flipped": flip1,
            },
            "direction2": (
                {
                    "spec": second.spec,
                    "follows": follows2,
                    "count": number2,
                    "spacing_mm": gap2,
                    "flipped": flip2,
                }
                if second
                else None
            ),
            "instances": int(count) * number2,
        }

    result: AdapterResult[dict[str, Any]] = adapter._handle_com_operation(
        "pattern_linear", _pattern_operation
    )
    if not result.is_success:
        result.error = strip_op_prefix(result.error, "pattern_linear")
    return result
