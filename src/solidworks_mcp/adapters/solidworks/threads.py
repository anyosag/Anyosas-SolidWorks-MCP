"""Modeled ISO metric threads on a circular edge (``tools/threads``, design D6).

SolidWorks' Thread feature (``IFeatureManager.CreateDefinition(swFmSweepThread)``
gives an ``IThreadFeatureData``; ``CreateFeature`` builds it) cuts a real
helical groove that prints, unlike a cosmetic thread. The order of
:func:`make_thread` is design D6:

1. parse ``size`` (``M10x1.5``) and check ``length_mm`` and the turn count
   (:data:`MAX_THREAD_TURNS`), before SolidWorks is touched;
2. make the thread definition: none is ``not_supported`` (D8);
3. set ``Type`` to the profile library (``Metric Tap`` inside a hole,
   ``Metric Die`` on a rod) and read it back: empty means the library file
   is missing, ``not_supported``;
4. ``ISldWorks.GetConfigurationNames(file)`` lists the library's sizes; a size
   not in it is refused, because SolidWorks accepts any size string and cuts
   a meaningless groove for one it doesn't know (hjbaard's finding);
5. find the circular edge from a point on it (not its centre: a rod's end
   edge and its chamfer's edge share one centre);
6. check the edge's diameter: the nominal diameter outside, the ISO basic
   minor diameter inside, because SolidWorks shifts an internal thread with
   its hole;
7. set the definition and ``CreateFeature``; then ``ForceRebuild3`` and the
   failing features.

What the type library and the API help (SOLIDWORKS 2026 SP4.1) say:

- ``IThreadFeatureData`` has one method used here, ``InitializeThreadData``
  (flagged, pitfall #5); ``Type``, ``Size``, ``Edge``, ``ThreadMethod``,
  ``EndCondition``, ``BlindDepth`` and ``RightHanded`` are properties.
- Every ``IThreadFeatureData`` member is "SOLIDWORKS 2018 FCS, Revision
  Number 26.0" in the API help, not 2016 as hjbaard's error text says.
- ``Type`` takes the library name without a path or extension; SolidWorks
  looks in File Locations > Thread Profiles and assumes ``.sldlfp``.
- ``Edge``'s Remarks say to select the edge with ``SelectByRay`` and
  ``Mark = 1``; this code sets the property directly, as hjbaard does.
  Probe 1.5 settles which works.

Unprobed (tasks section 1): the library spellings (``M10x1.0`` or
``M10x1``), the direction the thread runs from the edge, what a refused
``CreateFeature`` leaves in the tree, and the turn limit. The code compares
sizes as numbers so either spelling passes, and removes anything a refused
``CreateFeature`` left.

Credit: hjbaard's ``solidworks-mcp`` (MIT, (c) 2026 Henk Jan Baard),
``session.py`` ``add_thread``, is the reference for the call order. This code
is written fresh.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any

from . import limits
from .com_utils import as_list
from .com_utils import attempt as _attempt
from .com_utils import flag as _flag

#: ``swFeatureNameID_e.swFmSweepThread``.
SWEEP_THREAD = 87
#: ``swThreadMethod_e.swThreadMethod_Cut``.
THREAD_METHOD_CUT = 0
#: ``swThreadEndCondition_e.swThreadEndCondition_Blind``.
THREAD_END_BLIND = 0
#: The thread-profile libraries SolidWorks installs (``*.sldlfp``).
INTERNAL_LIBRARY = "Metric Tap"
EXTERNAL_LIBRARY = "Metric Die"
#: ISO 724 basic profile: D1 = D - (5 * sqrt(3) / 8) * P.
MINOR_FACTOR = 5.0 * math.sqrt(3.0) / 8.0
#: How far an edge may be from ``edge_point``, and its diameter from the one
#: the thread needs (mm).
EDGE_TOLERANCE_MM = 0.01
#: The most turns a thread may have (``length_mm / pitch``).
#:
#: Provisional until probe 1.8 times the rebuilds (D9): 100 turns is M3x0.5 at
#: 50 mm, or M10x1.5 at 150 mm.
MAX_THREAD_TURNS = 100

_SIZE = re.compile(r"^\s*M(\d+(?:\.\d+)?)\s*[xX]\s*(\d+(?:\.\d+)?)\s*$")
_DIAMETER = re.compile(r"^\s*M(\d+(?:\.\d+)?)\s*$")
_EXAMPLE = "M10x1.5"


# ---- pure helpers ---------------------------------------------------------


def _invalid(text: str) -> ValueError:
    return ValueError(f"Invalid request: {text}")


def parse_thread_size(size: str) -> tuple[float, float]:
    """``M10x1.5`` -> ``(10.0, 1.5)``: the nominal diameter and pitch, mm.

    Raises:
        ValueError: ``Invalid request``: not ``M<d>x<p>``, or a zero part.
    """
    match = _SIZE.match(str(size or ""))
    if match is None:
        raise _invalid(
            f"thread size {size!r} is not an ISO metric size with its pitch; "
            f"give it as M<diameter>x<pitch> in mm, e.g. {_EXAMPLE!r}."
        )
    diameter, pitch = float(match.group(1)), float(match.group(2))
    if diameter <= 0 or pitch <= 0:
        raise _invalid(f"thread size {size!r} needs a diameter and pitch above 0.")
    return diameter, pitch


def parse_diameter(size: str) -> float | None:
    """``M3`` -> 3.0; None when ``size`` is not a bare ``M<d>``."""
    match = _DIAMETER.match(str(size or ""))
    return float(match.group(1)) if match else None


def size_name(diameter: float, pitch: float) -> str:
    """``(10, 1.5)`` -> ``M10x1.5``."""
    return f"M{diameter:g}x{pitch:g}"


def minor_diameter(diameter: float, pitch: float) -> float:
    """The ISO basic minor diameter ``D - 1.0825318 * P`` (mm), to 4 places."""
    return round(diameter - MINOR_FACTOR * pitch, 4)


def thread_turns(length: float, pitch: float) -> float:
    """How many turns ``length`` mm of a ``pitch`` mm thread makes."""
    return float(length) / float(pitch)


def _parsed(names: Iterable[Any]) -> list[tuple[str, float, float]]:
    """Each library size that reads as ``M<d>x<p>``, with its numbers."""
    found = []
    for name in names:
        match = _SIZE.match(str(name))
        if match is not None:
            found.append((str(name), float(match.group(1)), float(match.group(2))))
    return found


def _same(a: float, b: float) -> bool:
    return abs(a - b) < 1e-6


def sizes_for(diameter: float, library_sizes: Iterable[Any]) -> list[str]:
    """The library's sizes of one nominal diameter, as the library spells them."""
    return [name for name, d, _p in _parsed(library_sizes) if _same(d, diameter)]


def library_size(size: str, library_sizes: Iterable[Any]) -> str | None:
    """The library's own spelling of ``size`` (``M10x1`` for ``M10x1.0``), or None.

    Sizes are compared as numbers, so ``M10x1`` and ``M10x1.0`` are the same.
    """
    diameter, pitch = parse_thread_size(size)
    return next(
        (
            name
            for name, d, p in _parsed(library_sizes)
            if _same(d, diameter) and _same(p, pitch)
        ),
        None,
    )


def coarse_size(size: str | float, library_sizes: Iterable[Any]) -> str | None:
    """The coarse size for a diameter: the largest pitch the library holds (D5).

    Args:
        size: ``M3``, ``M3x0.5`` (its diameter is used) or a diameter in mm.
        library_sizes: The library's configuration names.

    Returns:
        str | None: ``M3x0.5`` as :func:`size_name` writes it, or None when the
        library has no size of that diameter.
    """
    if isinstance(size, int | float):
        diameter: float | None = float(size)
    else:
        diameter = parse_diameter(size)
        if diameter is None:
            diameter = parse_thread_size(size)[0]
    pitches = [p for _n, d, p in _parsed(library_sizes) if _same(d, diameter)]
    return size_name(diameter, max(pitches)) if pitches else None


def needed_diameter(diameter: float, pitch: float, internal: bool) -> float:
    """The edge diameter a thread needs: nominal outside, basic minor inside."""
    return minor_diameter(diameter, pitch) if internal else diameter


def check_request(size: str, length_mm: Any) -> tuple[float, float]:
    """Steps 1 and 7 of D6: the size's form, the length and the turn limit.

    Raises:
        ValueError: ``Invalid request``, naming what is wrong.
    """
    diameter, pitch = parse_thread_size(size)
    if (
        isinstance(length_mm, bool)
        or not isinstance(length_mm, int | float)
        or length_mm <= 0
    ):
        raise _invalid(f"length_mm must be above 0 mm (got {length_mm!r}).")
    turns = thread_turns(length_mm, pitch)
    if turns > MAX_THREAD_TURNS:
        raise _invalid(
            f"a {size_name(diameter, pitch)} thread {length_mm:g} mm long has "
            f"{turns:.0f} turns, over the limit of {MAX_THREAD_TURNS}: every "
            "rebuild of the part pays for each turn. Shorten the thread or use "
            "a coarser pitch."
        )
    return diameter, pitch


def check_library(size: str, library: str, library_sizes: list[Any]) -> str:
    """Step 4 of D6: ``size`` in the library; returns the library's spelling.

    Raises:
        ValueError: ``Invalid request`` listing the library's sizes of that
            diameter.
    """
    found = library_size(size, library_sizes)
    if found is not None:
        return found
    diameter, _pitch = parse_thread_size(size)
    same = sizes_for(diameter, library_sizes)
    there = (
        f"its M{diameter:g} sizes are {', '.join(same)}"
        if same
        else f"it has no M{diameter:g} size at all"
    )
    raise _invalid(
        f"SolidWorks' {library!r} thread library has no {size}; {there}. "
        "SolidWorks would cut a meaningless groove for a size it doesn't hold."
    )


def check_diameter(
    diameter: float, pitch: float, internal: bool, edge_diameter: float
) -> None:
    """Step 6 of D6: the edge must have the diameter the thread needs.

    Raises:
        ValueError: ``Invalid request`` naming the diameter needed and found.
    """
    needed = needed_diameter(diameter, pitch, internal)
    if abs(edge_diameter - needed) <= EDGE_TOLERANCE_MM:
        return
    size = size_name(diameter, pitch)
    if internal:
        raise _invalid(
            f"an internal {size} thread needs a Ø{needed:.3f} hole (the ISO "
            f"basic minor diameter), but the edge is Ø{edge_diameter:.3f}. "
            f"Drill the Ø{needed:.3f} hole first; SolidWorks shifts an "
            "internal thread with its hole."
        )
    raise _invalid(
        f"an external {size} thread needs a Ø{needed:g} rod (the nominal "
        f"diameter), but the edge is Ø{edge_diameter:.3f}."
    )


def revision_text(revision: Any) -> str:
    """``34.4.1`` -> ``SOLIDWORKS 2026 (revision 34)``; the major is the year - 1992."""
    major = None
    match = re.match(r"\s*(\d+)", str(revision or ""))
    if match is not None:
        major = int(match.group(1))
    if major is None:
        return "this SOLIDWORKS (revision unknown)"
    return f"SOLIDWORKS {major + 1992} (revision {major})"


def thread_block(
    diameter: float, pitch: float, length_mm: float, internal: bool
) -> dict[str, Any]:
    """The reply's ``thread`` (D11)."""
    return {
        "size": size_name(diameter, pitch),
        "pitch_mm": pitch,
        "major_diameter_mm": diameter,
        "minor_diameter_mm": minor_diameter(diameter, pitch),
        "length_mm": float(length_mm),
        "internal": bool(internal),
        "right_handed": True,
    }


def edge_label(info: dict[str, Any]) -> str:
    """``Ø10.000 centred at [0, 0, 40] mm`` for messages."""
    centre = [round(float(c), 4) + 0.0 for c in info.get("center_mm") or []]
    diameter = 2.0 * float(info.get("radius_mm") or 0.0)
    return f"Ø{diameter:.3f} centred at {centre} mm"


# ---- COM ------------------------------------------------------------------


def revision(adapter: Any) -> Any:
    """``ISldWorks.RevisionNumber``, or None."""
    return _attempt(
        adapter, lambda: adapter._get_attr_or_call(adapter.swApp, "RevisionNumber")
    )


def _unsupported(adapter: Any, what: str) -> RuntimeError:
    return RuntimeError(
        f"{revision_text(revision(adapter))} does not support the Thread feature "
        f"here: {what}."
    )


def open_library(adapter: Any, model: Any, internal: bool) -> tuple[Any, str, list]:
    """Steps 2 and 3 of D6, and the sizes of step 4.

    Returns:
        tuple: the ``IThreadFeatureData``, the library file ``Type`` reads
        back as, and the library's sizes.

    Raises:
        RuntimeError: ``does not support``: no definition, the library doesn't
            resolve, or it lists no sizes.
    """
    from .bodies import feature_manager

    manager = feature_manager(adapter, model, "CreateDefinition", "CreateFeature")
    data = _attempt(adapter, lambda: manager.CreateDefinition(SWEEP_THREAD))
    if data is None:
        raise _unsupported(
            adapter,
            "IFeatureManager.CreateDefinition(swFmSweepThread) gave no "
            "IThreadFeatureData (it needs SOLIDWORKS 2018 or later)",
        )
    _flag(adapter, data, "InitializeThreadData")
    _attempt(adapter, lambda: data.InitializeThreadData())
    library = INTERNAL_LIBRARY if internal else EXTERNAL_LIBRARY
    _attempt(adapter, lambda: setattr(data, "Type", library))
    file = str(_attempt(adapter, lambda: data.Type) or "").strip()
    if not file:
        raise _unsupported(
            adapter,
            f"the {library!r} thread-profile library did not resolve; SolidWorks "
            "looks for it in Tools > Options > File Locations > Thread Profiles",
        )
    _flag(adapter, adapter.swApp, "GetConfigurationNames")
    sizes = [
        str(name)
        for name in as_list(
            _attempt(adapter, lambda: adapter.swApp.GetConfigurationNames(file))
        )
        if name
    ]
    if not sizes:
        raise _unsupported(
            adapter, f"the {library!r} library ({file}) lists no thread sizes"
        )
    return data, file, sizes


def library_sizes(adapter: Any, model: Any, internal: bool) -> list[str]:
    """The sizes of the thread library for ``internal`` (``not_supported`` without one)."""
    return open_library(adapter, model, internal)[2]


def _near(box: Any, point: tuple[float, float, float], reach: float) -> bool:
    from .edge_index import distance_to

    return bool(distance_to(box, point) <= reach)


def _circular(index: Any, edge: Any) -> dict[str, Any] | None:
    info = index.describe(edge)
    return info if info.get("curve") == "circular" and info.get("radius_mm") else None


def _distinct(edges: Iterable[Any]) -> list[Any]:
    from .. import sw_type_info

    kept: list[Any] = []
    for edge in edges:
        if not any(
            edge is seen or sw_type_info.same_com_object(edge, seen) for seen in kept
        ):
            kept.append(edge)
    return kept


def find_circular_edge(adapter: Any, point_mm: list[float]) -> tuple[Any, dict]:
    """Step 5 of D6: the circular edge passing within 0.01 mm of ``point_mm``.

    Only the faces whose boxes reach the point are read (``PartIndex``), with
    ``limits.check`` between edges. On a miss every edge of the part is
    measured to name the nearest circular one.

    Raises:
        LookupError: No circular edge there; names the nearest.
        ValueError: ``Invalid request``: more than one; lists them.
    """
    from .edge_index import BOX_SLACK_M, index_for

    point = (
        float(point_mm[0]) / 1000.0,
        float(point_mm[1]) / 1000.0,
        float(point_mm[2]) / 1000.0,
    )
    tolerance = EDGE_TOLERANCE_MM / 1000.0
    reach = tolerance + BOX_SLACK_M
    index = index_for(adapter)
    near = [
        face
        for body in index.bodies()
        if _near(index.body_box(body), point, reach)
        for face in index.faces(body)
        if _near(index.face_box(face), point, reach)
    ]
    index.read_edges(near)
    found: list[tuple[Any, dict[str, Any]]] = []
    for edge in _distinct(e for face in near for e in face.edges or []):
        limits.check("finding the thread's edge")
        info = _circular(index, edge)
        if info is None:
            continue
        distance = index.distance(edge, point)
        if distance is not None and distance <= tolerance:
            found.append((edge, info))
    if len(found) == 1:
        return found[0]
    if found:
        listed = "; ".join(edge_label(info) for _edge, info in found)
        raise _invalid(
            f"{len(found)} circular edges pass within {EDGE_TOLERANCE_MM} mm of "
            f"{list(point_mm)} mm: {listed}. Give a point on one edge only."
        )
    raise LookupError(_missing_edge(adapter, index, point, point_mm))


def _missing_edge(
    adapter: Any, index: Any, point: tuple[float, float, float], point_mm: list
) -> str:
    """The not-found message, naming the nearest circular edge of the part."""
    best: tuple[float, dict[str, Any]] | None = None
    for body in index.bodies():
        for edge in _distinct(index.body_edges(body)):
            limits.check("finding the nearest circular edge")
            info = _circular(index, edge)
            if info is None:
                continue
            distance = index.distance(edge, point)
            if distance is not None and (best is None or distance < best[0]):
                best = (distance, info)
    where = (
        f"No circular edge found within {EDGE_TOLERANCE_MM} mm of {list(point_mm)} mm"
    )
    if best is None:
        return f"{where}; the part has no circular edges."
    info = best[1]
    return (
        f"{where}. The nearest is {best[0] * 1000.0:.3f} mm away: "
        f"{edge_label(info)}, radius {float(info['radius_mm']):.4f} mm. Give a "
        "point on the edge itself (centre plus radius), not its centre."
    )


def feature_count(adapter: Any, model: Any) -> int | None:
    manager = _attempt(adapter, lambda: model.FeatureManager)
    _flag(adapter, manager, "GetFeatureCount")
    count = _attempt(adapter, lambda: manager.GetFeatureCount(True))
    return (
        int(count) if isinstance(count, int) and not isinstance(count, bool) else None
    )


def remove_extra(adapter: Any, model: Any, count_before: int | None) -> None:
    """Delete features a refused ``CreateFeature`` left after the last one."""
    from .bodies import delete_features

    now = feature_count(adapter, model)
    if count_before is None or now is None or now <= count_before:
        return
    _flag(adapter, model, "FeatureByPositionReverse")
    extra = [
        _attempt(adapter, lambda p=position: model.FeatureByPositionReverse(p))
        for position in range(now - count_before)
    ]
    delete_features(adapter, model, [f for f in extra if f is not None])


def make_thread(
    adapter: Any,
    size: str,
    edge_point: list[float],
    length_mm: float,
    internal: bool = False,
) -> dict[str, Any]:
    """Make a right-handed modeled thread from a circular edge (D6).

    Returns:
        dict[str, Any]: ``feature``, ``thread`` (:func:`thread_block`),
        ``edge`` (``center_mm``, ``diameter_mm``), ``library`` (the profile
        file) and ``failing_features``.

    Raises:
        ValueError: ``Invalid request``: the size, the length, the turns, the
            library, two edges or the diameter.
        LookupError: No circular edge at ``edge_point``.
        RuntimeError: ``does not support`` (D8), or SolidWorks made no thread.
    """
    from .equations import failing_features

    diameter, pitch = check_request(size, length_mm)
    model = adapter.currentModel
    data, file, sizes = open_library(adapter, model, internal)
    library = INTERNAL_LIBRARY if internal else EXTERNAL_LIBRARY
    spelled = check_library(size_name(diameter, pitch), library, sizes)
    edge, info = find_circular_edge(adapter, edge_point)
    edge_diameter = 2.0 * float(info["radius_mm"])
    check_diameter(diameter, pitch, internal, edge_diameter)
    limits.check("making the thread")
    for name, value in (
        ("Edge", edge),
        ("ThreadMethod", THREAD_METHOD_CUT),
        ("Size", spelled),
        ("EndCondition", THREAD_END_BLIND),
        ("BlindDepth", float(length_mm) / 1000.0),
        ("RightHanded", True),
    ):
        _attempt(adapter, lambda n=name, v=value: setattr(data, n, v))
    count_before = feature_count(adapter, model)
    manager = _attempt(adapter, lambda: model.FeatureManager)
    _flag(adapter, manager, "CreateFeature")
    feature = _attempt(adapter, lambda: manager.CreateFeature(data))
    if feature is None:
        remove_extra(adapter, model, count_before)
        raise RuntimeError(
            f"SolidWorks made no {size_name(diameter, pitch)} thread from the "
            f"edge {edge_label(info)} (IFeatureManager.CreateFeature returned "
            f"nothing). Is there {float(length_mm):g} mm of material behind "
            "the edge, along the thread's axis?"
        )
    _flag(adapter, model, "ForceRebuild3")
    _attempt(adapter, lambda: model.ForceRebuild3(False))
    name = _attempt(adapter, lambda: feature.Name)
    return {
        "feature": str(name) if name else None,
        "thread": thread_block(diameter, pitch, length_mm, internal),
        "edge": {
            "center_mm": [round(float(c), 4) + 0.0 for c in info["center_mm"]],
            "diameter_mm": round(edge_diameter, 4),
        },
        "library": file,
        "failing_features": failing_features(adapter, model),
    }


def part_guard(adapter: Any, operation: str) -> str | None:
    """Why ``operation`` can't run on the active document, or None."""
    from .io import _doc_type

    if not adapter.currentModel:
        return (
            f"Invalid request: {operation} takes an open part, and no document is open"
        )
    doc_type = _doc_type(adapter)
    if doc_type != 1:
        kind = {2: "an assembly", 3: "a drawing"}.get(doc_type or 0, "not a part")
        return (
            f"Invalid request: {operation} takes a part; the active document is {kind}"
        )
    return None


class SolidWorksThreadsMixin:
    """``create_thread`` on ``PyWin32Adapter`` (D12)."""

    async def create_thread(
        self,
        size: str,
        edge_point: list[float],
        length_mm: float,
        internal: bool = False,
    ) -> Any:
        """Make a modeled ISO metric thread from a circular edge.

        Returns:
            AdapterResult[dict[str, Any]]: ``feature``, ``thread``, ``edge``,
            ``library`` and ``failing_features``.
        """
        from ..base import AdapterResult, AdapterResultStatus

        try:
            check_request(size, length_mm)
        except ValueError as exc:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))
        refused = part_guard(self, "create_thread")
        if refused is not None:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=refused)
        return self._handle_com_operation(  # type: ignore[attr-defined]
            "create_thread",
            lambda: make_thread(self, size, edge_point, length_mm, internal),
        )
