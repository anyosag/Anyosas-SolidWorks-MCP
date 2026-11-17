"""Probe the COM sequences behind the round-two views: custom views, Z-up views, hidden components.

From "The Dipper" build (SOLIDWORKS 2026 SP4.1), written without SolidWorks
(another session was using it): ``export_image``'s ``view_direction`` / ``up``
/ ``up_axis`` (``adapters/solidworks/view_matrix.py`` and
``view_orientation.py``), ``hide_components`` and ``set_component_visibility``
(``component_visibility.py``), and ``check_interference``'s ``top_pairs``.
Each step runs raw first where the code assumes something, then through the
registered tools, and says what it settles.

The scratch geometry, modelled Z-up (sketches on Front: sketch (x, y) is model
(x, y); extrusions grow along +Z):

* the **tower**: 20 x 20 x 80, z 0..80, a 6 mm hole down its top on one
  side (x 5, y 5), so the pictures show which end is up;
* the **base**: 60 x 40 x 20, z 0..20; the **cover**: 60 x 40 x 3, placed
  at z 20 (resting on the base), then at z 19 (1 mm into it: 2400 mm3).

Steps (``run`` below, in order):

1. ``standard_views``: ``ShowNamedView2`` for each of the nine views, then
   ``IModelView.Orientation3.ArrayData``. Settles the convention every new
   view relies on: front, back, left, right, top, bottom and isometric should
   match ``view_matrix.standard_rows`` (columns = screen right, screen up,
   toward the viewer). Also settles trimetric 8 / dimetric 9: a dimetric
   view's toward-viewer vector has two equal components, a trimetric one none.
2. ``set_orientation``: ``Orientation3`` set to ``look_from([1, -1, 1],
   [0, 0, 1])`` through ``CreateTransform``, read back (settles that the
   property takes a transform and keeps it), a picture.
3. ``up_axis_flyout``: ``IModelView.UpAxisFlyout`` read, set to 1
   (``swViewOrientationUpAxisFlyout_Z_Up``), isometric shown and read, then
   put back. Settles whether SolidWorks' own Z-up option gives the same view
   as ``view_matrix.z_up`` (a native alternative), nothing else.
4. ``pictures``: ``export_image`` (BMP) of the tower in the standard
   isometric, the Z-up isometric, the Z-up front and ``view_direction``
   [1, 0, 0]. The tower's drawn box in each picture (pixels unlike the
   corner's): standing (taller than wide) in the Z-up ones, lying in the
   standard isometric. Settles that ``up_axis`` and ``view_direction`` turn
   the picture as their ``view_axes`` say.
5. ``zoom_box``: ``export_image`` with ``view_direction`` [0, -1, 0] and
   ``zoom_box_mm`` around the tower's top 20 mm. Settles pitfall #42 in a
   turned view: the drawn box should fill most of the picture's height.
6. ``visible_raw`` (assembly of base and cover): ``IComponent2.Visible``
   read, set to 0, read back, ``GetSaveFlag``; set to 1. Then the cover
   selected and ``HideComponent2``, ``Visible`` read, ``ShowComponent2``.
   Settles which call SolidWorks takes, and whether hiding marks the
   assembly modified.
7. ``tool_visibility``: ``set_component_visibility`` hides the cover
   (``method`` should be ``Visible``), a picture, shows it again.
8. ``export_hide``: ``export_image`` with ``hide_components`` [cover]; the
   cover is shown again afterwards (``Visible`` 1) and the picture differs
   from one with the cover. Then a picture that fails after hiding (a
   ``.xyz`` file: no format writes it): the cover must still come back.
9. ``persist``: the cover hidden with the tool, the assembly saved, closed
   through the adapter, opened again through the adapter: is the cover still
   hidden? Settles that ``Visible`` persists in the display state.
10. ``interference``: the cover moved 1 mm into the base,
    ``check_interference`` with ``components`` [cover]: ``top_pairs`` holds
    base / cover with about 2400 mm3.

It only touches documents it creates, closes only those, opens files through
the adapter only (a raw ``OpenDoc6`` of a saved assembly pops a SOLIDWORKS CAM
dialog), and deletes its scratch files at the end. The report and the
pictures, for a person to look at, stay in ``tests/.generated/probe_r2_views/``.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    $env:PYTHONPATH = "src"
    .venv\\Scripts\\python.exe tests/scripts/probe_r2_views.py
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters import sw_type_info
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import bitmap
from solidworks_mcp.adapters.solidworks import component_visibility as cv
from solidworks_mcp.adapters.solidworks import view_matrix as vm
from solidworks_mcp.adapters.solidworks import view_orientation as vo
from solidworks_mcp.adapters.solidworks.com_utils import double_array

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_r2_views"


async def _tools(adapter: PyWin32Adapter) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.analysis import register_analysis_tools
    from solidworks_mcp.tools.export import register_export_tools
    from solidworks_mcp.tools.modeling import register_modeling_tools
    from solidworks_mcp.tools.sketching import register_sketching_tools

    mcp = FastMCP("probe-r2-views")
    for register in (
        register_sketching_tools,
        register_modeling_tools,
        register_export_tools,
        register_analysis_tools,
    ):
        await register(mcp, adapter, {})
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


async def _box(
    tools: dict[str, Any], rect: tuple[float, float, float, float], depth: float
) -> None:
    """A rectangle on Front (model x, y), extruded ``depth`` mm along +Z."""
    x1, y1, x2, y2 = rect
    await _ok(tools, "create_sketch", {"plane": "Front"})
    await _ok(tools, "add_rectangle", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})
    await _ok(tools, "exit_sketch", {})
    await _ok(tools, "create_extrusion", {"depth": depth})


def _flat(rows: Any) -> list[float] | None:
    return [round(float(x), 6) for row in rows for x in row] if rows else None


def _view(adapter: PyWin32Adapter) -> Any:
    return adapter.currentModel.ActiveView


def _drawn_box(path: Path) -> dict[str, Any] | None:
    """The box of the model's outline (near-black pixels), and its height / width.

    The background is a grey gradient, so a pixel unlike the corner's is not
    the model (the first run measured the whole picture); the edges are
    drawn black and nothing else is (a plane's label is blue).
    """
    if not path.exists():
        return None
    width, height, rows = bitmap.read_bmp(path)
    xs, ys = [], []
    for y in range(height):
        row = rows[y]
        for x in range(width):
            if max(row[3 * x : 3 * x + 3]) < 64:
                xs.append(x)
                ys.append(y)
    if not xs:
        return {"empty": True}
    w, h = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
    return {
        "box_px": [min(xs), min(ys), max(xs), max(ys)],
        "height_over_width": round(h / w, 3),
        "fills_height": round(h / height, 3),
        # How far the outline's middle is from the picture's, in picture widths
        # (the FeatureManager pane shifted it about 0.33 to the right).
        "off_centre": round(((min(xs) + max(xs)) / 2 - width / 2) / width, 3),
    }


def _difference(a: Path, b: Path) -> float | None:
    """The share of sampled pixels that differ between two BMPs."""
    if not (a.exists() and b.exists()):
        return None
    _wa, _ha, rows_a = bitmap.read_bmp(a)
    _wb, _hb, rows_b = bitmap.read_bmp(b)
    if len(rows_a) != len(rows_b):
        return 1.0
    total = changed = 0
    for row_a, row_b in zip(rows_a[::8], rows_b[::8], strict=True):
        for i in range(0, min(len(row_a), len(row_b)), 24):
            total += 1
            changed += row_a[i : i + 3] != row_b[i : i + 3]
    return round(changed / total, 4) if total else None


def _save_flag(adapter: PyWin32Adapter) -> Any:
    model = adapter.currentModel
    sw_type_info.flag_members(model, "GetSaveFlag")
    return adapter._attempt(lambda: model.GetSaveFlag())


_SHOT = {"width": 800, "height": 600}


# ---- the steps on the tower part ----------------------------------------------------


def step_standard_views(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Settles Orientation3's convention, and which id is trimetric and dimetric."""
    model = adapter.currentModel
    found: dict[str, Any] = {}
    for name, view_id in vm.STANDARD_VIEW_IDS.items():
        model.ShowNamedView2("", view_id)
        rows = vo.read_rows(adapter, model)
        expected = vm.standard_rows(name)
        entry: dict[str, Any] = {
            "id": view_id,
            "read": _flat(rows),
            "axes": vm.axes(rows) if rows else None,
        }
        if expected is not None:
            entry["matches_table"] = bool(rows) and vm.same(rows, expected, 1e-4)
        elif rows:
            toward = [abs(v) for v in vm.axes(rows)["toward_viewer"]]
            entry["equal_components"] = sum(
                1
                for i in range(3)
                for j in range(i + 1, 3)
                if abs(toward[i] - toward[j]) < 1e-3
            )
            entry["expected"] = "1 for dimetric, 0 for trimetric"
        found[name] = entry
    return found


def step_set_orientation(adapter: PyWin32Adapter, out: Path) -> dict[str, Any]:
    """Settles that Orientation3 takes a CreateTransform and keeps it."""
    model = adapter.currentModel
    view = _view(adapter)
    wanted = vm.look_from([1, -1, 1], [0, 0, 1])
    before = view.Orientation3.ArrayData
    utility = adapter.swApp.GetMathUtility()
    sw_type_info.flag_members(utility, "CreateTransform")
    view.Orientation3 = utility.CreateTransform(
        double_array(vm.with_rows(before, wanted))
    )
    after = vo.read_rows(adapter, model)
    sw_type_info.flag_members(model, "ViewZoomtofit2", "SaveBMP")
    model.ViewZoomtofit2()
    picture = out / "set_orientation.bmp"
    model.SaveBMP(str(picture), 800, 600)
    return {
        "array_before": [round(float(v), 6) for v in before],
        "read_back": _flat(after),
        "kept": bool(after) and vm.same(after, wanted, 1e-6),
        "picture": str(picture),
        "expected": "kept True; the tower standing, its holed top up",
    }


def step_up_axis_flyout(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Settles whether IModelView.UpAxisFlyout = Z_Up gives view_matrix.z_up's views."""
    model = adapter.currentModel
    view = _view(adapter)
    original = adapter._attempt(lambda: view.UpAxisFlyout)
    report: dict[str, Any] = {"before": original}
    try:
        view.UpAxisFlyout = 1
        report["set"] = adapter._attempt(lambda: view.UpAxisFlyout)
        model.ShowNamedView2("", vm.STANDARD_VIEW_IDS["isometric"])
        rows = vo.read_rows(adapter, model)
        report["isometric"] = _flat(rows)
        report["same_as_z_up"] = bool(rows) and vm.same(
            rows, vm.z_up(vm.standard_rows("isometric")), 1e-4
        )
        # The isometric differed (it looks from +X +Y +Z); the others settle
        # which way SolidWorks' own Z-up front faces.
        for name in ("front", "top", "right"):
            model.ShowNamedView2("", vm.STANDARD_VIEW_IDS[name])
            native = vo.read_rows(adapter, model)
            report[name] = {
                "axes": vm.axes(native) if native else None,
                "same_as_z_up": bool(native)
                and vm.same(native, vm.z_up(vm.standard_rows(name)), 1e-4),
            }
    except Exception as exc:  # noqa: BLE001 - a probe records every failure
        report["error"] = repr(exc)
    finally:
        if original is not None:
            adapter._attempt(lambda: setattr(view, "UpAxisFlyout", original))
        report["after"] = adapter._attempt(lambda: view.UpAxisFlyout)
    return report


async def step_pictures(tools: dict[str, Any], out: Path) -> dict[str, Any]:
    """Settles that up_axis and view_direction turn the picture as view_axes say."""
    shots = {
        "standard_isometric": {"view_orientation": "isometric"},
        "z_up_isometric": {"view_orientation": "isometric", "up_axis": "z"},
        "z_up_front": {"view_orientation": "front", "up_axis": "z"},
        "z_up_dimetric": {"view_orientation": "dimetric", "up_axis": "z"},
        "from_plus_x": {"view_direction": [1, 0, 0], "up": [0, 0, 1]},
    }
    report: dict[str, Any] = {}
    for name, args in shots.items():
        path = out / f"tower_{name}.bmp"
        reply = await _invoke(
            tools, "export_image", {**_SHOT, **args, "file_path": str(path)}
        )
        data = reply.get("data") or {}
        report[name] = {
            "status": reply.get("status"),
            "message": reply.get("message"),
            "view_axes": data.get("view_axes"),
            "drawn": _drawn_box(path),
            "picture": str(path),
        }
    report["expected"] = (
        "height_over_width > 1 for every z_up picture and from_plus_x, < 1 for "
        "standard_isometric; view_axes up is +Z; off_centre within 0.05"
    )
    return report


async def step_zoom_box(tools: dict[str, Any], out: Path) -> dict[str, Any]:
    """Settles pitfall #42's zoom box in a view turned by view_direction."""
    path = out / "tower_top_zoom.bmp"
    reply = await _invoke(
        tools,
        "export_image",
        {
            **_SHOT,
            "file_path": str(path),
            "view_direction": [0, -1, 0],
            "up": [0, 0, 1],
            "zoom_box_mm": [-10, -10, 60, 10, 10, 80],
        },
    )
    data = reply.get("data") or {}
    return {
        "status": reply.get("status"),
        "zoom_box_view_mm": data.get("zoom_box_view_mm"),
        "drawn": _drawn_box(path),
        "picture": str(path),
        "expected": "zoom_box_view_mm [-10, 60, -10, 10, 80, 10]; fills_height > 0.8",
    }


# ---- the steps on the assembly ------------------------------------------------------


def _cover(adapter: PyWin32Adapter, name: str) -> Any:
    return cv.find(adapter, adapter.currentModel, [name])[name]


def step_visible_raw(adapter: PyWin32Adapter, cover_name: str) -> dict[str, Any]:
    """Settles which hide call SolidWorks takes, and whether it marks the assembly modified."""
    model = adapter.currentModel
    cover = _cover(adapter, cover_name)
    report: dict[str, Any] = {
        "visible_before": cover.Visible,
        "save_flag_before": _save_flag(adapter),
    }
    try:
        cover.Visible = cv.HIDDEN
        report["visible_after_put_0"] = cover.Visible
        report["save_flag_after_hide"] = _save_flag(adapter)
    except Exception as exc:  # noqa: BLE001
        report["put_error"] = repr(exc)
    adapter._attempt(lambda: setattr(cover, "Visible", cv.VISIBLE))
    report["visible_after_put_1"] = cover.Visible
    sw_type_info.flag_members(model, "HideComponent2", "ShowComponent2")
    model.ClearSelection2(True)
    data = sw_type_info.flagged(
        model.SelectionManager, "ISelectionMgr"
    ).CreateSelectData()
    report["selected"] = cover.Select4(False, data, False)
    try:
        model.HideComponent2()
        report["visible_after_hide_component2"] = cover.Visible
        model.ShowComponent2()
        report["visible_after_show_component2"] = cover.Visible
    except Exception as exc:  # noqa: BLE001
        report["hide_component2_error"] = repr(exc)
    model.ClearSelection2(True)
    report["expected"] = (
        "visible_after_put_0 0 (Visible works) and 1 after; same for HideComponent2"
    )
    return report


async def step_tool_visibility(
    adapter: PyWin32Adapter, tools: dict[str, Any], cover_name: str, out: Path
) -> dict[str, Any]:
    """set_component_visibility hides the cover, a picture, shows it again."""
    hidden = await _invoke(
        tools,
        "set_component_visibility",
        {"components": [cover_name], "visible": False},
    )
    path = out / "assembly_cover_hidden_by_tool.bmp"
    await _invoke(
        tools,
        "export_image",
        {
            **_SHOT,
            "file_path": str(path),
            "view_orientation": "isometric",
            "up_axis": "z",
        },
    )
    shown = await _invoke(
        tools, "set_component_visibility", {"components": [cover_name], "visible": True}
    )
    return {
        "hidden": hidden.get("data"),
        "shown": shown.get("data"),
        "visible_now": _cover(adapter, cover_name).Visible,
        "picture": str(path),
        "expected": "hidden method Visible, after False; visible_now 1",
    }


async def step_export_hide(
    adapter: PyWin32Adapter, tools: dict[str, Any], cover_name: str, out: Path
) -> dict[str, Any]:
    """hide_components hides for one picture; the cover comes back, also on failure."""
    shot = {**_SHOT, "view_orientation": "isometric", "up_axis": "z"}
    with_cover = out / "assembly_with_cover.bmp"
    without = out / "assembly_cover_off.bmp"
    await _invoke(tools, "export_image", {**shot, "file_path": str(with_cover)})
    flag_before = _save_flag(adapter)
    reply = await _invoke(
        tools,
        "export_image",
        {**shot, "file_path": str(without), "hide_components": [cover_name]},
    )
    report: dict[str, Any] = {
        "status": reply.get("status"),
        "hidden_components": (reply.get("data") or {}).get("hidden_components"),
        "visible_after": _cover(adapter, cover_name).Visible,
        "save_flag_before": flag_before,
        "save_flag_after": _save_flag(adapter),
        "changed_pixels": _difference(with_cover, without),
        "pictures": [str(with_cover), str(without)],
    }
    failed = await _invoke(
        tools,
        "export_image",
        {**shot, "file_path": str(out / "fails.xyz"), "hide_components": [cover_name]},
    )
    report["failed_status"] = failed.get("status")
    report["failed_message"] = failed.get("message")
    report["visible_after_failure"] = _cover(adapter, cover_name).Visible
    report["expected"] = (
        "visible_after 1, changed_pixels > 0.02, visible_after_failure 1"
    )
    return report


async def step_persist(
    adapter: PyWin32Adapter, tools: dict[str, Any], cover_name: str, path: Path
) -> dict[str, Any]:
    """Settles whether a hidden component stays hidden after a save and a reopen."""
    await _ok(
        tools,
        "set_component_visibility",
        {"components": [cover_name], "visible": False},
    )
    saved = await adapter.save_file(str(path))
    closed = await adapter.close_model()
    opened = await adapter.open_model(str(path))
    report = {
        "saved": saved.is_success,
        "closed": closed.is_success,
        "opened": opened.is_success or opened.error,
        "visible_after_reopen": cv.is_visible(adapter, _cover(adapter, cover_name)),
        "expected": "visible_after_reopen False",
    }
    await _ok(
        tools, "set_component_visibility", {"components": [cover_name], "visible": True}
    )
    return report


async def step_interference(tools: dict[str, Any], cover_name: str) -> dict[str, Any]:
    """check_interference names base / cover in top_pairs, about 2400 mm3."""
    await _ok(
        tools,
        "set_component_transform",
        {"component": cover_name, "origin_mm": [0, 0, 19]},
    )
    reply = await _invoke(tools, "check_interference", {"components": [cover_name]})
    data = reply.get("data") or {}
    return {
        "message": reply.get("message"),
        "interference_count": data.get("interference_count"),
        "top_pairs": data.get("top_pairs"),
        "expected": "one pair, base / cover, volume_mm3 about 2400",
    }


# ---- the run -----------------------------------------------------------------------

Step = Callable[..., Any]


async def _record(report: dict[str, Any], key: str, step: Step, *args: Any) -> None:
    print(f"[probe] {key}: {step.__doc__}")
    try:
        found = step(*args)
        report[key] = await found if inspect.isawaitable(found) else found
    except Exception as exc:  # noqa: BLE001 - a probe records every failure
        report[key] = {"error": repr(exc)}


async def run() -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT / stamp
    scratch = out / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"started": stamp}
    adapter = PyWin32Adapter({})
    await adapter.connect()
    before = {
        str(adapter._get_attr_or_call(d, "GetTitle"))
        for d in adapter.swApp.GetDocuments() or []
    }
    base_path, cover_path = scratch / "pr_base.SLDPRT", scratch / "pr_cover.SLDPRT"
    assembly_path = scratch / "pr_views.SLDASM"
    try:
        tools = await _tools(adapter)
        # The tower, with a hole down its top so the pictures show which end is up.
        assert (await adapter.create_part()).is_success
        await _box(tools, (-10, -10, 10, 10), 80)
        await _ok(
            tools, "create_reference_plane", {"reference": "Front Plane", "offset": 80}
        )
        await _ok(tools, "create_sketch", {"plane": "Plane1"})
        await _ok(tools, "add_circle", {"center_x": 5, "center_y": 5, "radius": 3})
        await _ok(tools, "exit_sketch", {})
        report["hole"] = await _invoke(tools, "create_cut_extrude", {"depth": 10})
        await _record(report, "standard_views", step_standard_views, adapter)
        await _record(report, "set_orientation", step_set_orientation, adapter, out)
        await _record(report, "up_axis_flyout", step_up_axis_flyout, adapter)
        await _record(report, "pictures", step_pictures, tools, out)
        await _record(report, "zoom_box", step_zoom_box, tools, out)
        # The base and the cover as parts, then an assembly of the two.
        for path, depth in ((base_path, 20), (cover_path, 3)):
            assert (await adapter.create_part()).is_success
            await _box(tools, (-30, -20, 30, 20), depth)
            assert (await adapter.save_file(str(path))).is_success
        assert (await adapter.create_assembly()).is_success
        report["inserted"] = [
            (await adapter.insert_component(str(base_path), 0, 0, 0)).data,
            (await adapter.insert_component(str(cover_path), 0, 0, 20)).data,
        ]
        names = (await adapter.list_components()).data or []
        cover_name = next(n for n in names if n.lower().startswith("pr_cover"))
        report["components"] = names
        await _record(report, "visible_raw", step_visible_raw, adapter, cover_name)
        await _record(
            report,
            "tool_visibility",
            step_tool_visibility,
            adapter,
            tools,
            cover_name,
            out,
        )
        await _record(
            report, "export_hide", step_export_hide, adapter, tools, cover_name, out
        )
        await _record(
            report, "persist", step_persist, adapter, tools, cover_name, assembly_path
        )
        await _record(report, "interference", step_interference, tools, cover_name)
    finally:
        for title in {
            str(adapter._get_attr_or_call(d, "GetTitle"))
            for d in adapter.swApp.GetDocuments() or []
        } - before:
            adapter._attempt(lambda t=title: adapter.swApp.CloseDoc(t))
        await adapter.disconnect()
        for path in scratch.glob("*"):
            try:
                path.unlink()
            except OSError:
                pass
        path = out / "report.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"[probe] report and pictures: {out}")
        for key, value in report.items():
            if isinstance(value, dict) and "error" in value:
                print(f"[probe] {key} FAILED: {value['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
