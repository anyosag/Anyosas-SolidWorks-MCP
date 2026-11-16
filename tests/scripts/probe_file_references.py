"""Probe the calls behind ``list_references`` and ``move_document``.

Neither has run on real SolidWorks yet (``lid-readonly`` branch). It builds,
through ``PyWin32Adapter``, a scratch part (``pf_bracket``), an assembly
holding it (``pf_frame``) and a drawing of the assembly (``pf_frame``
drawing), saves them in ``tests/.generated/probe_file_references/`` and
closes them all. Then it records:

1. **GetDocumentDependencies2** on each closed file, with ``traverse``
   off and on (search off, no read-only info): the shape of the reply (flat
   name/path pairs?), and whether the drawing lists the part directly.
2. **MoveDocument** with no children, on the closed part, trying the
   children arguments in turn until one marshals: an empty
   ``VT_ARRAY | VT_BSTR`` SAFEARRAY, ``None``, an empty Python list. The
   return code (``swMoveCopyError_e``), and whether the file moved.
3. **ReplaceReferencedDocument** on the closed assembly and drawing, old
   path -> new path: the return value, and what ``GetDocumentDependencies2``
   reads afterwards; with the old path in other letter case too.
4. **Opening the repointed assembly** (``OpenDoc6`` silent, read-only): its
   errors and warnings, so a repoint that SolidWorks can't follow shows.
5. **MoveDocument on an open, unmodified part**: does it move a file
   SolidWorks holds?
6. ``move_document`` itself, dry run and real, on a fresh copy of the
   three files.

It only touches the files it creates and deletes them at the end.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    .venv\\Scripts\\python.exe tests/scripts/probe_file_references.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters import sw_type_info
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter, _byref_long
from solidworks_mcp.adapters.solidworks import references

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_file_references"


def _raw(call: Any) -> dict[str, Any]:
    """Call and record what came back, or what was raised (never swallowed)."""
    try:
        value = call()
    except Exception as exc:  # noqa: BLE001 - a probe records every failure
        return {"raised": f"{type(exc).__name__}: {exc}"}
    return {"type": type(value).__name__, "value": value}


async def _tools(adapter: PyWin32Adapter) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.drawing import register_drawing_tools
    from solidworks_mcp.tools.modeling import register_modeling_tools
    from solidworks_mcp.tools.sketching import register_sketching_tools

    mcp = FastMCP("probe-file-references")
    await register_sketching_tools(mcp, adapter, {})
    await register_modeling_tools(mcp, adapter, {})
    await register_drawing_tools(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


def _titles(adapter: PyWin32Adapter) -> set[str]:
    return {
        str(adapter._get_attr_or_call(d, "GetTitle"))
        for d in adapter.swApp.GetDocuments() or []
    }


def _close_new(adapter: PyWin32Adapter, before: set[str]) -> None:
    """Close the documents this probe opened; the user's stay open."""
    for title in _titles(adapter) - before:
        adapter._attempt(lambda t=title: adapter.swApp.CloseDoc(t))


async def _build(
    adapter: PyWin32Adapter, folder: Path, before: set[str]
) -> dict[str, str]:
    """The part, the assembly holding it and the assembly's drawing, closed."""
    tools = await _tools(adapter)
    files = {
        "part": str(folder / "pf_bracket.SLDPRT"),
        "assembly": str(folder / "pf_frame.SLDASM"),
        "drawing": str(folder / "pf_frame.SLDDRW"),
    }
    assert (await adapter.create_part()).is_success
    await tools["create_sketch"]({"plane": "Top"})
    await tools["add_rectangle"]({"x1": 0, "y1": 0, "x2": 30, "y2": 20})
    await tools["exit_sketch"]()
    await tools["create_extrusion"]({"depth": 5})
    assert (await adapter.save_file(files["part"])).is_success
    assert (await adapter.create_assembly()).is_success
    assert (await adapter.insert_component(files["part"], 0, 0, 0)).is_success
    assert (await adapter.save_file(files["assembly"])).is_success
    assert (await adapter.create_drawing()).is_success
    drawing = await tools["create_technical_drawing"]({"model_file": files["assembly"]})
    assert drawing["status"] == "success", drawing
    assert (await adapter.save_file(files["drawing"])).is_success
    _close_new(adapter, before)
    return files


def _dependencies(app: Any, path: str, traverse: bool) -> dict[str, Any]:
    sw_type_info.flag_members(app, "GetDocumentDependencies2")
    raw = _raw(lambda: app.GetDocumentDependencies2(path, traverse, False, False))
    if "value" in raw:
        raw["pairs"] = references.parse_dependencies(raw["value"])
    return raw


def _move_variants(app: Any, src: str, dst: str) -> list[dict[str, Any]]:
    """MoveDocument with each kind of empty children, until one marshals."""
    import pythoncom
    import win32com.client

    sw_type_info.flag_members(app, "MoveDocument")
    tried = []
    for label, empty in (
        (
            "VT_ARRAY|VT_BSTR []",
            lambda: win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_BSTR, []),
        ),
        ("None", lambda: None),
        ("[]", lambda: []),
    ):
        result = _raw(lambda e=empty: app.MoveDocument(src, dst, e(), e(), 0))
        result.update(children=label, moved=Path(dst).exists(), left=Path(src).exists())
        tried.append(result)
        if "value" in result:
            break
    return tried


async def run() -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT / stamp
    first, second = out / "first", out / "second"
    first.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"started": stamp}
    adapter = PyWin32Adapter({})
    await adapter.connect()
    app = adapter.swApp
    before = _titles(adapter)
    try:
        files = await _build(adapter, first, before)
        shutil.copytree(first, second)
        report["files"] = files

        # 1. References of closed files.
        report["dependencies"] = {
            f"{kind}, traverse {traverse}": _dependencies(app, path, traverse)
            for kind, path in files.items()
            for traverse in (False, True)
        }

        # 2. Move the closed part with no children.
        moved = str(first / "parts" / "pf_plate.SLDPRT")
        (first / "parts").mkdir()
        report["move_closed_part"] = _move_variants(app, files["part"], moved)

        # 3. Repoint the closed assembly and drawing to the moved part.
        sw_type_info.flag_members(app, "ReplaceReferencedDocument")
        report["repoint"] = {}
        for kind in ("assembly", "drawing"):
            report["repoint"][kind] = {
                "returned": _raw(
                    lambda k=kind: app.ReplaceReferencedDocument(
                        files[k], files["part"], moved
                    )
                ),
                "after": _dependencies(app, files[kind], False),
            }
        report["repoint_other_case"] = _raw(
            lambda: app.ReplaceReferencedDocument(
                files["assembly"], moved.upper(), moved
            )
        )

        # 4. Open the repointed assembly: does SolidWorks find the part?
        errors, warnings = _byref_long(), _byref_long()
        opened = _raw(
            lambda: app.OpenDoc6(files["assembly"], 2, 3, "", errors, warnings)
        )
        report["open_repointed_assembly"] = {
            "opened": "value" in opened and opened["value"] is not None,
            "errors": getattr(errors, "value", errors),
            "warnings": getattr(warnings, "value", warnings),
        }
        _close_new(adapter, before)

        # 5. MoveDocument on an open, unmodified part.
        errors, warnings = _byref_long(), _byref_long()
        app.OpenDoc6(moved, 1, 1, "", errors, warnings)
        report["move_open_part"] = _move_variants(
            app, moved, str(first / "pf_open_moved.SLDPRT")
        )
        _close_new(adapter, before)

        # 6. The tool's own path, on the untouched copy.
        part = str(second / "pf_bracket.SLDPRT")
        target = str(second / "pf_renamed.SLDPRT")
        dry = await adapter.move_document(part, target)
        real = await adapter.move_document(part, target, dry_run=False)
        report["move_document_dry_run"] = dry.data if dry.is_success else dry.error
        report["move_document"] = real.data if real.is_success else real.error
    finally:
        _close_new(adapter, before)
        await adapter.disconnect()
        for folder in (first, second):
            shutil.rmtree(folder, ignore_errors=True)
        path = out / "report.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"[probe] report: {path}")
        for key in (
            "move_closed_part",
            "repoint",
            "open_repointed_assembly",
            "move_open_part",
        ):
            print(f"[probe] {key}: {report.get(key)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
