"""Interference in an assembly, found by intersecting its components' bodies (``check_interference``).

``IAssemblyDoc.InterferenceDetectionManager`` puts the assembly into
SolidWorks' interference display until ``Done()``, and a user saw
Interference Detection come up on screen at each call; sessions make many in
a row (a thread-phase sweep). ``ToolsCheckInterference2``, the one call
without that UI, reports only through ByRef out-parameters that late binding
can't read (COM pitfalls #13 and #14). So the overlaps are computed in the
geometry kernel, on temporary bodies, and nothing is shown:

* the part components from the root down: ``GetComponents(True)``, then
  ``IComponent2.GetChildren`` into each sub-assembly, leaving out a
  suppressed component (``GetSuppression2``) and all below it, and a hidden
  one (``Visible``) when hidden components are ignored, as ``assembly_box``
  walks them;
* each part component's solid bodies, ``IComponent2.GetBodies2(swSolidBody)``,
  in its part's coordinates (``clearance``), a hidden body left out like a
  hidden component; each body's box (``GetBodyBox``) placed in the assembly
  by the component's ``Transform2`` (relative to the root assembly, as the
  API help says);
* for each pair of components whose placed boxes overlap, each body of one
  against each body of the other whose boxes overlap, so components apart
  cost no COM call. A body that takes part is copied once into the assembly
  (``IBody2.Copy``, then ``ApplyTransform`` of its ``Transform2``, as
  ``rotation_clearance`` places its copies), and that copy is copied again
  for every intersection, since ``Operations2`` consumes both bodies;
* ``Operations2(SWBODYINTERSECT)``: each piece of more than
  ``bodies.OVERLAP_MM3`` is one interference, as the manager gives one
  ``IInterference`` per region. Two identical bodies
  (``swBodyOperationSameToolAndTarget``) share the whole body;
* with ``include_multibody``, each component's own bodies against each other.

Boxes that only touch can't hold a shared volume, so they aren't intersected,
and touching faces aren't an interference, as with the manager's
``TreatCoincidenceAsInterference`` off. With a ``components`` list, only the
pairs ``io.interferences_among`` would keep are computed, and only the
components in them are read.

Whatever the kernel can't answer for certain raises :class:`Unreadable`, and
``io.check_interference`` runs SolidWorks' Interference Detection instead: a
lightweight component (its bodies aren't loaded), components, bodies or a
placement that don't read, a copy that can't be placed, an intersection that
raises or gives an error code other than "no intersection", or a piece whose
volume doesn't read.

:func:`box_interferences` is the same check on boxes: the mock adapter's
geometry.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import limits
from .assembly_box import MAX_COMPONENTS
from .bodies import (
    BODY_INTERSECT,
    NO_ERROR,
    NO_INTERSECT,
    OVERLAP_MM3,
    SAME_TOOL_AND_TARGET,
    _box_m,
    _math_utility,
    _wrapped,
    boolean,
    box_overlap,
    overlap_volume,
    temp_copy,
    union_box,
    volume_mm3,
)
from .clearance import LIGHTWEIGHT, SOLID_BODY, SUPPRESSED, box_distance, transform_box
from .com_utils import as_list
from .com_utils import attempt as _attempt
from .component_visibility import is_visible
from .io import _as_com
from .placement import _transform_data
from .rotation_clearance import _placed_copy

#: ``method`` of a reply computed here, and of one from SolidWorks' manager.
BODY_INTERSECTION = "body_intersection"
INTERFERENCE_DETECTION = "interference_detection"
#: Why ``coincident`` goes to SolidWorks' own detection.
COINCIDENT_REASON = "only it counts touching faces as interference"
#: ``swBodyOperationError_e`` codes of an intersection that shares no volume:
#: no error, no intersection, disjoint bodies, an empty result. Any other
#: code (a boolean failure, partial coincidence, ...) is not trusted.
NO_OVERLAP_CODES = frozenset({NO_ERROR, NO_INTERSECT, 5, 6})
#: What :func:`body_interferences` checks between COM calls (``limits.check``).
DOING = "checking interference"
#: A placement that moves nothing: the rotation, translation and scale.
_IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)

Box = list[float]


class Unreadable(Exception):
    """The kernel can't answer for certain; the message says why (the reply's ``note``)."""


def fallback_note(reason: str) -> str:
    """The ``note`` of a reply from SolidWorks' Interference Detection."""
    return (
        "SolidWorks' Interference Detection was used, so it may have shown "
        f"briefly: {reason}."
    )


def asked_pair(a: str, b: str, asked: Collection[str]) -> bool:
    """Whether ``io.interferences_among`` keeps an interference of ``a`` and ``b``.

    No list keeps every pair; one component, the pairs it is in; two or
    more, the pairs among them. A component's own bodies are the pair
    ``(a, a)``.
    """
    if not asked:
        return True
    names = {a, b}
    wanted = set(asked)
    return bool(wanted & names) if len(wanted) == 1 else names <= wanted


def found_reply(
    details: list[dict[str, Any]], asked: Collection[str], coincident: bool = False
) -> dict[str, Any]:
    """``check_interference``'s reply for interferences found here (or on boxes).

    The keys the manager's reply has, and ``method`` ``body_intersection``.
    """
    return {
        "interference_found": bool(details),
        "interference_count": len(details),
        "interferences": details,
        "coincident_treated_as_interference": coincident,
        "scope": sorted(asked) if asked else "whole assembly",
        # SolidWorks has no tolerance for this either; said, not dropped.
        "tolerance_applied": None,
        "method": BODY_INTERSECTION,
    }


def _entry(a: str, b: str, volume: float) -> dict[str, Any]:
    """One interference, as ``io._interference_details`` gives it."""
    return {"components": [a, b], "component_count": 2, "volume_mm3": volume}


def box_interferences(
    boxes_mm: Mapping[str, Sequence[float]],
    asked: Collection[str] = (),
    touching: bool = False,
) -> list[dict[str, Any]]:
    """The interferences of components that each stand in for their box (mm).

    Each pair whose boxes share more than ``OVERLAP_MM3`` is one interference
    of that volume. With ``touching`` (the mock's ``coincident``), boxes that
    only touch are one too, of 0 mm3.

    Args:
        boxes_mm: Each component's box in the assembly, by name, in order.
        asked: The ``components`` list; empty for every pair.
        touching: Count boxes that touch.

    Returns:
        list[dict[str, Any]]: ``components``, ``component_count`` and
        ``volume_mm3`` per interference.
    """
    names = list(boxes_mm)
    found = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            if not asked_pair(a, b, asked):
                continue
            volume = overlap_volume(boxes_mm[a], boxes_mm[b])
            if volume > OVERLAP_MM3 or (
                touching and box_distance(boxes_mm[a], boxes_mm[b]) == 0.0
            ):
                found.append(_entry(a, b, round(volume, 6) + 0.0))
    return found


# ---- COM ----------------------------------------------------------------------


def _meet(a: Box | None, b: Box | None) -> bool:
    """Whether two placed boxes may hold a shared volume; an unread box meets all."""
    return a is None or b is None or box_overlap(a, b) is not None


def _around(boxes: Sequence[Box | None]) -> Box | None:
    """The box around ``boxes``; None when there are none or one wasn't read."""
    known = [box for box in boxes if box is not None]
    if not known or len(known) < len(boxes):
        return None
    around = known[0]
    for box in known[1:]:
        around = union_box(around, box)
    return around


class _Utility:
    """``ISldWorks.GetMathUtility()``, read when the first copy is moved."""

    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter
        self.value: Any = None

    def __call__(self) -> Any:
        if self.value is None:
            self.value = _math_utility(self.adapter)
        return self.value


@dataclass
class _Body:
    """One solid body of a part component.

    Attributes:
        name: The component's name, for messages.
        body: The component's ``IBody2``, in its part's coordinates.
        box: Its box placed in the assembly (m); None when it didn't read.
        values: The component's placement (16 values); None when it moves
            nothing.
    """

    name: str
    body: Any
    box: Box | None
    values: list[float] | None
    _copy: Any = None

    def placed(self, adapter: Any, utility: Callable[[], Any]) -> Any:
        """A temporary copy in the assembly's coordinates, made once.

        Raises:
            Unreadable: It couldn't be copied or moved.
        """
        if self._copy is None:
            try:
                self._copy = _placed_copy(
                    adapter,
                    utility() if self.values is not None else None,
                    self.body,
                    self.values,
                    self.name,
                )
            except RuntimeError as exc:
                raise Unreadable(
                    f"a body of {self.name} could not be copied into place "
                    f"({str(exc).rstrip('.')})"
                ) from exc
        return self._copy


@dataclass
class _Part:
    """A part component: its name, its solid bodies and the box around them."""

    name: str
    bodies: list[_Body] = field(default_factory=list)
    box: Box | None = None


def _name(adapter: Any, component: Any) -> str:
    """``IComponent2.Name2`` (``sub-1/part-1`` in a sub-assembly)."""
    name = adapter._attempt(lambda: component.Name2, default=None)
    return str(name) if name else "<unnamed>"


def _part_components(
    adapter: Any, assembly: Any, ignore_hidden: bool
) -> list[tuple[str, Any, Any]]:
    """The part components from the root down, in tree order.

    Returns:
        list[tuple[str, Any, Any]]: Each one's name, ``IComponent2`` and
        ``GetSuppression2`` (None when it didn't read).

    Raises:
        Unreadable: The components, or a sub-assembly's, can't be read.
    """
    top, error = adapter._attempt_with_error(lambda: assembly.GetComponents(True))
    if error is not None or not isinstance(top, (list, tuple)):
        raise Unreadable("the assembly's components could not be read")
    found: list[tuple[str, Any, Any]] = []
    pending = list(reversed(top))
    read = 0
    while pending:
        if read == MAX_COMPONENTS:
            raise Unreadable(f"the assembly has more than {MAX_COMPONENTS} components")
        limits.check(DOING)
        read += 1
        component = _as_com(adapter, pending.pop(), "IComponent2")
        if component is None:
            raise Unreadable("a component of the assembly could not be read")
        state = adapter._attempt(lambda c=component: c.GetSuppression2(), default=None)
        if state == SUPPRESSED or (
            ignore_hidden and is_visible(adapter, component) is False
        ):
            continue  # and so is everything below it
        children, failed = adapter._attempt_with_error(
            lambda c=component: c.GetChildren()
        )
        if failed is not None:
            raise Unreadable(
                f"the components of {_name(adapter, component)} could not be read"
            )
        if as_list(children):
            pending.extend(reversed(as_list(children)))
            continue
        found.append((_name(adapter, component), component, state))
    return found


def _needed(name: str, asked: Collection[str], present: set[str]) -> bool:
    """Whether a component is in any pair asked for, so its bodies are read."""
    if not asked:
        return True
    if len(set(asked)) == 1:
        return bool(set(asked) & present)  # everything pairs with the one asked
    return name in asked


def _read_part(
    adapter: Any, name: str, component: Any, state: Any, ignore_hidden: bool
) -> _Part:
    """A part component's solid bodies, their boxes placed in the assembly.

    Raises:
        Unreadable: Its bodies or its placement can't be read (a lightweight
            component's bodies aren't loaded).
    """
    raw, error = adapter._attempt_with_error(lambda: component.GetBodies2(SOLID_BODY))
    if error is not None:
        raise Unreadable(f"the bodies of {name} could not be read")
    bodies = as_list(raw)
    if not bodies:
        if state in LIGHTWEIGHT:
            raise Unreadable(f"{name} is lightweight, so its bodies aren't loaded")
        if state is None:
            raise Unreadable(f"{name} shows no body and its state could not be read")
        return _Part(name)  # a part with no solid body: a sketch or surfaces only
    values = _transform_data(adapter, component)
    if values is None:
        raise Unreadable(f"the placement of {name} could not be read")
    rows, origin = [values[0:3], values[3:6], values[6:9]], values[9:12]
    moves = None if tuple(values[:13]) == _IDENTITY else values
    # transform_box leaves out a scale: a scaled component's boxes meet every box.
    unscaled = values[12] == 1.0
    kept = []
    for raw_body in bodies:
        body = _wrapped(adapter, raw_body, "Copy", "GetBodyBox")
        if ignore_hidden and _attempt(adapter, lambda b=body: b.Visible) is False:
            continue
        box = _box_m(adapter, body)
        placed = transform_box(box, rows, origin) if box and unscaled else None
        kept.append(_Body(name, body, placed, moves))
    return _Part(name, kept, _around([b.box for b in kept]))


def _volume(adapter: Any, body: Any, between: str) -> float:
    """A body's volume (mm3).

    Raises:
        Unreadable: It didn't read.
    """
    volume = volume_mm3(adapter, body)
    if volume is None:
        raise Unreadable(f"the volume shared by {between} could not be read")
    return volume


def _overlaps(adapter: Any, first: Any, second: Any, between: str) -> list[float]:
    """The volumes (mm3) of the pieces two placed bodies share, from copies of both.

    Raises:
        Unreadable: A copy, the intersection or a volume failed, or the
            intersection gave an error code it can't be trusted with.
    """
    target, tool = temp_copy(adapter, first), temp_copy(adapter, second)
    if target is None or tool is None:
        raise Unreadable(f"IBody2.Copy gave nothing for {between}")
    pieces, code, error = boolean(adapter, target, tool, BODY_INTERSECT)
    if error is not None:
        raise Unreadable(f"SolidWorks could not intersect {between} ({error})")
    if code is not None and code not in NO_OVERLAP_CODES | {SAME_TOOL_AND_TARGET}:
        raise Unreadable(f"intersecting {between} gave error code {code}")
    if not pieces:
        same = code == SAME_TOOL_AND_TARGET
        return [_volume(adapter, first, between)] if same else []
    volumes = [_volume(adapter, piece, between) for piece in pieces]
    return [volume for volume in volumes if volume > OVERLAP_MM3]


def _shared(
    adapter: Any, utility: Callable[[], Any], first: _Body, second: _Body
) -> list[dict[str, Any]]:
    """The interferences of two bodies whose boxes overlap; none otherwise."""
    if not _meet(first.box, second.box):
        return []
    limits.check(DOING)
    same = first.name == second.name
    between = (
        f"two bodies of {first.name}" if same else f"{first.name} and {second.name}"
    )
    volumes = _overlaps(
        adapter,
        first.placed(adapter, utility),
        second.placed(adapter, utility),
        between,
    )
    return [_entry(first.name, second.name, volume) for volume in volumes]


def body_interferences(
    adapter: Any,
    assembly: Any,
    include_multibody: bool = True,
    ignore_hidden: bool = False,
    asked: Collection[str] = (),
) -> list[dict[str, Any]]:
    """The interferences of the assembly's part components, from their bodies.

    Args:
        adapter: A connected adapter, on the COM thread.
        assembly: The assembly, flagged for ``IAssemblyDoc``.
        include_multibody: Also intersect each component's own bodies.
        ignore_hidden: Leave out hidden components and bodies.
        asked: The ``components`` list; empty for every pair.

    Returns:
        list[dict[str, Any]]: ``components`` (the two names; a component's
        own bodies name it twice), ``component_count`` and ``volume_mm3``
        per interference, in tree order.

    Raises:
        Unreadable: Something the answer depends on can't be read or computed.
        limits.OperationStopped: The call ran out of time, or the client went.
    """
    found = _part_components(adapter, assembly, ignore_hidden)
    present = {name for name, _component, _state in found}
    parts = [
        _read_part(adapter, name, component, state, ignore_hidden)
        for name, component, state in found
        if _needed(name, asked, present)
    ]
    parts = [part for part in parts if part.bodies]
    utility = _Utility(adapter)
    details: list[dict[str, Any]] = []
    for i, part in enumerate(parts):
        if include_multibody and asked_pair(part.name, part.name, asked):
            for j, first in enumerate(part.bodies):
                for second in part.bodies[j + 1 :]:
                    details += _shared(adapter, utility, first, second)
        for other in parts[i + 1 :]:
            if not asked_pair(part.name, other.name, asked) or not _meet(
                part.box, other.box
            ):
                continue
            for first in part.bodies:
                for second in other.bodies:
                    details += _shared(adapter, utility, first, second)
    return details
