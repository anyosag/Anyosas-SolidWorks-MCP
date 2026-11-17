"""Probe Save Bodies, the dialog it raises, and the documents it leaves loaded.

The ``dipper-files`` branch was written without SolidWorks (another session
was using it), from what The Dipper build (SOLIDWORKS 2026 SP4.1) reported:
every ``save_body_as_part`` hit a "SOLIDWORKS Design" Yes/No task dialog
whose text wasn't captured, wrote its file anyway, and left an untitled
assembly open and active; and parts loaded by a closed assembly kept their
``~$`` lock files. This probe runs the COM sequences the new code assumes on
scratch parts, raw first (to settle the assumptions), then through the
adapter methods and tools (to check them end to end). Each step says what it
settles; its findings go in the report.

The scratch files, built here in the report folder:

* ``ps_master``: a multibody part, two 10 mm blocks extruded unmerged
  (40 x 10 x 15 mm and 15 x 10 x 15 mm, 6000 and 2250 mm3);
* copies of it for each raw variant, and the derived parts Save Bodies writes.

Steps (``STEPS`` below, in order):

1. ``variants``: raw ``CreateSaveBodyFeature`` on a fresh copy of the master,
   inside ``_handle_com_operation`` so the dialog watchdog (and its new UI
   Automation text reader) runs, once per ``(AssemName, CopyCustomProperty)``:
   ``("", True)`` (the old tool's call), ``("", False)``, ``(None, False)``,
   and a real ``.sldasm`` path. For each: the error or result, the dialog's
   title, ``texts``, ``buttons`` and ``text_source``, the documents that
   appeared (title, path, type, visible), the active document, and whether
   the file was written. Settles: what the dialog says (and that UI
   Automation reads it), which argument raises it, whether ``""`` makes the
   untitled assembly, and whether ``None`` is accepted.
2. ``tool``: ``save_body_as_part`` (the adapter method) on a fresh copy.
   Settles: the fix end to end: success (a dialog only as ``warning``),
   ``closed_documents`` names the untitled assembly, the master is active
   again, ``file_check.holds_body`` is True.
3. ``definition``: ``get_save_bodies`` on that master, with the feature's
   ``GetTypeName2`` / ``GetTypeName`` and the master's ``GetSaveFlag``
   before and after. Settles: the type name (``SAVE_BODIES_TYPES``), whether
   ``GetSaveBodies`` needs ``AccessSelections`` (``selections_accessed``),
   and that reading leaves the part clean.
4. ``edit``: ``edit_save_bodies`` moving the body to a new file. Settles:
   ``RemoveSaveBodies`` + ``AddSaveBodies`` + ``ModifyDefinition`` work,
   ``AddSaveBodies`` writes the new file, and whether a dialog or an
   assembly comes with it.
5. ``resplit``: the master's first block made 5 mm taller
   (``set_dimension``), then the derived file's time and size checked (does
   a master rebuild rewrite it by itself?), then ``resplit_bodies``.
   Settles: a derived part rebuilt with the master open takes the new body
   (``holds_body``, 9000 mm3), ``Save3`` silent writes it, no dialog.
6. ``leftovers``: an assembly of the derived part, saved and closed while
   the master stays open, then ``GetDocuments`` against the
   ``GetFirstDocument`` walk, ``Visible``, the lock file,
   ``list_loaded_documents`` and ``close_leftover_documents``; again after
   the master is closed. Settles: which enumeration lists a part a closed
   assembly left, whether ``CloseDoc`` unloads it and its lock file goes,
   and whether the open master is what keeps it (``still_loaded``).
7. ``stl``: ``export_assembly_parts_stl`` (the tool) on the assembly
   reopened, with the user's STL options read before and after. Settles: a
   part only the assembly loaded gets a window, is exported and read back
   (closed mesh, ``volume_ratio`` near 1), and the options come back.

It opens files only through the adapter (``open_model``), where the dialog
watchdog runs, closes only the documents it opened, and deletes its files at
the end. The report stays in ``tests/.generated/probe_save_bodies/``.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    $env:PYTHONPATH = "src"
    .venv\\Scripts\\python.exe tests/scripts/probe_save_bodies.py
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import loaded_documents as ld
from solidworks_mcp.adapters.solidworks import save_bodies as sb
from solidworks_mcp.adapters.solidworks import stl_settings
from solidworks_mcp.adapters.solidworks.io import _variant_array

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_save_bodies"
VT_DISPATCH, VT_BSTR = 9, 8


async def _tools(adapter: PyWin32Adapter) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.export import register_export_tools
    from solidworks_mcp.tools.file_management import register_file_management_tools
    from solidworks_mcp.tools.modeling import register_modeling_tools
    from solidworks_mcp.tools.sketching import register_sketching_tools

    mcp = FastMCP("probe-save-bodies")
    await register_sketching_tools(mcp, adapter, {})
    await register_modeling_tools(mcp, adapter, {})
    await register_file_management_tools(mcp, adapter, {})
    await register_export_tools(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


async def _invoke(tools: dict[str, Any], name: str, args: dict[str, Any]) -> Any:
    """Call a tool; one with no input model takes keywords only."""
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


async def _master(adapter: PyWin32Adapter, tools: dict[str, Any], path: Path) -> None:
    """Two blocks on the Top plane, the second unmerged, saved to ``path``."""
    assert (await adapter.create_part()).is_success
    await _ok(tools, "create_sketch", {"plane": "Top"})
    await _ok(tools, "add_rectangle", {"x1": -45, "y1": -7.5, "x2": -5, "y2": 7.5})
    await _ok(tools, "exit_sketch", {})
    await _ok(tools, "create_extrusion", {"depth": 10})
    await _ok(tools, "create_sketch", {"plane": "Top"})
    await _ok(tools, "add_rectangle", {"x1": 5, "y1": -7.5, "x2": 20, "y2": 7.5})
    await _ok(tools, "exit_sketch", {})
    await _ok(tools, "create_extrusion", {"depth": 10, "merge_result": False})
    assert (await adapter.save_file(str(path))).is_success


async def _fresh(adapter: PyWin32Adapter, master: Path, name: str) -> Path:
    """A copy of the master, opened through the adapter (the watchdog runs)."""
    copy = master.with_name(f"{name}.SLDPRT")
    shutil.copyfile(master, copy)
    opened = await adapter.open_model(str(copy))
    assert opened.is_success, opened.error
    return copy


def _documents(adapter: PyWin32Adapter) -> dict[str, dict[str, Any]]:
    return {
        d.key: {
            "title": d.title,
            "path": d.path,
            "type": d.type,
            "visible": d.visible,
            "modified": d.modified,
        }
        for d in ld.loaded_documents(adapter)
    }


def _active_title(adapter: PyWin32Adapter) -> str | None:
    active = adapter.swApp.ActiveDoc
    return ld.describe(adapter, active).title if active is not None else None


def _close_new(adapter: PyWin32Adapter, before: set[str]) -> list[str]:
    closed = []
    for document in ld.loaded_documents(adapter):
        if document.key not in before:
            adapter._attempt(lambda t=document.title: adapter.swApp.CloseDoc(t))
            closed.append(document.title)
    return closed


def _file(path: Path) -> dict[str, Any]:
    return {
        "exists": path.exists(),
        "size": path.stat().st_size if path.exists() else 0,
        "mtime": path.stat().st_mtime if path.exists() else None,
        "lock_file": ld.lock_file(str(path)),
    }


# ---- the steps ---------------------------------------------------------------------


async def step_variants(adapter: PyWin32Adapter, master: Path) -> dict[str, Any]:
    """Settles what the dialog says, which argument raises it, and the stray assembly."""
    variants = {
        "empty_copy_props": ("", True),
        "empty_no_copy": ("", False),
        "none_no_copy": (None, False),
        "assembly_path": (str(master.with_name("ps_asm.SLDASM")), True),
    }
    out: dict[str, Any] = {}
    for key, (assembly, copy_props) in variants.items():
        start = set(_documents(adapter))
        copy = await _fresh(adapter, master, f"ps_{key}")
        target = copy.with_name(f"ps_{key}_body.SLDPRT")
        model = adapter.currentModel
        before = set(_documents(adapter))

        def _call(
            m: Any = model, a: Any = assembly, c: bool = copy_props, t: Path = target
        ) -> Any:
            bodies = sb.solid_bodies(adapter, m)
            manager = m.FeatureManager
            feature = manager.CreateSaveBodyFeature(
                _variant_array(VT_DISPATCH, [bodies[0][1]]),
                _variant_array(VT_BSTR, [str(t)]),
                a,
                False,
                c,
            )
            return {"feature": str(feature.Name) if feature is not None else None}

        result = adapter._handle_com_operation("probe_save_body", _call)
        after = _documents(adapter)
        out[key] = {
            "status": result.status.value,
            "error": result.error,
            "data": result.data,
            "new_documents": [v for k, v in after.items() if k not in before],
            "active": _active_title(adapter),
            "file": _file(target),
        }
        out[key]["closed"] = _close_new(adapter, start)
    return out


async def step_tool(adapter: PyWin32Adapter, master: Path) -> dict[str, Any]:
    """Settles the fixed save_body_as_part end to end."""
    copy = await _fresh(adapter, master, "ps_tool")
    target = copy.with_name("ps_tool_comb.SLDPRT")
    bodies = [name for name, _b in sb.solid_bodies(adapter, adapter.currentModel)]
    result = await adapter.save_body_as_part(bodies[0], str(target))
    return {
        "status": result.status.value,
        "error": result.error,
        "data": result.data,
        "warning": (result.metadata or {}).get("warning"),
        "active": _active_title(adapter),
        "documents": list(_documents(adapter).values()),
        "target": str(target),
    }


async def step_definition(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Settles the feature's type name, AccessSelections, and a clean read."""
    model = adapter.currentModel
    dirty_before = adapter._get_attr_or_call(model, "GetSaveFlag")
    feature = model.FeatureByName("Save Bodies1")
    types = {
        "GetTypeName2": adapter._attempt(lambda: feature.GetTypeName2()),
        "GetTypeName": adapter._attempt(lambda: feature.GetTypeName()),
    }
    result = await adapter.get_save_bodies()
    return {
        "types": types,
        "status": result.status.value,
        "error": result.error,
        "data": result.data,
        "dirty_before": dirty_before,
        "dirty_after": adapter._get_attr_or_call(model, "GetSaveFlag"),
    }


async def step_edit(adapter: PyWin32Adapter, master: Path) -> dict[str, Any]:
    """Settles that a new file is refused untouched, and which options stick.

    The first live run moved the body with RemoveSaveBodies + AddSaveBodies:
    both returned True and nothing changed. ``files`` is refused now; the
    body volume the feature saves is read before and after. Each option is
    then set alone through the tool (AccessSelections, ModifyDefinition),
    and raw without AccessSelections, and read back.
    """

    def definition() -> tuple[Any, Any]:
        model = adapter.currentModel
        (feature,) = sb.find_feature(adapter, model, "Save Bodies1")
        return feature, sb._definition(adapter, feature)

    def saved_volumes() -> list[Any]:
        _feature, data = definition()
        return [
            sb._mm3(sb.body_volume_m3(adapter, body))
            for body, _n, _p, _c in sb.entries(adapter, data)
        ]

    listed = await adapter.get_save_bodies("Save Bodies1")
    body = listed.data["features"][0]["bodies"][0]["body"]
    new = master.with_name("ps_tool_comb_v2.SLDPRT")
    before = saved_volumes()
    refused = await adapter.edit_save_bodies(
        "Save Bodies1", {"files": {body: str(new)}}
    )
    out: dict[str, Any] = {
        "files_refused": refused.error,
        "file": _file(new),
        "volumes_before_after": [before, saved_volumes()],
    }
    for key, value in (
        ("copy_custom_properties", True),
        ("consume_body", True),
        ("assembly_path", str(master.with_name("ps_edit.SLDASM"))),
    ):
        result = await adapter.edit_save_bodies("Save Bodies1", {key: value})
        out[f"tool {key}"] = {"status": result.status.value, "error": result.error}
    # Put them back, and the part active, for the steps after.
    await adapter.edit_save_bodies(
        "Save Bodies1", {"consume_body": False, "assembly_path": ""}
    )
    await adapter.activate_document(master.with_name("ps_tool.SLDPRT").stem)

    def raw() -> dict[str, Any]:
        model = adapter.currentModel
        feature, data = definition()
        data.CopyCustomProperties = True
        modified = feature.ModifyDefinition(data, model, sb._null())
        _f, again = definition()
        return {"modified": modified, "reads": again.CopyCustomProperties}

    out["raw without access"] = adapter._handle_com_operation(
        "probe_raw_edit", raw
    ).data
    out["expected"] = "files refused, no file, the same volumes; each option read back"
    return out


async def step_resplit(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Settles that a rebuilt derived part takes the master's new body, saved silently."""
    listed = await adapter.get_save_bodies()
    paths = [Path(b["file_path"]) for f in listed.data["features"] for b in f["bodies"]]
    before = {str(p): _file(p) for p in paths}
    changed = await adapter.set_dimension("D1@Boss-Extrude1", 15.0)
    after_rebuild = {str(p): _file(p) for p in paths}
    result = await adapter.resplit_bodies()
    return {
        "set_dimension": changed.data if changed.is_success else changed.error,
        "files_before": before,
        "files_after_master_rebuild": after_rebuild,
        "status": result.status.value,
        "error": result.error,
        "data": result.data,
        "warning": (result.metadata or {}).get("warning"),
        "files_after": {str(p): _file(p) for p in paths},
    }


async def step_leftovers(
    adapter: PyWin32Adapter, tools: dict[str, Any], master: Path
) -> dict[str, Any]:
    """Settles which enumeration lists a closed assembly's part, and CloseDoc on it."""
    listed = await adapter.get_save_bodies()
    derived = listed.data["features"][0]["bodies"][0]["file_path"]
    master_title = _active_title(adapter)
    assembly = master.with_name("ps_rack.SLDASM")
    assert (await adapter.create_assembly()).is_success
    assert (await adapter.insert_component(derived)).is_success
    assert (await adapter.save_file(str(assembly))).is_success
    assert (await adapter.close_model(False)).is_success

    def snapshot() -> dict[str, Any]:
        app = adapter.swApp
        return {
            "GetDocuments": [
                ld.describe(adapter, d).title for d in (app.GetDocuments() or [])
            ],
            "walk": [ld.describe(adapter, d).title for d in ld._walk(adapter, app)],
            "documents": list(_documents(adapter).values()),
            "lock_file": ld.lock_file(derived),
        }

    out: dict[str, Any] = {"after_assembly_closed": snapshot()}
    listing = await _invoke(tools, "list_open_documents", {"check_hidden": True})
    out["list_open_documents"] = listing
    closing = await adapter.close_leftover_documents()
    out["close_leftover_documents"] = closing.data or closing.error
    out["after_close"] = snapshot()
    if master_title:
        await adapter.activate_document(master_title)
        await adapter.close_model(False)
    out["after_master_closed"] = snapshot()
    closing = await adapter.close_leftover_documents()
    out["close_after_master"] = closing.data or closing.error
    out["at_end"] = snapshot()
    out["assembly"] = str(assembly)
    return out


async def step_stl(
    adapter: PyWin32Adapter, tools: dict[str, Any], assembly: Path
) -> dict[str, Any]:
    """Settles the per-part STL export from an assembly, and the options coming back."""
    app = adapter.swApp

    def options() -> dict[str, Any]:
        return {
            "quality": app.GetUserPreferenceIntegerValue(stl_settings.STL_QUALITY),
            "deviation": app.GetUserPreferenceDoubleValue(stl_settings.STL_DEVIATION),
            "angle": app.GetUserPreferenceDoubleValue(stl_settings.STL_ANGLE_TOLERANCE),
            "units": app.GetUserPreferenceIntegerValue(stl_settings.STL_UNITS),
            "binary": app.GetUserPreferenceToggle(stl_settings.STL_BINARY),
        }

    opened = await adapter.open_model(str(assembly))
    assert opened.is_success, opened.error
    before = options()
    reply = await _invoke(
        tools,
        "export_assembly_parts_stl",
        {"folder": str(assembly.parent / "stl"), "resolution": "fine"},
    )
    return {
        "reply": reply,
        "options_before": before,
        "options_after": options(),
        "active": _active_title(adapter),
    }


async def run() -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT / stamp
    scratch = out / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"started": stamp}
    adapter = PyWin32Adapter({})
    await adapter.connect()
    before = {d.key for d in ld.loaded_documents(adapter)}
    master = scratch / "ps_master.SLDPRT"
    try:
        tools = await _tools(adapter)
        await _master(adapter, tools, master)
        assert (await adapter.close_model(False)).is_success
        steps: list[tuple[str, Any]] = [
            ("variants", lambda: step_variants(adapter, master)),
            ("tool", lambda: step_tool(adapter, master)),
            ("definition", lambda: step_definition(adapter)),
            ("edit", lambda: step_edit(adapter, master)),
            ("resplit", lambda: step_resplit(adapter)),
            ("leftovers", lambda: step_leftovers(adapter, tools, master)),
            ("stl", lambda: step_stl(adapter, tools, scratch / "ps_rack.SLDASM")),
        ]
        for key, step in steps:
            print(f"[probe] {key}")
            try:
                report[key] = await step()
            except Exception as exc:  # noqa: BLE001 - a probe records every failure
                report[key] = {"error": repr(exc)}
    finally:
        for document in ld.loaded_documents(adapter):
            if document.key not in before:
                adapter._attempt(lambda t=document.title: adapter.swApp.CloseDoc(t))
        await adapter.disconnect()
        for path in scratch.rglob("*"):
            if path.is_file():
                try:
                    os.remove(path)
                except OSError:
                    pass
        path = out / "report.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"[probe] report: {path}")
        for key, value in report.items():
            if isinstance(value, dict) and "error" in value and value["error"]:
                print(f"[probe] {key}: {value['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
