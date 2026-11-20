"""Probe what SolidWorks' GLB and STL exports hold, for ``export_for_blender``.

``export_for_blender`` assumes things about SolidWorks 2026 that no live run
has checked. This probe builds scratch documents through ``PyWin32Adapter``
and records, for each:

1. **GLB units and axes.** A 40 x 20 x 10 mm block (sketch on Front, so the
   10 mm runs along Z), painted red. Does ``SaveAs3`` to ``.glb`` write a
   GLB at all; is its scene 0.04 x 0.02 x 0.01 (metres, SolidWorks' own
   axes), or turned, or in mm? ``units_check`` in the reply says which.
2. **GLB colours.** Does the material carry the red (``base_color``
   ~[1, 0, 0, 1]), and under what name?
3. **STL in model coordinates.** The STL path turns SolidWorks' "move to
   positive space" off: the block's lowest corner must stay at the model's
   (the sketch is centred on the origin, so the STL's min is about
   [-20, -10, 0] mm, not [0, 0, 0]). And the user's STL options must read
   back as they were afterwards.
4. **Assembly parts in place.** Two instances of the block, the second at
   x = 100 mm: does the GLB hold two nodes, named how (``block-1``?), with
   the second 0.1 m along X; and is the scene 0.14 m long?

It writes the replies (``blender_code`` saved beside them as
``<case>.blender_code.py``) and the GLBs' JSON chunks to the report. It
touches only the documents it creates, closes them, and leaves the files
in ``tests/.generated/probe_blender_handoff/<stamp>/`` for the Blender step.

**The Blender step** (Blender open with its MCP add-on, and the user's OK):
for each ``*.blender_code.py`` in the report folder, pass its text to the
Blender MCP's ``execute_blender_code`` and check that the result has
``size_matches: true``; take a ``render_thumbnail_to_path`` of each and
look that the block stands on its 40 x 20 face (Y up model) and that the
assembly's two blocks sit 100 mm apart along Blender's X. Then run the part
GLB's code a second time: ``replaced_objects`` must be 2 or more and the
scene must hold one block, not two.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    .venv\\Scripts\\python.exe tests/scripts/probe_blender_handoff.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import stl_settings
from solidworks_mcp.utils import gltf_summary

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_blender_handoff"

#: The STL options ``export_for_blender`` changes, to check they come back.
_STL_OPTIONS = {
    "units": ("integer", stl_settings.STL_UNITS),
    "keep_coordinates": ("toggle", stl_settings.STL_KEEP_COORDINATES),
    "quality": ("integer", stl_settings.STL_QUALITY),
}


async def _tools(adapter: PyWin32Adapter) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.export import register_export_tools
    from solidworks_mcp.tools.modeling import register_modeling_tools
    from solidworks_mcp.tools.sketching import register_sketching_tools

    mcp = FastMCP("probe-blender-handoff")
    await register_sketching_tools(mcp, adapter, {})
    await register_modeling_tools(mcp, adapter, {})
    await register_export_tools(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


def _stl_options(adapter: PyWin32Adapter) -> dict[str, Any]:
    """The user's STL options this probe's exports change, as SolidWorks holds them."""
    app = adapter.swApp
    read: dict[str, Any] = {}
    for label, (kind, pref) in _STL_OPTIONS.items():
        getter = stl_settings.PREFERENCE_METHODS[kind][0]
        read[label] = adapter._attempt(lambda g=getter, p=pref: getattr(app, g)(p))
    return read


def _paint_red(adapter: PyWin32Adapter) -> Any:
    """Give the part a red appearance through ``MaterialPropertyValues``."""
    model = adapter.currentModel

    def paint() -> Any:
        values = list(model.MaterialPropertyValues)
        values[0:3] = [1.0, 0.0, 0.0]
        model.MaterialPropertyValues = values
        model.GraphicsRedraw2()
        return list(model.MaterialPropertyValues)

    try:
        return paint()
    except Exception as exc:  # noqa: BLE001 - a probe records every failure
        return {"raised": f"{type(exc).__name__}: {exc}"}


async def _export(
    tools: dict[str, Any], out: Path, case: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """One ``export_for_blender`` call: the reply, the code saved, the GLB read raw."""
    reply = await tools["export_for_blender"](
        {"folder": str(out / case), "name": case, **arguments}
    )
    data = reply.get("data") or {}
    code = data.pop("blender_code", None)
    if code:
        (out / f"{case}.blender_code.py").write_text(code, encoding="utf-8")
    record: dict[str, Any] = {"reply": reply}
    if str(data.get("file_path", "")).endswith(".glb"):
        try:
            document = gltf_summary.read_json_chunk(data["file_path"])
            record["glb_json"] = {
                key: document.get(key)
                for key in (
                    "asset",
                    "scene",
                    "scenes",
                    "nodes",
                    "materials",
                    "accessors",
                )
            }
        except (OSError, ValueError) as exc:
            record["glb_json"] = {"raised": str(exc)}
    return record


async def _block(tools: dict[str, Any], name: str) -> dict[str, Any]:
    """A 40 x 20 mm rectangle centred on the origin on Front, extruded 10 mm."""
    steps = {
        "create_part": await tools["create_part"]({"name": name}),
        "create_sketch": await tools["create_sketch"]({"plane": "Front"}),
        "add_rectangle": await tools["add_rectangle"](
            {"x1": -20, "y1": -10, "x2": 20, "y2": 10}
        ),
        "exit_sketch": await tools["exit_sketch"]({}),
        "create_extrusion": await tools["create_extrusion"]({"depth": 10}),
    }
    return {k: v.get("status") if isinstance(v, dict) else v for k, v in steps.items()}


async def run() -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT / stamp
    out.mkdir(parents=True, exist_ok=True)
    part_path = out / "probe_block.SLDPRT"
    assembly_path = out / "probe_pair.SLDASM"
    report: dict[str, Any] = {"started": stamp}
    adapter = PyWin32Adapter({})
    await adapter.connect()
    before = {
        str(adapter._get_attr_or_call(d, "GetTitle"))
        for d in adapter.swApp.GetDocuments() or []
    }
    try:
        tools = await _tools(adapter)
        report["stl_options_before"] = _stl_options(adapter)
        report["build"] = await _block(tools, "probe_block")
        report["paint"] = _paint_red(adapter)
        saved = await adapter.save_file(str(part_path))
        report["part_saved"] = saved.is_success or saved.error

        # 1-2. The part as a GLB: units, axes and colour.
        report["part_glb"] = await _export(tools, out, "part_glb", {})
        # The same part told it is Z-up: only the turn may differ.
        report["part_glb_z"] = await _export(tools, out, "part_glb_z", {"up_axis": "z"})
        # 3. The part as an STL in model coordinates, and the options after.
        report["part_stl"] = await _export(
            tools, out, "part_stl", {"file_format": "stl", "resolution": "fine"}
        )
        report["stl_options_after"] = _stl_options(adapter)

        # 4. Two instances in an assembly, 100 mm apart along X.
        made = await tools["create_assembly"]({"name": "probe_pair"})
        report["assembly"] = made.get("status")
        for x in (0.0, 100.0):
            placed = await tools["insert_component"](
                {"file_path": str(part_path), "x": x, "y": 0.0, "z": 0.0}
            )
            report.setdefault("inserted", []).append(placed.get("status"))
        saved = await adapter.save_file(str(assembly_path))
        report["assembly_saved"] = saved.is_success or saved.error
        report["assembly_glb"] = await _export(tools, out, "assembly_glb", {})
        report["assembly_stl"] = await _export(
            tools, out, "assembly_stl", {"file_format": "stl"}
        )
    finally:
        for title in {
            str(adapter._get_attr_or_call(d, "GetTitle"))
            for d in adapter.swApp.GetDocuments() or []
        } - before:
            adapter._attempt(lambda t=title: adapter.swApp.CloseDoc(t))
        await adapter.disconnect()
        path = out / "report.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"[probe] report: {path}")
        for case in (
            "part_glb",
            "part_glb_z",
            "part_stl",
            "assembly_glb",
            "assembly_stl",
        ):
            reply = (report.get(case) or {}).get("reply") or {}
            data = reply.get("data") or {}
            print(
                f"[probe] {case}: {reply.get('status')} units={data.get('units')} "
                f"file_size={data.get('file_size')} parts={data.get('parts')} "
                f"warning={reply.get('warning')!r} error={reply.get('message') if reply.get('status') == 'error' else None}"
            )
        print(f"[probe] stl options before {report.get('stl_options_before')}")
        print(f"[probe] stl options after  {report.get('stl_options_after')}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
