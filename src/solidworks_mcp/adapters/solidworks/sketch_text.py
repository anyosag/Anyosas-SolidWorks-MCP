"""Text in the open sketch (``add_sketch_text``), for labels to raise or sink.

``IModelDoc2.InsertSketchText(x, y, z, Text, Alignment, FlipDirection,
HorizontalMirror, WidthFactor, SpaceBetweenChars)`` writes text into the open
sketch and returns its ``ISketchText``:

* the point is in sketch metres (the API help's "Insert Sketch Text and
  Hole" example moves its point into the sketch with
  ``ModelToSketchTransform`` first);
* width and spacing are percentages, 100 for normal text (that example
  passes 100, 100; ``SpaceBetweenChars`` must be 1 to 10000);
* with a sketch segment selected at mark 1 the text follows it, and
  Alignment and FlipDirection apply; with nothing selected it runs along
  sketch x (Remarks).

So an angle is a construction line from the start point at that angle,
selected at mark 1, with the text on it. Font and height are the text's
``ITextFormat`` (``ISketchText.GetTextFormat``: ``CharHeight`` in metres,
``TypeFaceName``, ``Bold``, ``Italic``), changed and handed back with
``SetTextFormat(False, format)``, which needs the sketch open (Remarks).
What SolidWorks then holds is read back with ``inspection.describe_text``:
the Lid build's live read showed ``Text``, ``GetCoordinates`` (m),
``CharHeight`` (m) and ``TypeFaceName`` answering.

A sketch holding both a profile and text doesn't extrude, so the text's
reply names any other geometry already in its sketch.

From the Dipper build (SOLIDWORKS 2026 SP4.1), which could add no labels at
all (MIN/MAX, OPEN FLAT, name and version).
"""

from __future__ import annotations

import math
from typing import Any

from .com_utils import attempt as _attempt
from .com_utils import flag as _flag

#: ``InsertSketchText``'s WidthFactor and SpaceBetweenChars, in percent.
WIDTH_PERCENT = 100
SPACING_PERCENT = 100

#: The mark a curve the text follows is selected with (Remarks).
_MARK_TEXT_CURVE = 1

#: Registry prefix of the construction line an angled text follows; it and
#: ``Text`` both start ``Text``, so neither counts as a profile.
GUIDE_PREFIX = "TextLine"

#: The height assumed for a guide line's length when the document's is kept.
_DEFAULT_HEIGHT_MM = 5.0


def guide_length_mm(text: str, height_mm: float | None) -> float:
    """How long a construction line an angled text follows is drawn, mm.

    Longer than the text: a character is rarely wider than its height, and
    a line running past the text's end changes nothing.
    """
    return max(len(text), 1) * float(height_mm or _DEFAULT_HEIGHT_MM) * 1.5


def other_geometry(registry: dict[str, Any]) -> list[str]:
    """Ids in the open sketch that are not text: a profile drawn there too."""
    return [entity for entity in registry if not entity.startswith("Text")]


def _select_with_mark(adapter: Any, segment: Any, mark: int) -> bool:
    """Select a sketch segment alone under ``mark`` (``ISketchSegment.Select4``)."""
    manager = _attempt(adapter, lambda: adapter.currentModel.SelectionManager)
    _flag(adapter, manager, "CreateSelectData")
    select_data = _attempt(adapter, lambda: manager.CreateSelectData())
    if select_data is None:
        return False
    _attempt(adapter, lambda: setattr(select_data, "Mark", mark))
    _flag(adapter, segment, "Select4")
    return bool(_attempt(adapter, lambda: segment.Select4(False, select_data), False))


def _guide_line(
    adapter: Any, x: float, y: float, angle: float, length: float
) -> tuple[str, Any]:
    """Draw the construction line an angled text follows, and select it.

    Raises:
        Exception: The line could not be drawn or selected.
    """
    radians = math.radians(angle)
    end_x = x + length * math.cos(radians)
    end_y = y + length * math.sin(radians)
    line = adapter.currentSketchManager.CreateLine(
        x / 1000.0, y / 1000.0, 0.0, end_x / 1000.0, end_y / 1000.0, 0.0
    )
    if not line:
        raise Exception(
            f"Could not draw the construction line the text follows at {angle:g} deg."
        )
    _attempt(adapter, lambda: setattr(line, "ConstructionGeometry", True))
    line_id = str(adapter._register_sketch_entity(GUIDE_PREFIX, line))
    if not _select_with_mark(adapter, line, _MARK_TEXT_CURVE):
        raise Exception(
            f"Could not select the construction line {line_id} for the text "
            f"to follow at {angle:g} deg."
        )
    return line_id, line


def _apply_format(
    adapter: Any,
    sketch_text: Any,
    height: float | None,
    font: str | None,
    bold: bool,
    italic: bool,
) -> bool:
    """Set the text's height, font, bold and italic; True when SolidWorks took it."""
    _flag(adapter, sketch_text, "GetTextFormat")
    text_format = _attempt(adapter, lambda: sketch_text.GetTextFormat())
    if text_format is None:
        return False
    if height is not None:
        _attempt(
            adapter, lambda: setattr(text_format, "CharHeight", float(height) / 1000.0)
        )
    if font:
        _attempt(adapter, lambda: setattr(text_format, "TypeFaceName", str(font)))
    _attempt(adapter, lambda: setattr(text_format, "Bold", bool(bold)))
    _attempt(adapter, lambda: setattr(text_format, "Italic", bool(italic)))
    return bool(
        _attempt(adapter, lambda: sketch_text.SetTextFormat(False, text_format), False)
    )


def add_sketch_text(
    adapter: Any,
    text: str,
    x: float = 0.0,
    y: float = 0.0,
    height: float | None = None,
    font: str | None = None,
    angle: float = 0.0,
    bold: bool = False,
    italic: bool = False,
) -> dict[str, Any]:
    """Write ``text`` in the open sketch; runs inside ``_handle_com_operation``.

    Args:
        adapter: A ``PyWin32Adapter`` with a sketch open.
        text: The text.
        x: Where it starts, sketch x in mm.
        y: Where it starts, sketch y in mm.
        height: Character height in mm; None keeps the document's.
        font: Font name; None keeps the document's.
        angle: Degrees counter-clockwise from sketch x, along a construction
            line drawn for it.
        bold: Bold characters.
        italic: Italic characters.

    Returns:
        dict[str, Any]: ``id`` (``Text_<n>``), what SolidWorks reads back
        (``text``, ``position_mm``, ``font``, ``height_mm``, ``bold``,
        ``italic``, ``uses_document_font``), ``angle_deg``, ``along_line`` (the
        guide line's id, for an angle), ``format_applied`` (None when nothing
        was asked of the format) and ``shares_sketch_with`` (other geometry
        in the sketch).

    Raises:
        Exception: SolidWorks inserted no text, or the guide line failed.
    """
    from .inspection import describe_text

    model = adapter.currentModel
    others = other_geometry(dict(getattr(adapter, "_sketch_entities", {}) or {}))
    _attempt(adapter, lambda: model.ClearSelection2(True))
    guide_id = None
    if angle:
        guide_id, _line = _guide_line(
            adapter, x, y, angle, guide_length_mm(text, height)
        )
    _flag(adapter, model, "InsertSketchText")
    sketch_text = model.InsertSketchText(
        x / 1000.0,
        y / 1000.0,
        0.0,
        text,
        0,  # Alignment: left
        0,  # FlipDirection
        0,  # HorizontalMirror
        WIDTH_PERCENT,
        SPACING_PERCENT,
    )
    _attempt(adapter, lambda: model.ClearSelection2(True))
    if sketch_text is None:
        raise Exception(
            f"SolidWorks inserted no text for {text!r}. The sketch may be "
            "closed; open one with create_sketch."
        )
    applied = None
    if height is not None or font or bold or italic:
        applied = _apply_format(adapter, sketch_text, height, font, bold, italic)
    text_id = str(adapter._register_sketch_entity("Text", sketch_text))
    read = describe_text(adapter, sketch_text)
    read["angle_deg"] = float(angle)
    return {
        "id": text_id,
        **read,
        "along_line": guide_id,
        "format_applied": applied,
        "shares_sketch_with": others,
    }
