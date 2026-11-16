"""Probe the reads behind ``open_model(read_only)``, ``get_feature_definition`` and ``get_sketch``.

None of these COM paths has run on real SolidWorks yet (``lid-readonly``
branch). It builds one scratch part through ``PyWin32Adapter``: a 40 x 20
block extruded 10 mm with a 3 deg draft, a 2 mm fillet and a 1 mm chamfer
on two upright edges, and a sketch on the Top plane with the text "LID" at
(5, 4) mm. It saves the part and records:

1. **Read-only open.** ``OpenDoc6`` with Silent | ReadOnly (3): what
   ``IsOpenedReadOnly`` says, and what ``Save3`` does then; and the same
   open on the part while it is already open for writing (does it stay
   writable, with warning 128?).
2. **Definitions without AccessSelections.** ``get_feature_definition`` on
   each feature: are the values real (10 mm, 3 deg, 2 mm, 1 mm, one edge
   each) when read straight from ``GetDefinition``, and does the part stay
   unmodified (``GetSaveFlag`` False before and after)?
3. **Sketch text.** ``ISketch.GetSketchSegments`` on the text sketch: is the
   text there as a segment of type 4 (swSketchTEXT), do its items arrive as
   ``CDispatch`` or raw ``PyIDispatch``, do ``Text``, ``GetTextFormat()`` and
   ``GetCoordinates()`` answer through late binding on that item, what does
   ``GetSketchTextSegments`` give, and is ``GetCoordinates`` in sketch metres
   (5, 4 mm placed)? Also ``get_sketch``'s plane and axes (Top: sketch y =
   model -Z), and the save flag after it.

It only touches the document it creates and deletes its file at the end.
The report stays in ``tests/.generated/probe_read_only/``.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    .venv\\Scripts\\python.exe tests/scripts/probe_read_only_inspection.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters import sw_type_info
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter, _byref_long
from solidworks_mcp.adapters.solidworks import inspection

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_read_only"


async def _tools(adapter: PyWin32Adapter) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.modeling import register_modeling_tools
    from solidworks_mcp.tools.sketching import register_sketching_tools

    mcp = FastMCP("probe-read-only")
    await register_sketching_tools(mcp, adapter, {})
    await register_modeling_tools(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


def _raw(call: Any) -> dict[str, Any]:
    """Call and record what came back, or what was raised (never swallowed)."""
    try:
        value = call()
    except Exception as exc:  # noqa: BLE001 - a probe records every failure
        return {"raised": f"{type(exc).__name__}: {exc}"}
    return {"type": type(value).__name__, "value": value}


def _save_flag(adapter: PyWin32Adapter) -> Any:
    return adapter._attempt(
        lambda: adapter._get_attr_or_call(adapter.currentModel, "GetSaveFlag")
    )


async def _build(tools: dict[str, Any], adapter: PyWin32Adapter) -> dict[str, Any]:
    made: dict[str, Any] = {}
    assert (await tools["create_part"]({"name": "probe"}))["status"] == "success"
    await tools["create_sketch"]({"plane": "Top"})
    await tools["add_rectangle"]({"x1": 0, "y1": 0, "x2": 40, "y2": 20})
    await tools["exit_sketch"]()
    made["extrude"] = await tools["create_extrusion"]({"depth": 10, "draft_angle": 3})
    # Upright edges, leaning 3 deg with the draft: sketch (x, y) on Top is
    # model (x, up, -y), so the corner (40, 20) runs up from (40, 0, -20).
    lean = {"parallel_to": "y", "parallel_tolerance_deg": 5}
    made["select_fillet_edge"] = await tools["select_edges"](
        {**lean, "inside_box": [[38.5, -1, -21], [41, 11, -18.5]]}
    )
    made["fillet"] = await tools["add_fillet"]({"radius": 2, "edge_names": []})
    made["select_chamfer_edge"] = await tools["select_edges"](
        {**lean, "inside_box": [[-1, -1, -1], [1.5, 11, 1.5]]}
    )
    made["chamfer"] = await tools["add_chamfer"]({"distance": 1, "edge_names": []})
    # A text sketch on the Top plane, at (5, 4) mm in sketch coordinates.
    made["text_sketch"] = await tools["create_sketch"]({"plane": "Top"})
    model = adapter.currentModel
    sw_type_info.flag_members(model, "InsertSketchText")
    made["insert_text"] = _raw(
        lambda: model.InsertSketchText(0.005, 0.004, 0.0, "LID", 0, 0, 0, 100, 0)
    )
    made["exit_text"] = await tools["exit_sketch"]()
    return made


def _sketch_details(adapter: PyWin32Adapter, name: str) -> dict[str, Any]:
    """Question 3: the text sketch, read raw and through the inspection code."""
    model = adapter.currentModel
    feature = model.FeatureByName(name)
    sw_type_info.flag_members(feature, "GetSpecificFeature2")
    sketch = feature.GetSpecificFeature2()
    sw_type_info.flag_members(sketch, "GetSketchSegments", "GetSketchTextSegments")
    details: dict[str, Any] = {}
    segments = sketch.GetSketchSegments() or ()
    details["segments"] = []
    for item in segments:
        row: dict[str, Any] = {"python_type": type(item).__name__}
        sw_type_info.flag_members(item, "GetType", "GetTextFormat", "GetCoordinates")
        row["GetType"] = _raw(lambda i=item: i.GetType())
        row["Text"] = _raw(lambda i=item: i.Text)
        row["GetCoordinates"] = _raw(lambda i=item: i.GetCoordinates())
        row["GetTextFormat"] = _raw(lambda i=item: type(i.GetTextFormat()).__name__)
        row["described"] = inspection.describe_text(adapter, item)
        details["segments"].append(row)
    texts = sketch.GetSketchTextSegments() or ()
    details["text_segments"] = [
        {
            "python_type": type(t).__name__,
            "described": inspection.describe_text(adapter, t),
        }
        for t in texts
    ]
    details["placed_at_mm"] = [5.0, 4.0]
    return details


async def run() -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT / stamp
    out.mkdir(parents=True, exist_ok=True)
    part_path = out / "probe_read_only.SLDPRT"
    report: dict[str, Any] = {"started": stamp}
    adapter = PyWin32Adapter({})
    await adapter.connect()
    before = {
        str(adapter._get_attr_or_call(d, "GetTitle"))
        for d in adapter.swApp.GetDocuments() or []
    }
    try:
        tools = await _tools(adapter)
        report["build"] = await _build(tools, adapter)
        report["features"] = [
            f["name"]
            for f in (await adapter.list_features()).data or []
            if isinstance(f, dict)
        ]
        saved = await adapter.save_file(str(part_path))
        report["saved"] = saved.is_success or saved.error

        # 2. Definitions, read with the part saved (flag False) and open.
        report["save_flag_before_reads"] = _save_flag(adapter)
        report["definitions"] = {}
        for name in report["features"]:
            result = await adapter.get_feature_definition(name)
            report["definitions"][name] = (
                result.data if result.is_success else result.error
            )
        report["save_flag_after_definitions"] = _save_flag(adapter)

        # 3. The text sketch, raw and through get_sketch.
        text_sketch = next(
            (n for n in reversed(report["features"]) if n.startswith("Sketch")), None
        )
        if text_sketch:
            report["sketch_raw"] = _sketch_details(adapter, text_sketch)
            sketch = await adapter.get_sketch(text_sketch)
            report["get_sketch"] = sketch.data if sketch.is_success else sketch.error
        report["save_flag_after_get_sketch"] = _save_flag(adapter)

        # 1b. Opened again read-only while it is open for writing.
        again = await adapter.open_model_read_only(str(part_path))
        report["read_only_while_open"] = (
            again.data.properties if again.is_success else again.error
        )

        # 1a. Closed, then opened read-only; then try to save it.
        await adapter.close_model(save=False)
        opened = await adapter.open_model_read_only(str(part_path))
        report["read_only_open"] = (
            opened.data.properties if opened.is_success else opened.error
        )
        model = adapter.currentModel
        sw_type_info.flag_members(model, "Save3")
        errors, warnings = _byref_long(), _byref_long()
        report["save3_read_only"] = {
            **_raw(lambda: model.Save3(1, errors, warnings)),
            "errors": getattr(errors, "value", errors),
            "warnings": getattr(warnings, "value", warnings),
        }
        await adapter.close_model(save=False)
    finally:
        for title in {
            str(adapter._get_attr_or_call(d, "GetTitle"))
            for d in adapter.swApp.GetDocuments() or []
        } - before:
            adapter._attempt(lambda t=title: adapter.swApp.CloseDoc(t))
        await adapter.disconnect()
        try:
            part_path.unlink()
        except OSError:
            pass
        path = out / "report.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"[probe] report: {path}")
        for key in (
            "read_only_open",
            "read_only_while_open",
            "save3_read_only",
            "save_flag_before_reads",
            "save_flag_after_definitions",
            "save_flag_after_get_sketch",
        ):
            print(f"[probe] {key}: {report.get(key)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
