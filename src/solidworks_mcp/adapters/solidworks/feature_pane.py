"""Hide the FeatureManager pane while a picture is framed (pitfall #42).

SolidWorks draws the FeatureManager tree over the left of the graphics area,
and ``ViewZoomtofit2`` and ``ViewZoomTo2`` centre the model in what the pane
leaves visible, while ``SaveBMP`` renders the whole area. On SOLIDWORKS 2026
SP4.1, minimized, the graphics area was 542 x 343 px and the pane 311 px
wide (``IModelDoc2.GetFeatureManagerWidth``): an 80 mm tower zoomed to fit
came out 270 px right of centre in an 800 x 600 picture, and a zoom box
showed the wrong part of the model. With the pane hidden
(``IModelDocExtension.HideFeatureManager(True)``) the same zoom put the tower
in the middle, filling 90 % of the height; showing the pane again doesn't
move the view. A large window shows the same shift, smaller: the "few mm
off centre" pitfall #42 recorded.
"""

from __future__ import annotations

from typing import Any

from .com_utils import attempt as _attempt
from .com_utils import flag as _flag


def hide(adapter: Any, doc: Any) -> Any:
    """Hide the pane when it shows; return what :func:`show` needs, or None.

    Nothing is hidden (None) when the pane's width doesn't read as more than
    0: it is hidden already, or the document has no pane.
    """
    _flag(adapter, doc, "GetFeatureManagerWidth")
    width = _attempt(adapter, lambda: doc.GetFeatureManagerWidth())
    if isinstance(width, bool) or not isinstance(width, int | float) or width <= 0:
        return None
    extension = _attempt(adapter, lambda: doc.Extension)
    if extension is None:
        return None
    _flag(adapter, extension, "HideFeatureManager")
    if not _attempt(adapter, lambda: extension.HideFeatureManager(True), False):
        return None
    return extension


def show(adapter: Any, hidden: Any) -> None:
    """Show the pane :func:`hide` hid (``hidden`` is its return); never raises."""
    if hidden is not None:
        _attempt(adapter, lambda: hidden.HideFeatureManager(False))
