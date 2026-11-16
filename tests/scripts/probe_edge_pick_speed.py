"""Probe the coordinate edge pick on a part full of text edges.

The Lid build (SolidWorks 2026 SP4.1): ``add_chamfer`` with one coordinate
edge (``"0.0442,0.001,0"``) took 46 s and 110 s on a part with about 23 text
bodies' worth of edges, because the picker measured every edge with
``IEdge.GetClosestPointOn``. It now leaves out bodies (``IBody2.GetBodyBox``)
and faces (``IFace2.GetBox``) whose box is out of reach and measures only the
edges of the faces near the point. This settles:

* how long one pick takes now, how many edges it measures, against how long
  measuring one edge takes (a sample of 200, scaled to every edge);
* that the pruning rests on solid ground: every edge's end points (and arc
  centre +- radius) lie inside each of its faces' boxes, and every face box
  inside its body's box (``_BOX_PAD_M`` of slack);
* that the edge picked is the one meant, compared with measuring every edge;
* how long ``add_chamfer`` itself takes with that spec, merged text (one
  body) and text left as separate bodies.

The part is a 91.6 x 91.6 x 4.7 mm plate from the Top plane with the text
"LID TEXT PROBE 0123456789" standing 1 mm on it, extruded twice into two
parts: once merged, once as separate bodies. The probe only touches the
documents it creates, closes them without saving, and keeps the report in
``tests/.generated/probe_edge_pick_speed/``.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    .venv\\Scripts\\python.exe tests/scripts/probe_edge_pick_speed.py
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters import sw_type_info
from solidworks_mcp.adapters.base import ExtrusionParameters
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import features
from solidworks_mcp.adapters.solidworks.face_selection import describe_edge

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_edge_pick_speed"

#: An outer top edge of the plate, metres (x = 45.8 mm, y = 4.7 mm, z = 0).
POINT = (0.0458, 0.0047, 0.0)
TEXT = "LID TEXT PROBE 0123456789"


async def _part(adapter: PyWin32Adapter, merge: bool) -> str:
    assert (await adapter.create_part()).is_success
    title = str(adapter._get_attr_or_call(adapter.currentModel, "GetTitle"))
    assert (await adapter.create_sketch("Top")).is_success
    assert (await adapter.add_rectangle(-45.8, -45.8, 45.8, 45.8)).is_success
    assert (await adapter.exit_sketch()).is_success
    plate = await adapter.create_extrusion(ExtrusionParameters(depth=4.7))
    assert plate.is_success, plate.error
    plane = await adapter.create_reference_plane("Top Plane", offset=4.7)
    assert plane.is_success, plane.error
    assert (await adapter.create_sketch(plane.data["name"])).is_success
    model = adapter.currentModel
    sw_type_info.flag_members(model, "InsertSketchText")
    for row, y in enumerate((0.02, 0.0, -0.02)):
        part = TEXT[row * 9 : row * 9 + 9]
        adapter._attempt(
            lambda p=part, yy=y: model.InsertSketchText(
                -0.035, yy, 0.0, p, 0, 0, 0, 100, 100
            )
        )
    assert (await adapter.exit_sketch()).is_success
    text = await adapter.create_extrusion(
        ExtrusionParameters(depth=1.0, merge_result=merge)
    )
    assert text.is_success, text.error
    return title


def _bodies(adapter: PyWin32Adapter) -> list[Any]:
    model = adapter.currentModel
    sw_type_info.flag_members(model, "GetBodies2")
    return list(model.GetBodies2(0, False) or [])


def _inside(box: Any, point: Any) -> bool:
    if box is None or point is None:
        return True
    lo, hi = box
    return all(a - 1e-9 <= p <= b + 1e-9 for a, b, p in zip(lo, hi, point, strict=True))


def _soundness(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Edge ends inside their faces' boxes, face boxes inside the body box."""
    counts = {"bodies": 0, "faces": 0, "edge_checks": 0, "outside": []}
    for body in _bodies(adapter):
        counts["bodies"] += 1
        body_box = features._body_box(adapter, body)
        sw_type_info.flag_members(body, "GetFaces")
        for face in body.GetFaces() or []:
            counts["faces"] += 1
            box = features._face_box(adapter, face)
            if (
                body_box
                and box
                and not (_inside(body_box, box[0]) and _inside(body_box, box[1]))
            ):
                counts["outside"].append({"face_box": box, "body_box": body_box})
            sw_type_info.flag_members(face, "GetEdges")
            for edge in face.GetEdges() or []:
                info = describe_edge(adapter, edge)
                points = [info.get("start_mm"), info.get("end_mm")]
                if "center_mm" in info and "radius_mm" in info:
                    c, r = info["center_mm"], info["radius_mm"]
                    points += [[c[0] + r, c[1], c[2]], [c[0] - r, c[1], c[2]]]
                for p in points:
                    if p is None:
                        continue
                    counts["edge_checks"] += 1
                    metres = [v / 1000 for v in p]
                    if not _inside(box, metres):
                        counts["outside"].append({"edge_point_mm": p, "face_box": box})
    counts["outside"] = counts["outside"][:20]
    return counts


def _timed_pick(adapter: PyWin32Adapter) -> dict[str, Any]:
    picker = features._BodyEntityPicker(adapter)
    measured = 0
    distance = picker._distance

    def _counting(entity: Any, point: Any) -> Any:
        nonlocal measured
        measured += 1
        return distance(entity, point)

    picker._distance = _counting  # type: ignore[method-assign]
    started = time.perf_counter()
    nearest = picker.nearest("GetEdges", POINT)
    seconds = time.perf_counter() - started
    picked = describe_edge(adapter, nearest[0]) if nearest and nearest[0] else None
    return {
        "seconds": round(seconds, 3),
        "measured_edges": measured,
        "distance_mm": None if not nearest else round(nearest[1] * 1000, 6),
        "picked": picked,
        "pruned": picker.pruned,
    }


def _direct_pick(adapter: PyWin32Adapter, point: Any) -> dict[str, Any]:
    """``pick_edge_at`` (SelectByID2, kept only on the point) against the search."""
    model = adapter.currentModel
    picker = features._BodyEntityPicker(adapter)
    started = time.perf_counter()
    kept = picker.pick_edge_at(point, append=False)
    seconds = time.perf_counter() - started
    manager = sw_type_info.flagged(model.SelectionManager, "ISelectionMgr")
    count = manager.GetSelectedObjectCount2(-1)
    chosen = manager.GetSelectedObject6(1, -1) if count else None
    model.ClearSelection2(True)
    searched = features._BodyEntityPicker(adapter).nearest("GetEdges", point)
    want = describe_edge(adapter, searched[0]) if searched and searched[0] else None
    got = describe_edge(adapter, chosen) if chosen is not None else None
    return {
        "seconds": round(seconds, 3),
        "kept": kept,
        "selected_after": count,
        "picked": got,
        "search_picks": want,
        "same_as_search": None if not kept else got == want,
    }


def _every_edge(adapter: PyWin32Adapter) -> dict[str, Any]:
    """The old way: every edge, timed on a sample and scaled."""
    picker = features._BodyEntityPicker(adapter)
    bodies = _bodies(adapter)
    for body in bodies:
        sw_type_info.flag_members(body, "GetEdges")
    edges = [e for body in bodies for e in (body.GetEdges() or [])]
    sample = edges[:: max(1, len(edges) // 200)][:200]
    started = time.perf_counter()
    nearest = min(
        ((e, picker._distance(e, POINT)) for e in sample),
        key=lambda pair: math.inf if pair[1] is None else pair[1],
    )
    per_edge = (time.perf_counter() - started) / max(1, len(sample))
    return {
        "edges": len(edges),
        "sampled": len(sample),
        "seconds_per_edge": round(per_edge, 5),
        "estimated_seconds_for_every_edge": round(per_edge * len(edges), 1),
        "nearest_in_sample_mm": None
        if nearest[1] is None
        else round(nearest[1] * 1000, 6),
    }


async def _variant(adapter: PyWin32Adapter, merge: bool) -> dict[str, Any]:
    title = await _part(adapter, merge)
    report: dict[str, Any] = {"title": title, "bodies": len(_bodies(adapter))}
    try:
        report["soundness"] = _soundness(adapter)
        report["pick"] = _timed_pick(adapter)
        report["direct_pick"] = _direct_pick(adapter, POINT)
        # The plate's bottom edge under the same point, likely hidden from view.
        report["direct_pick_bottom"] = _direct_pick(adapter, (POINT[0], 0.0, POINT[2]))
        report["every_edge"] = _every_edge(adapter)
        spec = ",".join(str(c) for c in POINT)
        started = time.perf_counter()
        chamfer = await adapter.add_chamfer(0.5, [spec])
        report["add_chamfer"] = {
            "seconds": round(time.perf_counter() - started, 3),
            "ok": chamfer.is_success,
            "error": chamfer.error,
        }
    finally:
        adapter._attempt(lambda: adapter.swApp.CloseDoc(title))
    return report


async def run() -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT / stamp
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"started": stamp, "point_m": POINT}
    adapter = PyWin32Adapter({})
    await adapter.connect()
    try:
        report["merged"] = await _variant(adapter, merge=True)
        report["separate_bodies"] = await _variant(adapter, merge=False)
    finally:
        await adapter.disconnect()
        path = out / "report.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"[probe] report: {path}")
        for key in ("merged", "separate_bodies"):
            r = report.get(key, {})
            print(
                f"[probe] {key}: bodies {r.get('bodies')}, pick {r.get('pick')}, "
                f"every edge {r.get('every_edge')}, add_chamfer {r.get('add_chamfer')}, "
                f"outside boxes {len(r.get('soundness', {}).get('outside', []))}"
            )
            for pick in ("direct_pick", "direct_pick_bottom"):
                print(f"[probe] {key} {pick}: {r.get(pick)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
