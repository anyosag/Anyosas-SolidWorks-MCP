"""Probe the COM behaviour behind the Dipper session's fixes (branch ``dipper-fixes``).

The fixes were written without SolidWorks (another session was using it).
Every uncertain COM fact they rest on is checked here on scratch parts built
through ``PyWin32Adapter``; each step says what it settles, and its findings
go in the report.

Steps (``STEPS`` below, in order):

1. ``cut_floor``: a 40 x 20 x 10 block; a 0.8 mm blind cut from a plane 5 mm
   up, inside it (the Dipper's slab), and a 3 mm cut down from the top face.
   Records each cut's planar faces (``IFace2.Normal``, area), the side it
   cut, and the tool's ``floor_area_mm2`` and warning. Settles: a cut's floor
   faces back toward its sketch (its outward ``Normal`` against the cut
   direction), the slab has a ceiling as big as its floor, and only the
   floor counts now (800 mm2 each time, no warning).
2. ``fillet_bar_up``: a block with a cut at the end; the bar rolled back
   above the cut; ``add_fillet`` on two top edges; then rolled forward.
   Records ``GetLastFeatureAdded`` raw (flagged) and its ``Name`` against the
   tree's new name, the fillet definition's ``GetEdgeCount``, the next
   feature's ``IsRolledBack``, and the tool's reply. Settles: the new
   feature reads under late binding (else the fallback by name works),
   ``GetEdgeCount`` on a new fillet equals the edges given, and the
   bar-up warning names the cut.
3. ``fillet_loses_edge``: a block; Fillet1 on the front-top and back-top
   edges; the bar rolled back above it and a cut inserted that removes the
   front-top edge; rolled forward. Records ``set_rollback``'s ``rebuild``
   and ``warning``, ``GetWhatsWrongCount`` and ``GetWhatsWrong`` raw, the
   tree walk (``GetErrorCode2``), Fillet1's ``GetEdgeCount`` after, then
   ``rebuild_model``'s reply and ``ForceRebuild3``'s return. Settles: a
   fillet that lost one of two edges is a warning (code 13,
   ``swFeatureErrorFilletNoEdge``?) or an error; whether
   ``GetWhatsWrongCount`` counts warnings (the adapter skips
   ``GetWhatsWrong`` at 0); whether ``GetWhatsWrong`` is current right
   after ``EditRollback`` without a rebuild; whether ``GetEdgeCount`` reads
   the lost edge; and whether ``ForceRebuild3`` returns False for warnings
   only (``rebuild_model`` would then be an error).
4. ``rectangle``: a block from Front; a plane 15 mm in front of it; in a
   sketch there, ``CreateCornerRectangle`` raw for a 2 x 64 rectangle near
   the block's edges with AddToDB on and off and ``swSketchInference``
   (249) on and off (each read back and deleted), then ``add_rectangle``
   twice with the Dipper's corners and ``get_sketch``. The user's toggles
   249, 95 (``swSketchInferFromModel``) and 9 (``swSketchAutomaticRelations``)
   are recorded and put back. Settles: which setting squared the rectangle,
   and that ``add_rectangle`` now draws it right or refuses it.
5. ``contours``: two circles r 18 35.5 mm apart on Top, extruded. Records
   the reply and each segment as ``describe_segment`` reads it. Settles: a
   circle's ``GetCenterPoint2`` reads through the segment's late-bound
   dispatch (else circles are never found), and the error is
   ``sketch_contours_intersect``.
6. ``move``: a part and an assembly using it, saved and closed in the
   report folder; ``move_document`` dry run and real, with ``method`` and
   the save guard's records after. Then, with ``--cloud-folder <folder>``
   (a scratch folder inside Google Drive, OneDrive or Dropbox; nothing else
   there is touched), the same in a new subfolder of it, plus
   ``MoveDocument`` raw on a closed copy, the file id (``st_ino``) before and
   after an ``os.rename`` there, ``file_access.move_blocker`` and
   ``sync_client``. Settles: what ``MoveDocument`` returns there, that the
   plain-move fallback moves it, and whether that file system keeps file
   ids through a rename (the save guard's rename check needs them).
7. ``save_after_rename``: a part made here, saved, closed and renamed with
   ``os.rename``; an assembly inserting the renamed file; the part changed
   (a boss added) and the assembly saved in place. Settles: the save no
   longer returns ``confirmation_required`` for it (also in the cloud folder
   when given).

It only touches documents it creates and closes only those (without
saving), deletes its files at the end, and leaves the user's documents and
SolidWorks settings as they were. The report stays in
``tests/.generated/probe_dipper_fixes/``.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    $env:PYTHONPATH = "src"
    .venv\\Scripts\\python.exe tests/scripts/probe_dipper_fixes.py
    .venv\\Scripts\\python.exe tests/scripts/probe_dipper_fixes.py --cloud-folder "G:\\Shared drives\\Scratch"
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import shutil
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters import sw_type_info
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import (
    feature_editing,
    file_access,
    rebuild_inspector,
    rectangle_check,
    saving,
)
from solidworks_mcp.adapters.solidworks.io import _byref_variant

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_dipper_fixes"


async def _tools(adapter: PyWin32Adapter) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.file_management import register_file_management_tools
    from solidworks_mcp.tools.modeling import register_modeling_tools
    from solidworks_mcp.tools.sketching import register_sketching_tools

    mcp = FastMCP("probe-dipper-fixes")
    await register_sketching_tools(mcp, adapter, {})
    await register_modeling_tools(mcp, adapter, {})
    await register_file_management_tools(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


async def _invoke(tools: dict[str, Any], name: str, args: dict[str, Any]) -> Any:
    """Call a tool; one with no input model (``exit_sketch``) takes keywords only."""
    fn = tools[name]
    positional = [
        p
        for p in inspect.signature(fn).parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    if not positional:
        return await fn(**args)
    return await fn(args)


async def _ok(tools: dict[str, Any], name: str, args: dict[str, Any]) -> Any:
    reply = await _invoke(tools, name, args)
    assert reply["status"] == "success", (name, reply)
    return reply


def _raw(call: Callable[[], Any]) -> dict[str, Any]:
    """Call and record what came back, or what was raised (never swallowed)."""
    try:
        value = call()
    except Exception as exc:  # noqa: BLE001 - a probe records every failure
        return {"raised": f"{type(exc).__name__}: {exc}"}
    return {"type": type(value).__name__, "value": value}


def _on_com(adapter: PyWin32Adapter, name: str, call: Callable[[], Any]) -> Any:
    """Run raw COM work as an adapter operation: settle pause, dialog watchdog."""
    result = adapter._handle_com_operation(f"probe_{name}", call)
    return result.data if result.is_success else {"error": result.error}


def _titles(adapter: PyWin32Adapter) -> set[str]:
    return {
        str(adapter._get_attr_or_call(d, "GetTitle"))
        for d in adapter.swApp.GetDocuments() or []
    }


def _close_new(adapter: PyWin32Adapter, before: set[str]) -> None:
    """Close the documents this probe opened, unsaved; the user's stay open."""
    for title in _titles(adapter) - before:
        adapter._attempt(lambda t=title: adapter.swApp.CloseDoc(t))


async def _block(
    tools: dict[str, Any], plane: str, rect: tuple[float, ...], depth: float
) -> None:
    """A rectangle (sketch x1, y1, x2, y2 in mm) on ``plane``, extruded."""
    await _ok(tools, "create_sketch", {"plane": plane})
    x1, y1, x2, y2 = rect
    await _ok(tools, "add_rectangle", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})
    await _ok(tools, "exit_sketch", {})
    await _ok(tools, "create_extrusion", {"depth": depth})


def _model(adapter: PyWin32Adapter) -> Any:
    return adapter.currentModel


def _feature(adapter: PyWin32Adapter, name: str) -> Any:
    feature = _model(adapter).FeatureByName(name)
    sw_type_info.flag_members(
        feature,
        "GetDefinition",
        "GetFaces",
        "GetNextFeature",
        "IsRolledBack",
        "GetErrorCode2",
        "GetTypeName2",
    )
    return feature


def _edge_count(adapter: PyWin32Adapter, name: str) -> Any:
    """A fillet's edge count, raw and as ``feature_editing.edge_count`` reads it."""
    data = _feature(adapter, name).GetDefinition()
    sw_type_info.flag_members(data, "GetEdgeCount")
    return {
        "GetEdgeCount": _raw(lambda: data.GetEdgeCount()),
        "edge_count": feature_editing.edge_count(adapter, data),
    }


def _faces(adapter: PyWin32Adapter, name: str) -> list[dict[str, Any]]:
    out = []
    for face in _feature(adapter, name).GetFaces() or ():
        sw_type_info.flag_members(face, "GetArea")
        normal = list(face.Normal or [])
        if any(abs(c) > 1e-9 for c in normal):
            out.append(
                {
                    "normal": [round(float(c), 6) for c in normal],
                    "area_mm2": round(float(face.GetArea()) * 1e6, 4),
                }
            )
    return out


def _whats_wrong(adapter: PyWin32Adapter) -> dict[str, Any]:
    """GetWhatsWrongCount and GetWhatsWrong raw, and the tree walk."""
    model = _model(adapter)
    ext = model.Extension
    sw_type_info.flag_members(ext, "GetWhatsWrongCount", "GetWhatsWrong")
    features, codes, warnings = _byref_variant(), _byref_variant(), _byref_variant()
    called = _raw(lambda: ext.GetWhatsWrong(features, codes, warnings))
    names = [
        str(getattr(f, "Name", f)) for f in (getattr(features, "value", None) or ())
    ]
    walk = rebuild_inspector._from_tree_walk(adapter, model)
    return {
        "GetWhatsWrongCount": _raw(lambda: ext.GetWhatsWrongCount()),
        "GetWhatsWrong": {
            "returned": called,
            "features": names,
            "codes": list(getattr(codes, "value", None) or ()),
            "warnings": list(getattr(warnings, "value", None) or ()),
        },
        "tree_walk": [
            {"feature": i.feature, "code": i.code, "is_warning": i.is_warning}
            for i in walk
        ],
    }


# ---- the steps ---------------------------------------------------------------------


async def step_cut_floor(adapter: PyWin32Adapter, tools: dict[str, Any]) -> Any:
    """Settles which way a cut's floor faces, and the slab's ceiling."""
    assert (await adapter.create_part()).is_success
    await _block(tools, "Top", (-20, -10, 20, 10), 10)
    out: dict[str, Any] = {}
    for label, offset, rect, depth in (
        ("slab_inside", 5.0, (-30, -20, 30, 20), 0.8),
        ("down_from_top", 10.0, (-5, -5, 5, 5), 3.0),
    ):
        plane = await _ok(
            tools,
            "create_reference_plane",
            {"reference": "Top Plane", "offset": offset},
        )
        await _ok(tools, "create_sketch", {"plane": plane["plane"]["name"]})
        x1, y1, x2, y2 = rect
        await _ok(tools, "add_rectangle", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})
        await _ok(tools, "exit_sketch", {})
        reply = await _invoke(
            tools, "create_cut_extrude", {"depth": depth, "end_condition": "Blind"}
        )
        name = (reply.get("cut_extrude") or {}).get("name")
        out[label] = {
            "reply": reply,
            "faces": _on_com(adapter, "cut_faces", lambda n=name: _faces(adapter, n))
            if name
            else None,
            "expected_floor_mm2": 800.0 if label == "slab_inside" else 100.0,
        }
    return out


async def step_fillet_bar_up(adapter: PyWin32Adapter, tools: dict[str, Any]) -> Any:
    """Settles GetLastFeatureAdded, GetEdgeCount on a new fillet, the bar-up warning."""
    assert (await adapter.create_part()).is_success
    await _block(tools, "Top", (-20, -10, 20, 10), 10)
    await _ok(tools, "create_sketch", {"plane": "Top"})
    await _ok(tools, "add_circle", {"center_x": 0, "center_y": 0, "radius": 3})
    await _ok(tools, "exit_sketch", {})
    cut = await _ok(
        tools, "create_cut_extrude", {"depth": 10, "end_condition": "ThroughAllBoth"}
    )
    cut_name = cut["cut_extrude"]["name"]
    out: dict[str, Any] = {
        "bar": await _invoke(tools, "set_rollback", {"before": cut_name})
    }
    fillet = await _invoke(
        tools,
        "add_fillet",
        {"radius": 1.0, "edge_names": ["0,0.01,0.01", "0,0.01,-0.01"]},
    )
    out["add_fillet"] = fillet
    name = (fillet.get("fillet") or {}).get("name")

    def read() -> dict[str, Any]:
        ext = _model(adapter).Extension
        sw_type_info.flag_members(ext, "GetLastFeatureAdded")
        last = _raw(lambda: ext.GetLastFeatureAdded())
        newest = last.get("value")
        following = _feature(adapter, name).GetNextFeature() if name else None
        if following is not None:
            sw_type_info.flag_members(following, "IsRolledBack")
        return {
            "GetLastFeatureAdded": {
                "raw": {k: str(v) for k, v in last.items()},
                "name": _raw(lambda: newest.Name) if newest is not None else None,
                "tree_name": name,
            },
            "edges": _edge_count(adapter, name) if name else None,
            "next_feature": {
                "name": _raw(lambda: following.Name),
                "IsRolledBack": _raw(lambda: following.IsRolledBack()),
            }
            if following is not None
            else None,
        }

    out["read"] = _on_com(adapter, "fillet_read", read)
    out["forward"] = await _invoke(tools, "set_rollback", {})
    out["edges_after_forward"] = (
        _on_com(adapter, "edges_after", lambda: _edge_count(adapter, name))
        if name
        else None
    )
    return out


async def step_fillet_loses_edge(adapter: PyWin32Adapter, tools: dict[str, Any]) -> Any:
    """Settles how a fillet that lost an edge on roll-forward is reported."""
    assert (await adapter.create_part()).is_success
    await _block(tools, "Top", (-20, -10, 20, 10), 10)
    fillet = await _ok(
        tools,
        "add_fillet",
        {"radius": 1.0, "edge_names": ["0,0.01,0.01", "0,0.01,-0.01"]},
    )
    name = fillet["fillet"]["name"]
    out: dict[str, Any] = {
        "add_fillet": fillet,
        "edges_before": _on_com(adapter, "edges", lambda: _edge_count(adapter, name)),
        "bar": await _invoke(tools, "set_rollback", {"before": name}),
    }
    # On Top, sketch y is model -Z: y -12..-8 is z 8..12, the front 2 mm.
    await _ok(tools, "create_sketch", {"plane": "Top"})
    await _ok(tools, "add_rectangle", {"x1": -30, "y1": -12, "x2": 30, "y2": -8})
    await _ok(tools, "exit_sketch", {})
    out["cut"] = await _invoke(
        tools, "create_cut_extrude", {"depth": 10, "end_condition": "ThroughAllBoth"}
    )
    out["forward"] = await _invoke(tools, "set_rollback", {})
    out["right_after_forward"] = _on_com(
        adapter, "whats_wrong", lambda: _whats_wrong(adapter)
    )
    out["edges_after"] = _on_com(
        adapter, "edges_after", lambda: _edge_count(adapter, name)
    )
    out["rebuild_model"] = await _invoke(tools, "rebuild_model", {})

    def force() -> dict[str, Any]:
        model = _model(adapter)
        sw_type_info.flag_members(model, "ForceRebuild3")
        return {
            "ForceRebuild3": _raw(lambda: model.ForceRebuild3(False)),
            **_whats_wrong(adapter),
        }

    out["after_force_rebuild"] = _on_com(adapter, "force_rebuild", force)
    return out


def _toggles(app: Any) -> dict[int, Any]:
    sw_type_info.flag_members(app, "GetUserPreferenceToggle", "SetUserPreferenceToggle")
    return {t: app.GetUserPreferenceToggle(t) for t in (249, 95, 9)}


async def step_rectangle(adapter: PyWin32Adapter, tools: dict[str, Any]) -> Any:
    """Settles which setting squared the Dipper's rectangle, and add_rectangle's check."""
    app = adapter.swApp
    users = _toggles(app)
    out: dict[str, Any] = {"user_toggles": users}
    try:
        assert (await adapter.create_part()).is_success
        await _block(tools, "Front", (0, -90, 30, -10), 10)
        plane = await _ok(
            tools, "create_reference_plane", {"reference": "Front Plane", "offset": 15}
        )
        await _ok(tools, "create_sketch", {"plane": plane["plane"]["name"]})
        asked = (10.6, -81.0, 12.6, -17.0)

        def draw(add_to_db: bool, inference: bool) -> dict[str, Any]:
            manager = adapter.currentSketchManager
            manager.AddToDB = add_to_db
            app.SetUserPreferenceToggle(249, inference)
            lines = manager.CreateCornerRectangle(
                asked[0] / 1000, asked[1] / 1000, 0, asked[2] / 1000, asked[3] / 1000, 0
            )
            drawn = rectangle_check.drawn_extent(adapter, lines)
            deleted = rectangle_check.delete_segments(adapter, lines) if lines else None
            return {
                "returned": len(lines or ()),
                "drawn": drawn,
                "mismatch": rectangle_check.mismatch(asked, drawn) if drawn else None,
                "deleted": deleted,
            }

        for add_to_db in (True, False):
            for inference in (True, False):
                key = f"AddToDB {add_to_db}, inference {inference}"
                out[key] = _on_com(
                    adapter, "rectangle", lambda a=add_to_db, i=inference: draw(a, i)
                )
        _on_com(
            adapter,
            "add_to_db",
            lambda: setattr(adapter.currentSketchManager, "AddToDB", True),
        )
        app.SetUserPreferenceToggle(249, bool(users[249]))
        out["add_rectangle"] = [
            await _invoke(
                tools,
                "add_rectangle",
                {"x1": x, "y1": -81, "x2": x + 2, "y2": -17},
            )
            for x in (10.6, 20.6)
        ]
        exited = await _invoke(tools, "exit_sketch", {})
        sketch = (exited.get("sketch") or {}).get("name") or adapter._last_sketch_name
        out["get_sketch"] = await _invoke(tools, "get_sketch", {"name": sketch})
    finally:
        for toggle, value in users.items():
            adapter._attempt(
                lambda t=toggle, v=value: app.SetUserPreferenceToggle(t, bool(v))
            )
    out["toggles_after"] = _toggles(app)
    return out


async def step_contours(adapter: PyWin32Adapter, tools: dict[str, Any]) -> Any:
    """Settles reading a circle's centre, and the contours error."""
    assert (await adapter.create_part()).is_success
    await _ok(tools, "create_sketch", {"plane": "Top"})
    for x in (0.0, 35.5):
        await _ok(tools, "add_circle", {"center_x": x, "center_y": 0, "radius": 18})
    exited = await _ok(tools, "exit_sketch", {})
    sketch = (exited.get("sketch") or {}).get("name") or adapter._last_sketch_name
    reply = await _invoke(tools, "create_extrusion", {"depth": 5})

    def segments() -> Any:
        feature = _model(adapter).FeatureByName(sketch)
        sw_type_info.flag_members(feature, "GetSpecificFeature2")
        return [
            info
            for _segment, info in feature_editing.describe_segments(
                adapter, feature.GetSpecificFeature2()
            )
        ]

    return {
        "sketch": sketch,
        "create_extrusion": reply,
        "segments": _on_com(adapter, "segments", segments),
        "expected_error_type": "sketch_contours_intersect",
    }


async def _part_and_assembly(
    adapter: PyWin32Adapter, tools: dict[str, Any], folder: Path, before: set[str]
) -> dict[str, str]:
    """A part and an assembly using it, saved in ``folder`` and closed."""
    folder.mkdir(parents=True, exist_ok=True)
    files = {
        "part": str(folder / "pd_handle.SLDPRT"),
        "assembly": str(folder / "pd_dipper.SLDASM"),
    }
    assert (await adapter.create_part()).is_success
    await _block(tools, "Top", (0, 0, 30, 20), 5)
    assert (await adapter.save_file(files["part"])).is_success
    assert (await adapter.create_assembly()).is_success
    assert (await adapter.insert_component(files["part"], 0, 0, 0)).is_success
    assert (await adapter.save_file(files["assembly"])).is_success
    _close_new(adapter, before)
    return files


def _file_id(path: str) -> Any:
    try:
        status = os.stat(path)
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return {
        "st_dev": status.st_dev,
        "st_ino": status.st_ino,
        "mtime_ns": status.st_mtime_ns,
    }


async def _move_in(
    adapter: PyWin32Adapter, tools: dict[str, Any], folder: Path, before: set[str]
) -> dict[str, Any]:
    files = await _part_and_assembly(adapter, tools, folder, before)
    target = str(folder / "pd_handle_moved.SLDPRT")
    out: dict[str, Any] = {
        "sync_client": file_access.sync_client(files["part"]),
        "move_blocker": file_access.move_blocker(files["part"]),
        "unwritable_folder": file_access.unwritable_folder(str(folder)),
    }
    dry = await adapter.move_document(files["part"], target)
    out["dry_run"] = dry.data if dry.is_success else dry.error
    real = await adapter.move_document(files["part"], target, dry_run=False)
    out["move"] = real.data if real.is_success else real.error
    out["session_paths_has_target"] = saving.normalize(target) in saving.session_paths(
        adapter
    )
    out["saved_by_session"] = saving.saved_by_session(adapter, target)

    # MoveDocument raw on a closed copy, and a file id through a rename.
    copy, renamed = (
        str(folder / "pd_copy.SLDPRT"),
        str(folder / "pd_copy_renamed.SLDPRT"),
    )
    shutil.copy2(target if Path(target).exists() else files["part"], copy)
    app = adapter.swApp

    def raw_move() -> dict[str, Any]:
        from solidworks_mcp.adapters.solidworks.references import _string_array

        sw_type_info.flag_members(app, "MoveDocument")
        moved_to = str(folder / "pd_copy_raw.SLDPRT")
        result = _raw(
            lambda: app.MoveDocument(
                copy, moved_to, _string_array([]), _string_array([]), 0
            )
        )
        return {**result, "moved": Path(moved_to).exists(), "left": Path(copy).exists()}

    out["MoveDocument_raw"] = _on_com(adapter, "move_raw", raw_move)
    source = copy if Path(copy).exists() else str(folder / "pd_copy_raw.SLDPRT")
    out["file_id_before_rename"] = _file_id(source)
    os.rename(source, renamed)
    out["file_id_after_rename"] = _file_id(renamed)
    return out


async def step_move(
    adapter: PyWin32Adapter,
    tools: dict[str, Any],
    scratch: Path,
    cloud: Path | None,
    before: set[str],
) -> Any:
    """Settles MoveDocument and the fallback, locally and in a cloud-sync folder."""
    out: dict[str, Any] = {
        "local": await _move_in(adapter, tools, scratch / "move", before)
    }
    if cloud is not None:
        out["cloud"] = await _move_in(adapter, tools, cloud, before)
    return out


async def _save_after_rename_in(
    adapter: PyWin32Adapter, tools: dict[str, Any], folder: Path, before: set[str]
) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    made, renamed = folder / "pd_lid.SLDPRT", folder / "pd_lid_renamed.SLDPRT"
    assembly = str(folder / "pd_lid_assembly.SLDASM")
    assert (await adapter.create_part()).is_success
    await _block(tools, "Top", (0, 0, 20, 20), 4)
    assert (await adapter.save_file(str(made))).is_success
    _close_new(adapter, before)
    os.rename(made, renamed)  # as the Dipper session's mv did
    out: dict[str, Any] = {
        "moved_session_file": saving.moved_session_file(adapter, str(renamed))
    }
    assert (await adapter.create_assembly()).is_success
    assert (await adapter.insert_component(str(renamed), 0, 0, 0)).is_success
    assert (await adapter.save_file(assembly)).is_success
    opened = await adapter.open_model(str(renamed))
    out["open_part"] = opened.is_success or opened.error
    await _block(tools, "Top", (5, 5, 10, 10), 2)  # the part changes
    activated = await adapter.activate_document(assembly)
    out["activate_assembly"] = activated.is_success or activated.error
    saved = await adapter.save_file()
    out["save_assembly"] = (
        saved.data if saved.is_success else {"error": saved.error, "data": saved.data}
    )
    out["expected"] = "saved, not confirmation_required"
    return out


async def step_save_after_rename(
    adapter: PyWin32Adapter,
    tools: dict[str, Any],
    scratch: Path,
    cloud: Path | None,
    before: set[str],
) -> Any:
    """Settles the save guard for a renamed part of this session's."""
    out: dict[str, Any] = {
        "local": await _save_after_rename_in(adapter, tools, scratch / "rename", before)
    }
    _close_new(adapter, before)
    if cloud is not None:
        out["cloud"] = await _save_after_rename_in(
            adapter, tools, cloud / "rename", before
        )
    return out


# ---- the run -----------------------------------------------------------------------


async def run(cloud_root: Path | None) -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT / stamp
    scratch = out / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    cloud = cloud_root / f"probe_dipper_{stamp}" if cloud_root is not None else None
    report: dict[str, Any] = {
        "started": stamp,
        "cloud_folder": str(cloud) if cloud else None,
    }
    adapter = PyWin32Adapter({})
    await adapter.connect()
    before = _titles(adapter)
    try:
        tools = await _tools(adapter)
        steps: list[tuple[str, Callable[..., Any], tuple[Any, ...]]] = [
            ("cut_floor", step_cut_floor, ()),
            ("fillet_bar_up", step_fillet_bar_up, ()),
            ("fillet_loses_edge", step_fillet_loses_edge, ()),
            ("rectangle", step_rectangle, ()),
            ("contours", step_contours, ()),
            ("move", step_move, (scratch, cloud, before)),
            ("save_after_rename", step_save_after_rename, (scratch, cloud, before)),
        ]
        for key, step, args in steps:
            print(f"[probe] {key}: {step.__doc__}")
            try:
                report[key] = await step(adapter, tools, *args)
            except Exception as exc:  # noqa: BLE001 - a probe records every failure
                report[key] = {"error": repr(exc)}
            _close_new(adapter, before)
    finally:
        _close_new(adapter, before)
        await adapter.disconnect()
        shutil.rmtree(scratch, ignore_errors=True)
        if cloud is not None:
            shutil.rmtree(cloud, ignore_errors=True)
        path = out / "report.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"[probe] report: {path}")
        for key, value in report.items():
            if isinstance(value, dict) and "error" in value:
                print(f"[probe] {key} FAILED: {value['error']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cloud-folder",
        type=Path,
        default=None,
        help="A scratch folder inside a cloud-sync folder; a subfolder is made and removed",
    )
    args = parser.parse_args()
    if args.cloud_folder is not None and not args.cloud_folder.is_dir():
        parser.error(f"{args.cloud_folder} is not a folder")
    return asyncio.run(run(args.cloud_folder))


if __name__ == "__main__":
    sys.exit(main())
