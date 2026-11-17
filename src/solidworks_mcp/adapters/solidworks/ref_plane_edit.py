"""Move an offset reference plane: its distance and side (``IRefPlaneFeatureData``).

The Dipper build (SOLIDWORKS 2026 SP4.1) cut 0.2 mm off a block instead of
moving the plane its sketch stood on, because ``edit_feature`` didn't take
planes. A plane from ``create_reference_plane`` with an ``offset`` is an
``InsertRefPlane`` with a Distance constraint on its first reference. Its
definition (``IFeature.GetDefinition``, an ``IRefPlaneFeatureData``) holds:

* ``Distance``: the offset in metres, a plain property;
* ``Constraint(0)``: the first reference's ``swRefPlaneReferenceConstraints_e``
  bits, Distance (8) and, on the other side, OptionFlip (256): the bits
  ``create_reference_plane`` passes to ``InsertRefPlane``;
* ``ReversedReferenceDirection(0)``: the API help's flip of the first
  reference.

``Constraint`` and ``ReversedReferenceDirection`` take an index, which pywin32's
late binding can't pass to a property, so they go through ``IDispatch.Invoke``.
The side is read from ``Constraint(0)``, or from ``ReversedReferenceDirection(0)``
when that doesn't read, and written the way it was read. The edit itself is
``edit_feature``'s: ``AccessSelections``, the change, ``ModifyDefinition``,
``EditRebuild3``, then the values read back with the plane's origin
(``IRefPlane.Transform``), so the reply shows where the plane now is.

Measured on SOLIDWORKS 2026 SP4.1 (``tests/scripts/probe_dipper_edges.py``,
step ``plane``): an offset plane reads ``Distance`` 0.01, ``Type`` 5 and
``Constraint(0)`` 8 (distance), 264 once flipped, with
``ReversedReferenceDirection(0)`` True. An offset of 9.8 moved the plane
and its boss (box top 15 to 14.8 mm, 4500 to 4480 mm3), a flip put it at
y -9.8 and back, and Top Plane (``Type`` 10, ``Constraint(0)`` 0) is
refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .com_utils import attempt as _attempt
from .com_utils import flag as _flag
from .face_selection import plane_from_transform

#: ``IFeature.GetTypeName2`` of a reference plane.
REF_PLANE = "RefPlane"

#: The changes ``edit_feature`` makes to an offset plane.
PLANE_EDITS = frozenset({"offset", "flip"})

#: ``swRefPlaneReferenceConstraints_e`` (SolidWorks 2026 ``swconst.tlb``).
CONSTRAINT_DISTANCE = 8
CONSTRAINT_FLIP = 256

#: ``IDispatch::Invoke`` flags (``pythoncom.DISPATCH_PROPERTYGET`` / ``PUT``).
_PROPERTY_GET = 2
_PROPERTY_PUT = 4


def get_indexed(obj: Any, name: str, *index: Any) -> Any:
    """Read an indexed property (``name(index)``) through ``IDispatch.Invoke``.

    Raises:
        Exception: Whatever pywin32 raises when ``obj`` has no such member.
    """
    ole = obj._oleobj_
    dispid = ole.GetIDsOfNames(0, name)
    return ole.Invoke(dispid, 0, _PROPERTY_GET, True, *index)


def put_indexed(obj: Any, name: str, *index_and_value: Any) -> None:
    """Write an indexed property (``name(index) = value``) through ``IDispatch.Invoke``.

    pywin32 passes the last argument as the value (``DISPID_PROPERTYPUT``).

    Raises:
        Exception: Whatever pywin32 raises when ``obj`` has no such member.
    """
    ole = obj._oleobj_
    dispid = ole.GetIDsOfNames(0, name)
    ole.Invoke(dispid, 0, _PROPERTY_PUT, False, *index_and_value)


@dataclass(frozen=True)
class PlaneDefinition:
    """What an ``IRefPlaneFeatureData`` says of its first reference.

    Attributes:
        offset_mm: ``Distance`` in mm, or None when it doesn't read.
        constraint: ``Constraint(0)``'s bits, or None.
        reversed: ``ReversedReferenceDirection(0)``, or None.
    """

    offset_mm: float | None
    constraint: int | None
    reversed: bool | None

    @property
    def flip(self) -> bool | None:
        """The plane is on the other side: the OptionFlip bit, else ``reversed``."""
        if self.constraint is not None:
            return bool(self.constraint & CONSTRAINT_FLIP)
        return self.reversed

    def values(self) -> dict[str, Any]:
        """``offset`` (mm) and ``flip``, as ``edit_feature`` reports them."""
        return {"offset": self.offset_mm, "flip": self.flip}


def read_definition(adapter: Any, data: Any) -> PlaneDefinition:
    """Read an ``IRefPlaneFeatureData``'s distance and side; what doesn't read is None."""
    distance = _attempt(adapter, lambda: float(data.Distance))
    constraint = _attempt(adapter, lambda: get_indexed(data, "Constraint", 0))
    reversed_ = _attempt(
        adapter, lambda: get_indexed(data, "ReversedReferenceDirection", 0)
    )
    return PlaneDefinition(
        offset_mm=None if distance is None else round(distance * 1000, 6),
        constraint=int(constraint) if isinstance(constraint, int | float) else None,
        reversed=reversed_ if isinstance(reversed_, bool) else None,
    )


def not_offset_message(name: str) -> str:
    """The error for a plane that isn't offset from its reference (``invalid_input``)."""
    return (
        f"{name!r} is not an offset plane (its first reference is not a "
        "distance); edit_feature changes an offset plane's offset and flip. "
        "Change an angled plane's angle with set_dimension."
    )


def plane_problem(name: str, plane: PlaneDefinition) -> str | None:
    """Why ``plane`` can't be edited as an offset plane, or None.

    A plane whose ``Constraint(0)`` reads must carry the Distance bit; one
    whose constraint doesn't read must at least read a ``Distance``.
    """
    if plane.constraint is not None:
        if plane.constraint & CONSTRAINT_DISTANCE:
            return None
        return not_offset_message(name)
    if plane.offset_mm is None:
        return (
            f"SolidWorks gave no distance for {name!r}'s definition, so it can't "
            "be moved this way; change its offset with set_dimension (e.g. "
            f"'D1@{name}') instead."
        )
    return None


def write_definition(
    data: Any, plane: PlaneDefinition, changes: dict[str, Any]
) -> None:
    """Put ``offset`` (mm) and ``flip`` into a definition whose selections are accessed.

    The side is written the way it was read: the OptionFlip bit of
    ``Constraint(0)``, or ``ReversedReferenceDirection(0)``.

    Raises:
        RuntimeError: ``flip`` asked for, but neither of them read.
        Exception: SolidWorks refused a value.
    """
    if "offset" in changes:
        data.Distance = float(changes["offset"]) / 1000.0
    if "flip" not in changes:
        return
    flip = bool(changes["flip"])
    if plane.constraint is not None:
        bits = plane.constraint & ~CONSTRAINT_FLIP
        put_indexed(data, "Constraint", 0, bits | (CONSTRAINT_FLIP if flip else 0))
    elif plane.reversed is not None:
        put_indexed(data, "ReversedReferenceDirection", 0, flip)
    else:
        raise RuntimeError(
            "SolidWorks gave neither Constraint(0) nor ReversedReferenceDirection(0) "
            "for this plane, so its side can't be changed this way."
        )


def plane_origin_mm(adapter: Any, feature: Any) -> list[float] | None:
    """A point of the plane (``IRefPlane.Transform``'s origin), mm, or None."""
    _flag(adapter, feature, "GetSpecificFeature2")
    plane = _attempt(adapter, lambda: feature.GetSpecificFeature2())
    array = _attempt(adapter, lambda: list(plane.Transform.ArrayData))
    if not array or len(array) < 12:
        return None
    origin, _normal = plane_from_transform(array)
    return [round(c * 1000, 6) for c in origin]


def edit_plane(
    adapter: Any,
    name: str,
    feature: Any,
    changes: dict[str, Any],
    null_component: Any,
    find: Any,
) -> dict[str, Any]:
    """Change an offset plane's ``offset`` and ``flip``, and read them back.

    Args:
        adapter: A connected adapter with a ``currentModel``.
        name: The plane's tree name.
        feature: Its ``IFeature``, with ``GetDefinition`` and
            ``ModifyDefinition`` flagged.
        changes: ``offset`` (mm) and/or ``flip``.
        null_component: The null ``IComponent2`` the definition calls take.
        find: ``(adapter, name) -> IFeature``, to read the plane again after
            the rebuild.

    Returns:
        dict[str, Any]: ``feature``, ``type``, ``before`` and ``after``
        (``offset``, ``flip`` and ``origin_mm``, a point of the plane).

    Raises:
        ValueError: The plane isn't an offset plane.
        RuntimeError: SolidWorks gave no definition or refused the change.
    """
    model = adapter.currentModel
    data = _attempt(adapter, lambda: feature.GetDefinition())
    if data is None:
        raise RuntimeError(
            f"SolidWorks gave no definition for {name!r}, so it can't be edited "
            f"this way; change its offset with set_dimension (e.g. 'D1@{name}')."
        )
    _flag(adapter, data, "AccessSelections", "ReleaseSelectionAccess")
    plane = read_definition(adapter, data)
    problem = plane_problem(name, plane)
    if problem:
        raise ValueError(problem)
    before = {**plane.values(), "origin_mm": plane_origin_mm(adapter, feature)}
    if not _attempt(
        adapter, lambda: data.AccessSelections(model, null_component), False
    ):
        raise RuntimeError(
            f"SolidWorks would not open {name!r}'s definition for editing."
        )
    try:
        write_definition(data, plane, changes)
        changed = feature.ModifyDefinition(data, model, null_component)
    except Exception:
        _attempt(
            adapter, lambda: adapter._get_attr_or_call(data, "ReleaseSelectionAccess")
        )
        raise
    if not changed:
        _attempt(
            adapter, lambda: adapter._get_attr_or_call(data, "ReleaseSelectionAccess")
        )
        raise RuntimeError(
            f"SolidWorks refused the change to {name!r} (ModifyDefinition "
            "returned False); the plane is where it was."
        )
    _attempt(adapter, lambda: model.EditRebuild3())
    again = _attempt(adapter, lambda: find(adapter, name)) or feature
    after_data = _attempt(adapter, lambda: again.GetDefinition())
    after = (
        read_definition(adapter, after_data).values()
        if after_data is not None
        else {k: v for k, v in changes.items() if k in PLANE_EDITS}
    )
    after["origin_mm"] = plane_origin_mm(adapter, again)
    return {"feature": name, "type": REF_PLANE, "before": before, "after": after}
