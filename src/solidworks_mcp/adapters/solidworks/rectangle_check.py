"""Whether ``add_rectangle`` drew the rectangle it was asked for.

The Dipper (SOLIDWORKS 2026 SP4.1): on a plane offset from Front,
``add_rectangle {x1: 10.6, y1: -81, x2: 12.6, y2: -17}`` reported 2 x 64 mm,
and ``get_sketch`` showed a 2 x 2 square (y -81 to -79). The second rectangle
in the sketch did the same. The extrusion's ``measured_change`` (164 mm³
where about 5,270 were meant) was the only clue. The same corners drawn with
``add_outlines`` (four ``CreateLine`` calls) came out right.

``ISketchManager.CreateCornerRectangle`` places its second corner where
SolidWorks infers it should go; ``AddToDB`` (on since ``create_sketch``)
should skip that, as it does for lines. Which inference squared the
rectangle is what ``tests/scripts/probe_dipper_fixes.py`` settles. Here:

* :func:`inference_off`: ``swSketchInference`` off around the call, then
  back to the user's value; ``AddToDB`` is set again first.
* :func:`drawn_extent`: the box of the lines it returned, read back in
  sketch millimetres (``feature_editing.describe_segment``).
* :func:`mismatch`: what differs from the corners asked, beyond
  :data:`TOLERANCE_MM`.
* :func:`delete_segments`: removes a wrong rectangle again.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .com_utils import as_list
from .com_utils import attempt as _attempt
from .com_utils import flag as _flag

#: ``swUserPreferenceToggle_e.swSketchInference``.
SKETCH_INFERENCE = 249
#: How far a drawn side may be from the one asked, in mm.
TOLERANCE_MM = 1e-3

#: ``(x_min, y_min, x_max, y_max)`` in sketch mm.
Extent = tuple[float, float, float, float]


@contextmanager
def inference_off(adapter: Any) -> Iterator[None]:
    """Draw with ``AddToDB`` on and sketch inference off; put inference back after.

    Nothing is changed when the preference can't be read.
    """
    manager = adapter.currentSketchManager
    _attempt(adapter, lambda: setattr(manager, "AddToDB", True))
    app = getattr(adapter, "swApp", None)
    _flag(adapter, app, "GetUserPreferenceToggle", "SetUserPreferenceToggle")
    was = _attempt(adapter, lambda: app.GetUserPreferenceToggle(SKETCH_INFERENCE))
    turned = isinstance(was, bool) and was
    if turned:
        _attempt(adapter, lambda: app.SetUserPreferenceToggle(SKETCH_INFERENCE, False))
    try:
        yield
    finally:
        if turned:
            _attempt(
                adapter, lambda: app.SetUserPreferenceToggle(SKETCH_INFERENCE, True)
            )


def drawn_extent(adapter: Any, segments: Any) -> Extent | None:
    """The box around the lines ``CreateCornerRectangle`` returned, in sketch mm.

    Returns:
        Extent | None: The box, or None when fewer than four lines read.
    """
    from .feature_editing import describe_segment

    xs: list[float] = []
    ys: list[float] = []
    lines = 0
    for segment in as_list(segments):
        info = describe_segment(adapter, segment)
        if info.get("type") != "line":
            continue
        ends = [info.get("start_mm"), info.get("end_mm")]
        if any(not isinstance(point, list) or len(point) < 2 for point in ends):
            return None
        lines += 1
        for point in ends:
            xs.append(float(point[0]))  # type: ignore[index]
            ys.append(float(point[1]))  # type: ignore[index]
    if lines < 4:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def mismatch(asked: Extent, drawn: Extent) -> str | None:
    """How the drawn rectangle differs from the one asked, or None when it doesn't.

    Args:
        asked: The corners asked, as a box.
        drawn: :func:`drawn_extent`.

    Returns:
        str | None: A sentence naming both, to follow "SolidWorks drew".
    """
    if all(abs(a - d) <= TOLERANCE_MM for a, d in zip(asked, drawn, strict=True)):
        return None

    def _box(box: Extent) -> str:
        return (
            f"({box[0]:g}, {box[1]:g}) to ({box[2]:g}, {box[3]:g}) mm, "
            f"{box[2] - box[0]:g} x {box[3] - box[1]:g}"
        )

    return f"{_box(drawn)}, not {_box(asked)} as asked"


def delete_segments(adapter: Any, segments: Any) -> bool:
    """Select the rectangle's segments and delete them; True when SolidWorks did."""
    handles = as_list(segments)
    if not all(
        adapter._select_sketch_entity(handle, index > 0)
        for index, handle in enumerate(handles)
    ):
        return False
    model = adapter.currentModel
    # EditDelete returns nothing; only an exception means it failed.
    return _attempt(adapter, lambda: model.EditDelete(), False) is not False
