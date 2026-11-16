"""Probe an ``edit_sketch`` + ``exit_sketch`` made only to read a sketch.

The Lid build (SolidWorks 2026 SP4.1) opened a sketch with ``edit_sketch``
to read its geometry and closed it with ``exit_sketch``; the part came out
``is_dirty`` with nothing changed, and the sketch's text was listed as
"Text_5" with no string, font, height or position. ``exit_sketch`` now leaves
a sketch that is as ``edit_sketch`` found it, on a part with no unsaved
changes, with ``InsertSketch(False)`` (no rebuild), and ``edit_sketch``
describes a text through ``ISketchText``. This settles:

* which step sets the save flag (``IModelDoc2.GetSaveFlag``): ``EditSketch``
  itself, ``InsertSketch(True)``, or ``InsertSketch(False)``; and whether
  ``IModelDocExtension.NeedsRebuild2`` is left set after ``InsertSketch(False)``;
* whether ``EditUndo2(1)`` after a read-only round trip clears the flag and
  keeps the geometry (volume), on this scratch part only;
* what the adapter's ``exit_sketch`` reports (``metadata``: ``unchanged``,
  ``is_dirty``);
* whether a text segment from ``ISketch.GetSketchSegments`` answers
  ``Text``/``GetTextFormat``/``GetCoordinates`` itself, or only the
  ``ISketchText`` from ``GetSketchTextSegments`` does, whether the two wrap
  the same COM object (``same_com_object``), and what units and frame
  ``GetCoordinates`` and ``CharHeight`` use (the text is placed at
  (12, -3.5) mm in the sketch, the document's default height);
* the segments ``edit_sketch`` lists.

The part is a 40 x 40 x 5 mm block from a Top sketch that also holds the
text "LID". It is saved to a scratch folder first, so the save flag starts
clear. The probe only touches that document, closes it, deletes its file,
and keeps the report in ``tests/.generated/probe_sketch_round_trip/``.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    .venv\\Scripts\\python.exe tests/scripts/probe_sketch_round_trip.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters import sw_type_info
from solidworks_mcp.adapters.base import ExtrusionParameters
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import feature_editing

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_sketch_round_trip"


def _read(obj: Any, name: str, call: bool = False) -> Any:
    try:
        value = getattr(obj, name)
        value = value() if call else value
    except Exception as exc:  # noqa: BLE001 - a probe records every failure
        return f"error: {exc}"
    if isinstance(value, (list, tuple)):
        return [float(v) if isinstance(v, (int, float)) else str(v) for v in value]
    return (
        value if isinstance(value, (int, float, bool, str, type(None))) else str(value)
    )


def _state(adapter: PyWin32Adapter, model: Any) -> dict[str, Any]:
    extension = sw_type_info.flagged(model.Extension, "IModelDocExtension")
    return {
        "save_flag": feature_editing.save_flag(adapter, model),
        "needs_rebuild2": _read(extension, "NeedsRebuild2"),
    }


async def _volume(adapter: PyWin32Adapter) -> Any:
    summary = await adapter.get_model_geometry_summary()
    return (summary.data or {}).get("volume_mm3") if summary.is_success else None


def _edit(adapter: PyWin32Adapter, model: Any, name: str) -> None:
    feature = sw_type_info.flagged(model.FeatureByName(name), "IFeature")
    model.ClearSelection2(True)
    feature.Select2(False, 0)
    adapter._get_attr_or_call(model, "EditSketch")


def _text_reads(adapter: PyWin32Adapter, sketch: Any) -> dict[str, Any]:
    sw_type_info.flag_members(sketch, "GetSketchSegments", "GetSketchTextSegments")
    segments = list(sketch.GetSketchSegments() or [])
    texts = list(sketch.GetSketchTextSegments() or [])
    rows = []
    for segment in segments:
        sw_type_info.flag_members(segment, "GetType", "GetTextFormat", "GetCoordinates")
        if segment.GetType() != feature_editing.SEGMENT_TEXT:
            continue
        rows.append(
            {
                "on_segment": {
                    "Text": _read(segment, "Text"),
                    "GetCoordinates": _read(segment, "GetCoordinates", call=True),
                    "GetTextFormat": _read(segment, "GetTextFormat", call=True),
                },
                "same_as_texts": [
                    sw_type_info.same_com_object(segment, t) for t in texts
                ],
            }
        )
    for text in texts:
        sw_type_info.flag_members(
            text, "GetTextFormat", "GetCoordinates", "GetUseDocTextFormat"
        )
        text_format = text.GetTextFormat()
        rows.append(
            {
                "ISketchText": {
                    "Text": _read(text, "Text"),
                    "GetCoordinates": _read(text, "GetCoordinates", call=True),
                    "GetUseDocTextFormat": _read(text, "GetUseDocTextFormat", True),
                    "CharHeight": _read(text_format, "CharHeight"),
                    "CharHeightInPts": _read(text_format, "CharHeightInPts"),
                    "TypeFaceName": _read(text_format, "TypeFaceName"),
                }
            }
        )
    return {"segments": len(segments), "texts": len(texts), "rows": rows}


async def run() -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT / stamp
    scratch = out / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"started": stamp}
    adapter = PyWin32Adapter({})
    await adapter.connect()
    title = None
    try:
        assert (await adapter.create_part()).is_success
        model = adapter.currentModel
        title = str(adapter._get_attr_or_call(model, "GetTitle"))
        assert (await adapter.create_sketch("Top")).is_success
        assert (await adapter.add_rectangle(-20, -20, 20, 20)).is_success
        assert (await adapter.exit_sketch()).is_success
        # A sketch holding the rectangle and the text won't extrude: the block
        # is extruded first, and the text gets a sketch of its own.
        boss = await adapter.create_extrusion(ExtrusionParameters(depth=5))
        assert boss.is_success, boss.error
        assert (await adapter.create_sketch("Top")).is_success
        assert (await adapter.add_line(-15, -15, 15, -15)).is_success
        sw_type_info.flag_members(model, "InsertSketchText")
        made = adapter._attempt(
            lambda: model.InsertSketchText(
                0.012, -0.0035, 0.0, "LID", 0, 0, 0, 100, 100
            )
        )
        report["inserted_text"] = made is not None
        assert (await adapter.exit_sketch()).is_success
        sketch_name = adapter._last_sketch_name or "Sketch2"
        saved = await adapter.save_file(str(scratch / "probe_round_trip.SLDPRT"))
        assert saved.is_success, saved.error
        report["sketch"] = sketch_name
        report["after_save"] = _state(adapter, model)
        report["volume_before_mm3"] = await _volume(adapter)

        # 1. EditSketch alone, then InsertSketch(False).
        _edit(adapter, model, sketch_name)
        report["after_edit_sketch"] = _state(adapter, model)
        report["text_reads"] = _text_reads(adapter, model.SketchManager.ActiveSketch)
        manager = sw_type_info.flagged(model.SketchManager, "ISketchManager")
        manager.InsertSketch(False)
        report["after_insert_sketch_false"] = _state(adapter, model)

        # 2. Saved clean again, then EditSketch and InsertSketch(True).
        assert (await adapter.save_file()).is_success
        report["after_second_save"] = _state(adapter, model)
        _edit(adapter, model, sketch_name)
        manager.InsertSketch(True)
        report["after_insert_sketch_true"] = _state(adapter, model)

        # 3. The adapter's round trip on a clean part.
        assert (await adapter.save_file()).is_success
        opened = await adapter.edit_sketch(sketch_name)
        report["edit_sketch"] = opened.data if opened.is_success else opened.error
        exited = await adapter.exit_sketch()
        report["exit_sketch"] = {
            "status": str(exited.status),
            "metadata": exited.metadata,
            "error": exited.error,
            **_state(adapter, model),
        }
        report["volume_after_mm3"] = await _volume(adapter)

        # 4. Last, as it may undo more than the round trip: EditUndo2(1) after
        # a dirty round trip, its flag and the geometry.
        _edit(adapter, model, sketch_name)
        manager.InsertSketch(True)
        report["before_edit_undo2"] = _state(adapter, model)
        sw_type_info.flag_members(model, "EditUndo2")
        undone = adapter._attempt(lambda: model.EditUndo2(1))
        report["after_edit_undo2"] = {
            "returned": undone,
            **_state(adapter, model),
            "volume_mm3": await _volume(adapter),
        }
    finally:
        if title:
            adapter._attempt(lambda: adapter.swApp.CloseDoc(title))
        await adapter.disconnect()
        for path in scratch.glob("*"):
            try:
                path.unlink()
            except OSError:
                pass
        path = out / "report.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"[probe] report: {path}")
        for key in (
            "after_save",
            "after_edit_sketch",
            "after_insert_sketch_false",
            "after_insert_sketch_true",
            "exit_sketch",
            "after_edit_undo2",
        ):
            print(f"[probe] {key}: {report.get(key)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
