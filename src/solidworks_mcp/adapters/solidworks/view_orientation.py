"""Turn the model view to an orientation: ``export_image``'s ``view_direction`` and ``up_axis``.

The maths is in ``view_matrix``. The COM calls:

* ``IModelDoc2.ActiveView`` (``IModelView``), its ``Orientation3`` read and
  written as an ``IMathTransform``. The SolidWorks API help gives it as a
  read-write property; ``zoom_box`` reads it for every zoom box (pitfall #42).
* ``IMathUtility.CreateTransform`` (flagged first: unflagged, late binding
  reads it as a property and SolidWorks answers "Member not found", pitfall
  #41) with the 16 ``ArrayData`` values as a ``VT_ARRAY | VT_R8``.

Only the rotation is written; the transform's other values are kept as read.
The orientation is read back, and a view SolidWorks didn't take stops the
picture rather than giving one from the wrong side.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from . import view_matrix
from .com_utils import attempt as _attempt
from .com_utils import double_array as _double_array
from .com_utils import flag as _flag


def _view(adapter: Any, doc: Any) -> Any:
    return _attempt(adapter, lambda: doc.ActiveView)


def _array(adapter: Any, view: Any) -> Any:
    transform = _attempt(adapter, lambda: view.Orientation3) if view else None
    return _attempt(adapter, lambda: transform.ArrayData) if transform else None


def read_rows(adapter: Any, doc: Any) -> view_matrix.Rows | None:
    """The active view's orientation, or None when it can't be read."""
    return view_matrix.rows_from_array(_array(adapter, _view(adapter, doc)))


def turn(adapter: Any, doc: Any, rows: Sequence[Sequence[float]]) -> dict[str, Any]:
    """Set the active view's orientation to ``rows`` and read it back.

    Args:
        adapter: A connected adapter.
        doc: The document whose view turns.
        rows: The orientation (``view_matrix.look_from``'s convention).

    Returns:
        dict[str, Any]: ``axes`` (``view_matrix.axes`` of the orientation read
        back, or of ``rows`` when it can't be read) and ``checked`` (whether
        it was read back).

    Raises:
        RuntimeError: There is no view or math utility, or SolidWorks kept a
            different orientation. No picture should be taken.
    """
    view = _view(adapter, doc)
    utility = _attempt(adapter, lambda: adapter.swApp.GetMathUtility())
    if view is None or utility is None:
        raise RuntimeError(
            "The model view or SolidWorks' math utility can't be read, so the "
            "view could not be turned; no picture was taken."
        )
    _flag(adapter, utility, "CreateTransform")
    values = view_matrix.with_rows(_array(adapter, view), rows)
    view.Orientation3 = utility.CreateTransform(_double_array(values))
    after = view_matrix.rows_from_array(_array(adapter, view))
    if after is not None and not view_matrix.same(after, rows):
        raise RuntimeError(
            "SolidWorks kept a different view orientation "
            f"({view_matrix.axes(after)}), so no picture was taken."
        )
    return {
        "axes": view_matrix.axes(after if after is not None else rows),
        "checked": after is not None,
    }


def turn_z_up(adapter: Any, doc: Any) -> dict[str, Any]:
    """Turn the standard view just shown so the model's +Z is up.

    Read SolidWorks' own orientation for the view (so dimetric and trimetric
    keep its angles), then :func:`turn` to ``view_matrix.z_up`` of it.

    Raises:
        RuntimeError: The view's orientation can't be read, or
            :func:`turn` failed.
    """
    rows = read_rows(adapter, doc)
    if rows is None:
        raise RuntimeError(
            "The view's orientation can't be read, so it could not be turned "
            "Z-up; no picture was taken."
        )
    return turn(adapter, doc, view_matrix.z_up(rows))
