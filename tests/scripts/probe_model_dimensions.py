"""Probe where a part's dimensions live, for ``list_model_dimensions``.

The Lid build (SolidWorks 2026 SP4.1) got 0 dimensions from
``list_model_dimensions`` on a part whose Boss-Extrude2/3 are "ICE"
(Instant3D, pitfall #43). The walk now reads ``FirstFeature`` as a method or
a property, completes a ``GetNextFeature`` walk that stops short with
``FeatureByPositionReverse``, and visits every feature's sub-features (an
absorbed sketch). This settles which of those mattered:

* for every top-level feature (``GetNextFeature``) and every sub-feature
  (``GetFirstSubFeature`` / ``GetNextSubFeature``): its name,
  ``GetTypeName2``, ``GetTypeName``, and the display dimensions its own
  ``GetFirstDisplayDimension`` chain gives (``FullName``, ``Value``);
* whether ``GetNextFeature`` lists the absorbed sketches on the top level
  (then sub-features only repeat them), and whether it stops before
  ``IFeatureManager.GetFeatureCount(True)``;
* whether an "ICE" extrude's ``GetFirstDisplayDimension`` gives its depth;
* what ``list_model_dimensions`` replies, and what it replied with the old
  top-level walk (``FirstFeature`` called on the flagged document).

The part is built the way the Lid was: a Top sketch with a 60 x 40
rectangle, its width and height dimensioned, extruded 5 mm
(``create_extrusion``), then a Ø10 circle, its diameter dimensioned, cut
through all (``create_cut_extrude``). It only touches that document, closes
it without saving, and keeps the report in
``tests/.generated/probe_model_dimensions/``.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    .venv\\Scripts\\python.exe tests/scripts/probe_model_dimensions.py
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

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_model_dimensions"

_MEMBERS = (
    "GetNextFeature",
    "GetFirstSubFeature",
    "GetNextSubFeature",
    "GetFirstDisplayDimension",
    "GetNextDisplayDimension",
    "GetTypeName",
    "GetTypeName2",
)


async def _tools(adapter: PyWin32Adapter) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.modeling import register_modeling_tools
    from solidworks_mcp.tools.sketching import register_sketching_tools

    mcp = FastMCP("probe-model-dimensions")
    await register_sketching_tools(mcp, adapter, {})
    await register_modeling_tools(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


def _call(obj: Any, name: str) -> Any:
    try:
        return getattr(obj, name)()
    except Exception as exc:  # noqa: BLE001 - a probe records every failure
        return f"error: {exc}"


def _dimensions(feature: Any) -> list[dict[str, Any]]:
    out = []
    display = _call(feature, "GetFirstDisplayDimension")
    while display is not None and not isinstance(display, str) and len(out) < 100:
        display = sw_type_info.flagged(display, "IDisplayDimension")
        dim = sw_type_info.flagged(display.GetDimension2(0), "IDimension")
        out.append({"full_name": dim.FullName, "value": dim.Value})
        try:
            display = feature.GetNextDisplayDimension(display)
        except Exception as exc:  # noqa: BLE001
            out.append({"next_error": str(exc)})
            break
    if isinstance(display, str):
        out.append({"first_error": display})
    return out


def _describe(feature: Any, depth: int) -> dict[str, Any]:
    sw_type_info.flag_members(feature, *_MEMBERS)
    row: dict[str, Any] = {
        "name": feature.Name,
        "type_name2": _call(feature, "GetTypeName2"),
        "type_name": _call(feature, "GetTypeName"),
        "dimensions": _dimensions(feature),
        "sub_features": [],
    }
    if depth < 2:
        sub = _call(feature, "GetFirstSubFeature")
        while sub is not None and not isinstance(sub, str):
            row["sub_features"].append(_describe(sub, depth + 1))
            sw_type_info.flag_members(sub, *_MEMBERS)
            sub = _call(sub, "GetNextSubFeature")
    return row


def _tree(model: Any) -> dict[str, Any]:
    top = []
    feature = model.FirstFeature()
    while feature is not None and len(top) < 500:
        top.append(_describe(feature, 0))
        feature = _call(feature, "GetNextFeature")
        if isinstance(feature, str):
            top.append({"next_error": feature})
            break
    manager = sw_type_info.flagged(model.FeatureManager, "IFeatureManager")
    names = [row.get("name") for row in top]
    subs = {s["name"] for row in top for s in row.get("sub_features", [])}
    return {
        "top_level": top,
        "top_level_count": len(top),
        "feature_count_top_level": manager.GetFeatureCount(True),
        "feature_count_all": manager.GetFeatureCount(False),
        "sketches_also_on_top_level": sorted(subs & set(names)),
    }


def _old_walk(model: Any) -> list[str]:
    """The walk before the fix: top-level features only."""
    names = []
    feature = model.FirstFeature()
    while feature is not None and len(names) < 500:
        feature = sw_type_info.flagged(feature, "IFeature")
        display = feature.GetFirstDisplayDimension()
        while display is not None:
            display = sw_type_info.flagged(display, "IDisplayDimension")
            names.append(
                sw_type_info.flagged(display.GetDimension2(0), "IDimension").FullName
            )
            display = feature.GetNextDisplayDimension(display)
        feature = feature.GetNextFeature()
    return names


async def run() -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT / stamp
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"started": stamp}
    adapter = PyWin32Adapter({})
    await adapter.connect()
    title = None
    try:
        tools = await _tools(adapter)
        assert (await adapter.create_part()).is_success
        model = adapter.currentModel
        title = str(adapter._get_attr_or_call(model, "GetTitle"))

        assert (await tools["create_sketch"]({"plane": "Top"}))["status"] == "success"
        corners = [(-30, -20), (30, -20), (30, 20), (-30, 20)]
        lines = []
        for (x1, y1), (x2, y2) in zip(corners, corners[1:] + corners[:1], strict=True):
            line = await tools["add_line"]({"x1": x1, "y1": y1, "x2": x2, "y2": y2})
            assert line["status"] == "success", line
            lines.append(line["line"]["id"])
        for line_id, value in zip(lines, (60, 40), strict=False):
            dimensioned = await tools["add_sketch_dimension"](
                {"entity1": line_id, "value": value}
            )
            report.setdefault("rectangle_dimensions", []).append(dimensioned)
        assert (await tools["exit_sketch"]())["status"] == "success"
        boss = await tools["create_extrusion"]({"depth": 5})
        assert boss["status"] == "success", boss

        assert (await tools["create_sketch"]({"plane": "Top"}))["status"] == "success"
        circle = await tools["add_circle"]({"center_x": 0, "center_y": 0, "radius": 5})
        assert circle["status"] == "success", circle
        report["circle_dimension"] = await tools["add_sketch_dimension"](
            {
                "entity1": circle["circle"]["id"],
                "dimension_type": "diameter",
                "value": 10,
            }
        )
        assert (await tools["exit_sketch"]())["status"] == "success"
        cut = await tools["create_cut_extrude"](
            {"depth": 10, "end_condition": "ThroughAll"}
        )
        assert cut["status"] == "success", cut

        report["tree"] = _tree(model)
        try:
            report["old_walk"] = _old_walk(model)
        except Exception as exc:  # noqa: BLE001
            report["old_walk"] = f"error: {exc}"
        listed = await adapter.list_model_dimensions()
        report["list_model_dimensions"] = (
            listed.data if listed.is_success else {"error": listed.error}
        )
    finally:
        if title:
            adapter._attempt(lambda: adapter.swApp.CloseDoc(title))
        await adapter.disconnect()
        path = out / "report.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"[probe] report: {path}")
        tree = report.get("tree", {})
        print(
            f"[probe] top level {tree.get('top_level_count')} of "
            f"{tree.get('feature_count_top_level')} (all {tree.get('feature_count_all')})"
            f"; sketches also on the top level: {tree.get('sketches_also_on_top_level')}"
        )
        print(f"[probe] old walk: {report.get('old_walk')}")
        listed_now = report.get("list_model_dimensions")
        if isinstance(listed_now, list):
            print(f"[probe] now: {[d.get('full_name') for d in listed_now]}")
        else:
            print(f"[probe] now: {listed_now}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
