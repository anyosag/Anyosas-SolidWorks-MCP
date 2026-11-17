"""Build an extrude or a cut from every region of a sketch: their union.

The Dipper's sketches had touching and overlapping circles and hexagons,
which SolidWorks won't extrude as they are ("sketch contours intersect",
``sketch_contours``). The session worked out polygon unions and offsets by
hand, or split the sketch into several bosses. SolidWorks' own way is the
Selected Contours box: pick every region the contours enclose and extrude
them, and it unions them into one solid.

``regions: "all"`` does that, as SolidWorks' own macro does (API help,
"Create Extrude Feature Using Sketch Contours"): contour selection on
(``ISelectionMgr.EnableContourSelection``), the sketch at mark 4, then each
region (``ISketch.GetSketchRegions``, each ``ISketchRegion.Select2`` with a
``SelectData`` of mark 4, else ``Select(True, 4)``) appended at mark 4. A
sketch with no regions read selects its contours (``GetSketchContours``)
the same way. Every region is a region, so a contour wholly inside another
is filled: a ring becomes a disc.

:func:`union_refusal` says when building every region would fill such a
hole, so the automatic retry after a crossing failure isn't made then.
"""

from __future__ import annotations

from typing import Any

from .com_utils import as_list
from .com_utils import attempt as _attempt
from .com_utils import flag as _flag
from .sketch_contours import Circle, Contour, crossings

#: The selection mark FeatureExtrusion3 and FeatureCut4 read contours at.
REGION_MARK = 4


def _inside(point: tuple[float, float], contour: Contour) -> bool:
    """Whether ``point`` lies inside ``contour`` (on its edge counts as outside)."""
    if isinstance(contour, Circle):
        dx, dy = point[0] - contour.center[0], point[1] - contour.center[1]
        return dx * dx + dy * dy < contour.radius * contour.radius
    inside = False
    for (x1, y1), (x2, y2) in contour.edges():
        if (y1 > point[1]) != (y2 > point[1]):
            x_at = x1 + (point[1] - y1) * (x2 - x1) / (y2 - y1)
            if point[0] < x_at:
                inside = not inside
    return inside


def _a_point_of(contour: Contour) -> tuple[float, float]:
    """A point on ``contour``'s edge."""
    if isinstance(contour, Circle):
        return (contour.center[0] + contour.radius, contour.center[1])
    return contour.points[0]


def nested(found: list[Contour]) -> list[str]:
    """Each contour that lies wholly inside another it doesn't cross, described.

    A contour that crosses no other and has a point inside one lies wholly
    inside it.
    """
    crossing = set(crossings_by_index(found))
    inner = []
    for index, contour in enumerate(found):
        for other_index, other in enumerate(found):
            if index == other_index or frozenset((index, other_index)) in crossing:
                continue
            if _inside(_a_point_of(contour), other):
                inner.append(f"{contour.describe()} is inside {other.describe()}")
                break
    return inner


def crossings_by_index(found: list[Contour]) -> list[frozenset[int]]:
    """The index pairs of the contours that cross or touch."""
    return [
        frozenset((i, j))
        for i in range(len(found))
        for j in range(i + 1, len(found))
        if crossings([found[i], found[j]])
    ]


def _segment_count(contour: Contour) -> int:
    return 1 if isinstance(contour, Circle) else len(contour.points)


def union_refusal(segments: list[dict[str, Any]], found: list[Contour]) -> str | None:
    """Why building every region of a sketch wouldn't be its contours' union.

    Args:
        segments: The sketch's described segments (``describe_segment``).
        found: Its closed contours (``sketch_contours.contours``).

    Returns:
        str | None: None when every drawn segment belongs to a circle or a
        closed loop of lines and none lies inside another; otherwise why
        not, as a clause.
    """
    drawn = [s for s in segments if not s.get("construction")]
    if sum(_segment_count(c) for c in found) != len(drawn):
        return (
            "not every segment is part of a full circle or a closed loop of "
            "lines, so a hole could be filled"
        )
    inner = nested(found)
    if inner:
        return f"{inner[0]}, and building every region would fill it"
    return None


def _sketch_of(adapter: Any, sketch_name: str) -> Any:
    model = adapter.currentModel
    feature = _attempt(adapter, lambda: model.FeatureByName(sketch_name))
    if feature is None:
        return None
    _flag(adapter, feature, "GetSpecificFeature2")
    return _attempt(adapter, lambda: feature.GetSpecificFeature2())


def _wrapped(adapter: Any, item: Any, interface: str) -> Any:
    """An array item as a dispatch late binding can call (``io._as_com``)."""
    if type(item).__name__ != "PyIDispatch":
        return item
    from .io import _as_com

    return _as_com(adapter, item, interface) or item


def _select_each(adapter: Any, items: list[Any], interface: str, data: Any) -> int:
    """Append each region or contour at :data:`REGION_MARK`; how many took."""
    selected = 0
    for raw in items:
        item = _wrapped(adapter, raw, interface)
        _flag(adapter, item, "Select2", "Select")
        took = data is not None and _attempt(
            adapter, lambda i=item: i.Select2(True, data), False
        )
        if not took:
            took = _attempt(adapter, lambda i=item: i.Select(True, REGION_MARK), False)
        selected += bool(took)
    return selected


def select_all(adapter: Any, sketch_name: str) -> dict[str, Any]:
    """Select every region of ``sketch_name`` for the next extrude or cut.

    Turns contour selection on; :func:`end` turns it off.

    Returns:
        dict[str, Any]: ``kind`` (``"regions"`` or ``"contours"``),
        ``found`` and ``selected`` counts.

    Raises:
        RuntimeError: The sketch has no region SolidWorks would select.
    """
    from .features import _null_callout

    model = adapter.currentModel
    sketch = _sketch_of(adapter, sketch_name)
    if sketch is None:
        raise RuntimeError(f"SolidWorks gave no sketch for {sketch_name!r}.")
    _flag(adapter, sketch, "GetSketchRegions", "GetSketchContours")
    kind, interface = "regions", "ISketchRegion"
    items = as_list(_attempt(adapter, lambda: sketch.GetSketchRegions()))
    if not items:
        kind, interface = "contours", "ISketchContour"
        items = as_list(_attempt(adapter, lambda: sketch.GetSketchContours()))
    if not items:
        raise RuntimeError(
            f"SolidWorks found no closed regions in sketch {sketch_name!r}."
        )
    sel_mgr = model.SelectionManager
    _attempt(adapter, lambda: setattr(sel_mgr, "EnableContourSelection", True))
    _attempt(adapter, lambda: model.ClearSelection2(True))
    _attempt(
        adapter,
        lambda: model.Extension.SelectByID2(
            sketch_name, "SKETCH", 0.0, 0.0, 0.0, False, REGION_MARK, _null_callout(), 0
        ),
    )
    _flag(adapter, sel_mgr, "CreateSelectData")
    data = _attempt(adapter, lambda: sel_mgr.CreateSelectData())
    if data is not None:
        _attempt(adapter, lambda: setattr(data, "Mark", REGION_MARK))
    selected = _select_each(adapter, items, interface, data)
    if not selected:
        end(adapter)
        raise RuntimeError(
            f"SolidWorks would not select the {len(items)} {kind} of sketch "
            f"{sketch_name!r}."
        )
    return {"kind": kind, "found": len(items), "selected": selected}


def end(adapter: Any) -> None:
    """Turn contour selection off again (best effort)."""
    model = adapter.currentModel
    sel_mgr = _attempt(adapter, lambda: model.SelectionManager) if model else None
    if sel_mgr is not None:
        _attempt(adapter, lambda: setattr(sel_mgr, "EnableContourSelection", False))


def note(sketch_name: str, crossing: list[str]) -> str:
    """The warning an automatic union of every region carries."""
    shown = "; ".join(crossing[:2]) + ("; ..." if len(crossing) > 2 else "")
    return (
        f"Sketch {sketch_name!r}: its contours cross ({shown}), so it was built "
        "from every region they enclose, which SolidWorks unions (regions: "
        "'all'); no contour lies inside another, so no hole was filled."
    )
