"""Probe the COM behaviour behind the Dipper's second-round fixes (branch ``r2-fixes``).

The fixes were written without SolidWorks (other sessions were using it).
Each COM fact they rest on is checked here on scratch parts built through
``PyWin32Adapter`` and the registered tools; each step says what it
settles, and its findings go in the report.

Steps, in order:

1. ``empty_sketch``: a part; ``create_sketch`` on Top, then ``exit_sketch``
   with nothing drawn; ``FeatureByName`` on the sketch while it is open and
   after; then a sketch holding one point only (``CreatePoint``). Settles:
   the open sketch is in the tree while edited, an empty one is gone after
   the exit (pitfall #45) and the reply says ``discarded``, and whether a
   sketch with just a point is kept (``sketch_is_empty`` counts points).
2. ``body_split``: a 60 x 10 x 5 strip on Top and a 20 x 10 boss beside it
   (one body); a through-all cut of a 2 mm circle over the boss, made with
   ``feature_scope`` and ``auto_select`` on, so it is scoped to the body it
   meets; then ``edit_sketch`` on the boss's sketch, every entity deleted,
   the outline drawn again 2.5 mm short of the strip, ``exit_sketch``, and
   ``rebuild_model``. Records ``GetBodies2`` before, during the edit and
   after; the cut definition's ``FeatureScope``, ``AutoSelect``,
   ``FeatureScopeBodies`` and ``GetFeatureScopeBodiesCount`` before and after
   the split, read without ``AccessSelections`` and then with it (and
   ``GetSaveFlag`` around each); the cut's reply. Settles: the body count
   across the sketch round trip (counted before ``EditSketch``, since the
   part shows rolled back while a sketch is edited), that
   ``exit_sketch`` warns of the split, whether the scoped cut then fails
   ``swFeatureErrorCutNotIntersectModel`` (71) and ``rebuild_model`` names its
   bodies, whether the scope bodies read without ``AccessSelections``, and
   ``cut_toward`` in the cut's message.
3. ``save_flags``: a part saved by the adapter, then a forced rebuild, then a
   real edit (a boss), then a custom property; at each, ``GetSaveFlag``,
   ``IModelDocExtension.NeedsRebuild2`` and ``IModelDoc2.GetUpdateStamp``.
   Then an assembly of a second part made here, the part changed, the
   session's memory cleared (a restart), and ``save_file`` on the assembly.
   Settles: whether the update stamp stays put through a save and a rebuild
   and moves on a model edit and a property edit (the save guard counts an
   unchanged stamp as "only a flag"), what ``NeedsRebuild2`` reads, and that
   the save guard keeps a part this server made as its own after a restart
   (no ``confirmation_required``).

The ledger used is a scratch one, not the user's. It only touches documents
it creates and closes only those (without saving), deletes its files at the
end, and leaves the user's documents as they were. The report stays in
``tests/.generated/probe_r2_fixes/``.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    $env:PYTHONPATH = "src"
    .venv\\Scripts\\python.exe tests/scripts/probe_r2_fixes.py
"""

from __future__ import annotations

import asyncio
import inspect
import json
import shutil
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters import sw_type_info
from solidworks_mcp.adapters.base import ExtrusionParameters
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import bodies, saving
from solidworks_mcp.adapters.solidworks.file_ledger import FileLedger

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_r2_fixes"


async def _tools(adapter: PyWin32Adapter) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.file_management import register_file_management_tools
    from solidworks_mcp.tools.modeling import register_modeling_tools
    from solidworks_mcp.tools.sketching import register_sketching_tools

    mcp = FastMCP("probe-r2-fixes")
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
) -> dict[str, Any]:
    """A rectangle (sketch x1, y1, x2, y2 in mm) on ``plane``, extruded."""
    sketch = await _ok(tools, "create_sketch", {"plane": plane})
    x1, y1, x2, y2 = rect
    await _ok(tools, "add_rectangle", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})
    await _ok(tools, "exit_sketch", {})
    await _ok(tools, "create_extrusion", {"depth": depth})
    return sketch


def _bodies(adapter: PyWin32Adapter) -> Any:
    return bodies.solid_body_names(adapter, adapter.currentModel)


def _flags(adapter: PyWin32Adapter) -> dict[str, Any]:
    """GetSaveFlag, NeedsRebuild2 and GetUpdateStamp of the active document, raw."""
    model = adapter.currentModel
    sw_type_info.flag_members(model, "GetSaveFlag", "GetUpdateStamp")
    return {
        "GetSaveFlag": _raw(lambda: model.GetSaveFlag()),
        "NeedsRebuild2": _raw(lambda: model.Extension.NeedsRebuild2),
        "GetUpdateStamp": _raw(lambda: model.GetUpdateStamp()),
    }


def _scope(adapter: PyWin32Adapter, cut: str) -> dict[str, Any]:
    """A cut's feature scope, read without and then with AccessSelections."""
    model = adapter.currentModel
    feature = model.FeatureByName(cut)
    sw_type_info.flag_members(feature, "GetDefinition")
    data = feature.GetDefinition()
    sw_type_info.flag_members(
        data, "GetFeatureScopeBodiesCount", "AccessSelections", "ReleaseSelectionAccess"
    )

    def read() -> dict[str, Any]:
        raw = _raw(lambda: data.FeatureScopeBodies)
        names = [
            str(_raw(lambda b=bodies._wrapped(adapter, b): b.Name).get("value"))
            for b in (raw.get("value") or ())
        ]
        return {
            "FeatureScope": _raw(lambda: data.FeatureScope),
            "AutoSelect": _raw(lambda: data.AutoSelect),
            "FeatureScopeBodies": {"raw_type": raw.get("type"), "names": names},
            "GetFeatureScopeBodiesCount": _raw(
                lambda: data.GetFeatureScopeBodiesCount()
            ),
            "save_flag": _flags(adapter)["GetSaveFlag"],
        }

    out = {"without_access": read()}
    out["AccessSelections"] = _raw(lambda: data.AccessSelections(model, None))
    out["with_access"] = read()
    out["ReleaseSelectionAccess"] = _raw(lambda: data.ReleaseSelectionAccess())
    out["after_release"] = _flags(adapter)
    return out


# ---- the steps ---------------------------------------------------------------------


async def step_empty_sketch(adapter: PyWin32Adapter, tools: dict[str, Any]) -> Any:
    """Settles that an empty sketch is discarded on exit, and one with a point."""
    assert (await adapter.create_part()).is_success
    out: dict[str, Any] = {}
    opened = await _ok(tools, "create_sketch", {"plane": "Top"})
    name = opened["sketch"]["name"]
    out["create_sketch"] = opened
    out["found_while_open"] = _on_com(
        adapter, "open", lambda: adapter.currentModel.FeatureByName(name) is not None
    )
    out["exit_sketch"] = await _invoke(tools, "exit_sketch", {})
    out["found_after"] = _on_com(
        adapter, "after", lambda: adapter.currentModel.FeatureByName(name) is not None
    )
    out["expected"] = "found_while_open True, discarded True, found_after False"

    point = await _ok(tools, "create_sketch", {"plane": "Front"})
    out["point_only"] = {
        "CreatePoint": _on_com(
            adapter,
            "point",
            lambda: (
                adapter.currentSketchManager.CreatePoint(0.01, 0.01, 0.0) is not None
            ),
        ),
        "exit_sketch": await _invoke(tools, "exit_sketch", {}),
        "found_after": _on_com(
            adapter,
            "point_after",
            lambda: (
                adapter.currentModel.FeatureByName(point["sketch"]["name"]) is not None
            ),
        ),
    }
    return out


async def step_body_split(adapter: PyWin32Adapter, tools: dict[str, Any]) -> Any:
    """Settles the body count across an edit, the scoped cut's error and its scope."""
    assert (await adapter.create_part()).is_success
    await _block(tools, "Top", (0, 0, 60, 10), 5)
    boss = await _block(tools, "Top", (60, 0, 80, 10), 5)
    boss_sketch = boss["sketch"]["name"]
    out: dict[str, Any] = {
        "bodies_start": _on_com(adapter, "b0", lambda: _bodies(adapter))
    }

    await _ok(tools, "create_sketch", {"plane": "Top"})
    await _ok(tools, "add_circle", {"center_x": 70, "center_y": 5, "radius": 1})
    await _ok(tools, "exit_sketch", {})
    params = ExtrusionParameters(
        depth=10.0,
        end_condition="ThroughAllBoth",
        feature_scope=True,
        auto_select=True,
    )
    made = await adapter.create_cut_extrude(params)
    out["scoped_cut"] = made.data.parameters if made.is_success else made.error
    cut = made.data.name if made.is_success else None
    if cut:
        out["scope_before_split"] = _on_com(adapter, "s0", lambda: _scope(adapter, cut))

    out["edit_sketch"] = await _ok(tools, "edit_sketch", {"name": boss_sketch})
    out["bodies_while_editing"] = _on_com(adapter, "b1", lambda: _bodies(adapter))
    await _ok(tools, "delete_sketch_entities", {"all": True})
    await _ok(tools, "add_rectangle", {"x1": 62.5, "y1": 0, "x2": 80, "y2": 10})
    out["exit_sketch"] = await _invoke(tools, "exit_sketch", {})
    out["bodies_after"] = _on_com(adapter, "b2", lambda: _bodies(adapter))
    out["rebuild_model"] = await _invoke(tools, "rebuild_model", {})
    if cut:
        out["scope_after_split"] = _on_com(adapter, "s1", lambda: _scope(adapter, cut))
    # A blind cut down into the strip from Top: its message names the side.
    await _ok(tools, "create_sketch", {"plane": "Top"})
    await _ok(tools, "add_circle", {"center_x": 30, "center_y": 5, "radius": 1})
    await _ok(tools, "exit_sketch", {})
    out["plain_cut"] = await _invoke(tools, "create_cut_extrude", {"depth": 2.0})
    out["expected"] = (
        "exit_sketch: solid_body_count 1 -> 2 with a warning; rebuild_model: the "
        "cut fails code 71 with scope naming its body"
    )
    return out


async def step_save_flags(
    adapter: PyWin32Adapter, tools: dict[str, Any], scratch: Path, before: set[str]
) -> Any:
    """Settles GetUpdateStamp and NeedsRebuild2, and the guard after a restart."""
    adapter._file_ledger = FileLedger(scratch / "ledger.json")  # not the user's
    out: dict[str, Any] = {}
    assert (await adapter.create_part()).is_success
    await _block(tools, "Top", (0, 0, 20, 20), 4)
    part = str(scratch / "pr_flags.SLDPRT")
    assert (await adapter.save_file(part)).is_success
    out["after_save"] = _on_com(adapter, "f0", lambda: _flags(adapter))
    model = adapter.currentModel
    out["ForceRebuild3"] = _on_com(adapter, "rb", lambda: model.ForceRebuild3(False))
    out["after_rebuild"] = _on_com(adapter, "f1", lambda: _flags(adapter))
    await _block(tools, "Top", (5, 5, 10, 10), 2)
    out["after_edit"] = _on_com(adapter, "f2", lambda: _flags(adapter))
    assert (await adapter.save_file()).is_success
    out["after_second_save"] = _on_com(adapter, "f3", lambda: _flags(adapter))

    def add_property() -> Any:
        manager = model.Extension.CustomPropertyManager("")
        return manager.Add3("pr_probe", 30, "x", 2)

    out["Add3"] = _on_com(adapter, "prop", add_property)
    out["after_property"] = _on_com(adapter, "f4", lambda: _flags(adapter))
    _close_new(adapter, before)

    made = str(scratch / "pr_cage.SLDPRT")
    assembly = str(scratch / "pr_cradle.SLDASM")
    assert (await adapter.create_part()).is_success
    await _block(tools, "Top", (0, 0, 10, 10), 3)
    assert (await adapter.save_file(made)).is_success
    assert (await adapter.create_assembly()).is_success
    assert (await adapter.insert_component(made, 0, 0, 0)).is_success
    assert (await adapter.save_file(assembly)).is_success
    opened = await adapter.open_model(made)
    out["open_part"] = opened.is_success or opened.error
    await _block(tools, "Top", (2, 2, 4, 4), 1)  # the part changes
    # A restart: the server's memory is gone, the ledger is not.
    for attribute in ("_session_paths", "_session_saves", "_session_files"):
        setattr(adapter, attribute, None)
    out["ledger_entry"] = adapter._file_ledger.unchanged_entry(made)
    out["solidworks_process"] = saving.solidworks_process(adapter)
    activated = await adapter.activate_document(assembly)
    out["activate_assembly"] = activated.is_success or activated.error
    saved = await adapter.save_file()
    out["save_assembly"] = (
        saved.data if saved.is_success else {"error": saved.error, "data": saved.data}
    )
    out["expected"] = (
        "stamp equal after_save/after_rebuild, different after_edit and "
        "after_property; save_assembly saved, not confirmation_required"
    )
    return out


# ---- the run -----------------------------------------------------------------------


async def run() -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT / stamp
    scratch = out / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"started": stamp}
    adapter = PyWin32Adapter({})
    await adapter.connect()
    before = _titles(adapter)
    try:
        tools = await _tools(adapter)
        steps: list[tuple[str, Callable[..., Any], tuple[Any, ...]]] = [
            ("empty_sketch", step_empty_sketch, ()),
            ("body_split", step_body_split, ()),
            ("save_flags", step_save_flags, (scratch, before)),
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
        path = out / "report.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"[probe] report: {path}")
        for key, value in report.items():
            if isinstance(value, dict) and "error" in value:
                print(f"[probe] {key} FAILED: {value['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
