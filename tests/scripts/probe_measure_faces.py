"""Probe ``IMeasure`` on two parallel faces: which property is the 2.5 mm?

The Lid build (SolidWorks 2026 SP4.1) measured two parallel planar faces at
y = 1.0 and 3.5 mm, ``["face:0.0175,0.001,0.0175", "face:0,0.0035,0"]``, and
got ``Distance`` 24.75 mm with ``DeltaX`` 17.5, ``DeltaY`` 0, ``DeltaZ`` 17.5
and ``IsParallel`` True. ``measure_entities`` now reports ``NormalDistance``
and, for two parallel planar faces, makes ``distance_m`` the normal distance.
This settles what that rests on:

* every ``IMeasure`` property after ``Calculate(None)``, for the faces picked
  as ``measure_entities`` picks them (``IEntity.Select4``, no pick point) and
  picked with ``SelectByID2`` at the same points (which records them);
* whether ``Distance``/``DeltaY`` change with the pick (a centre or pick-point
  distance) and whether ``NormalDistance`` is the 2.5 mm;
* each selected face's ``ISurface.PlaneParams`` (to check the order assumed:
  normal first, then a point), ``IFace2.Normal`` and ``GetBox``, to make sure
  the faces picked are the ones meant;
* what ``measure_entities`` itself replies.

The part is a 40 x 40 x 1 mm plate from the Top plane with a 20 x 20 x 2.5 mm
boss in the middle: plate top at y = 1, boss top at y = 3.5 mm, as on the lid.
It only touches the document it creates, closes it without saving, and keeps
the report in ``tests/.generated/probe_measure_faces/``.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    .venv\\Scripts\\python.exe tests/scripts/probe_measure_faces.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters import sw_type_info
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks.features import (
    _null_callout,
    _select_edge_by_coord,
)

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_measure_faces"

#: The two specs the Lid build measured, metres.
POINTS = [(0.0175, 0.001, 0.0175), (0.0, 0.0035, 0.0)]

#: Every readable ``IMeasure`` property (SolidWorks 2026 gen_py wrapper).
MEASURE_PROPERTIES = (
    "Angle",
    "ArcLength",
    "ArcOption",
    "Area",
    "CenterDistance",
    "ChordLength",
    "DeltaX",
    "DeltaY",
    "DeltaZ",
    "Diameter",
    "Distance",
    "IsConcentricSpheres",
    "IsIntersect",
    "IsParallel",
    "IsPerpendicular",
    "Length",
    "Normal",
    "NormalDistance",
    "Perimeter",
    "Projection",
    "ProjectionOption",
    "Radius",
    "SpericalCenterDistance",
    "TotalArea",
    "TotalLength",
    "X",
    "Y",
    "Z",
)


async def _tools(adapter: PyWin32Adapter) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.modeling import register_modeling_tools
    from solidworks_mcp.tools.sketching import register_sketching_tools

    mcp = FastMCP("probe-measure-faces")
    await register_sketching_tools(mcp, adapter, {})
    await register_modeling_tools(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


async def _boss(tools: dict[str, Any], plane: str, half: float, depth: float) -> None:
    assert (await tools["create_sketch"]({"plane": plane}))["status"] == "success"
    drawn = await tools["add_rectangle"](
        {"x1": -half, "y1": -half, "x2": half, "y2": half}
    )
    assert drawn["status"] == "success", drawn
    assert (await tools["exit_sketch"]())["status"] == "success"
    made = await tools["create_extrusion"]({"depth": depth})
    assert made["status"] == "success", made


def _value(obj: Any, name: str) -> Any:
    try:
        value = getattr(obj, name)
    except Exception as exc:  # noqa: BLE001 - a probe records every failure
        return f"error: {exc}"
    if isinstance(value, (list, tuple)):
        return [float(v) if isinstance(v, (int, float)) else str(v) for v in value]
    return value if isinstance(value, (int, float, bool, str)) else str(value)


def _selected_faces(model: Any) -> list[dict[str, Any]]:
    """Each selected face's plane, normal and box, as read."""
    manager = sw_type_info.flagged(model.SelectionManager, "ISelectionMgr")
    out = []
    for index in range(1, int(manager.GetSelectedObjectCount2(-1) or 0) + 1):
        face = manager.GetSelectedObject6(index, -1)
        sw_type_info.flag_members(face, "GetSurface", "GetBox")
        surface = face.GetSurface()
        sw_type_info.flag_members(surface, "IsPlane")
        out.append(
            {
                "type": manager.GetSelectedObjectType3(index, -1),
                "is_plane": surface.IsPlane(),
                "plane_params": _value(surface, "PlaneParams"),
                "face_normal": _value(face, "Normal"),
                "box_mm": [round(float(v) * 1000, 4) for v in face.GetBox()],
            }
        )
    return out


def _measure(model: Any) -> dict[str, Any]:
    """Calculate on the current selection and read every property."""
    extension = sw_type_info.flagged(model.Extension, "IModelDocExtension")
    measure = sw_type_info.flagged(extension.CreateMeasure(), "IMeasure")
    try:
        calculated = measure.Calculate(None)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    return {
        "calculated": calculated,
        **{name: _value(measure, name) for name in MEASURE_PROPERTIES},
    }


def _with_select4(adapter: PyWin32Adapter, model: Any) -> dict[str, Any]:
    model.ClearSelection2(True)
    picked = [
        _select_edge_by_coord(adapter, *point, index > 0, 0, "FACE")
        for index, point in enumerate(POINTS)
    ]
    report = {"picked": picked, "faces": _selected_faces(model)}
    report["measure"] = _measure(model)
    model.ClearSelection2(True)
    return report


def _with_select_by_id(model: Any) -> dict[str, Any]:
    model.ClearSelection2(True)
    picked = [
        bool(
            model.Extension.SelectByID2(
                "", "FACE", *point, index > 0, 0, _null_callout(), 0
            )
        )
        for index, point in enumerate(POINTS)
    ]
    report = {"picked": picked, "faces": _selected_faces(model)}
    report["measure"] = _measure(model)
    model.ClearSelection2(True)
    return report


async def run() -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT / stamp
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"started": stamp, "points_m": POINTS}
    adapter = PyWin32Adapter({})
    await adapter.connect()
    title = None
    try:
        tools = await _tools(adapter)
        assert (await adapter.create_part()).is_success
        model = adapter.currentModel
        title = str(adapter._get_attr_or_call(model, "GetTitle"))
        await _boss(tools, "Top", 20, 1.0)  # plate, y 0..1
        # The boss's sketch on the plate's top face, through the Top plane
        # offset: a reference plane at 1 mm.
        plane = await tools["create_reference_plane"](
            {"reference": "Top Plane", "offset": 1.0}
        )
        assert plane["status"] == "success", plane
        await _boss(tools, plane["plane"]["name"], 10, 2.5)  # boss, y 1..3.5
        report["select4"] = _with_select4(adapter, model)
        report["select_by_id2"] = _with_select_by_id(model)
        measured = await adapter.measure_entities(
            [f"face:{','.join(str(c) for c in p)}" for p in POINTS]
        )
        report["measure_entities"] = (
            measured.data if measured.is_success else {"error": measured.error}
        )
    finally:
        if title:
            adapter._attempt(lambda: adapter.swApp.CloseDoc(title))
        await adapter.disconnect()
        path = out / "report.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"[probe] report: {path}")
        for key in ("select4", "select_by_id2"):
            m = report.get(key, {}).get("measure", {})
            print(
                f"[probe] {key}: Distance {m.get('Distance')}, NormalDistance "
                f"{m.get('NormalDistance')}, CenterDistance {m.get('CenterDistance')}"
                f", Delta ({m.get('DeltaX')}, {m.get('DeltaY')}, {m.get('DeltaZ')})"
            )
        print(f"[probe] measure_entities: {report.get('measure_entities')}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
