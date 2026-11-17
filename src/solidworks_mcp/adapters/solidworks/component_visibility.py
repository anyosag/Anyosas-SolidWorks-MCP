"""Hide and show components of an assembly: ``set_component_visibility`` and ``export_image``'s ``hide_components``.

``set_visibility`` covers reference geometry only, so to render a hinged
part "cover off" The Dipper session moved the cover 60 mm sideways and back.

Two ways to hide a component, both in the active display state:

* ``IComponent2.Visible``, a read-write ``swComponentVisibilityState_e``
  (``swComponentHidden`` 0, ``swComponentVisible`` 1, ``swComponentUnknown``
  -1). No selection is needed, and it reads back.
* ``IModelDoc2.HideComponent2`` / ``ShowComponent2`` on the selected
  components, as the tree's Hide and Show do (no arguments: flagged first).

``Visible`` is tried first and read back; when SolidWorks kept the old state,
the component is selected alone (``IComponent2.Select4`` with a
``SelectData``) and hidden or shown with the selection call, then read back
again, and the selection is cleared. The reply says which call did it
(``method``); ``tests/scripts/probe_r2_views.py`` settles which one SolidWorks
2026 takes and whether a hidden component stays hidden after a save and a
reopen.

Components are looked up by ``IComponent2.Name2`` among all of the
assembly's components (``GetComponents(False)``), so a top-level component is
``cover-1`` and one inside a sub-assembly ``hinge-1/pin-1``.
"""

from __future__ import annotations

from typing import Any

from .. import sw_type_info as _sw_type_info
from .com_utils import attempt as _attempt
from .com_utils import flag as _flag
from .io import _as_com

#: ``swComponentVisibilityState_e`` (SOLIDWORKS 2026 swconst).
HIDDEN = 0
VISIBLE = 1


def find(adapter: Any, model: Any, names: list[str]) -> dict[str, Any]:
    """The named components of the assembly ``model``, as ``IComponent2``.

    Args:
        adapter: A connected adapter.
        model: The assembly, as ``IModelDoc2``.
        names: Component names, as ``list_components`` gives them (a part in a
            sub-assembly as ``sub-1/part-1``).

    Returns:
        dict[str, Any]: Each name to its component, in the order given.

    Raises:
        LookupError: The components can't be read, or a name isn't there; the
            message lists the top-level components. Nothing has changed.
    """
    assembly = _sw_type_info.flagged(model, "IAssemblyDoc")
    raw = _attempt(adapter, lambda: assembly.GetComponents(False))
    if not isinstance(raw, (list, tuple)):
        raise LookupError("The assembly's components can't be read.")
    by_name: dict[str, Any] = {}
    for item in raw:
        component = _as_com(adapter, item, "IComponent2")
        name = _attempt(adapter, lambda c=component: c.Name2) if component else None
        if name:
            by_name.setdefault(str(name), component)
    missing = [name for name in names if name not in by_name]
    if missing:
        top_level = [name for name in by_name if "/" not in name]
        raise LookupError(
            f"Component(s) not found: {', '.join(missing)}. The assembly holds: "
            f"{', '.join(top_level) or 'nothing'} (list_components names them)."
        )
    return {name: by_name[name] for name in names}


def is_visible(adapter: Any, component: Any) -> bool | None:
    """Whether a component is shown, from ``IComponent2.Visible``; None when unknown."""
    value = _attempt(adapter, lambda: component.Visible)
    if not isinstance(value, (int, float, bool)) or int(value) not in (
        HIDDEN,
        VISIBLE,
    ):
        return None
    return int(value) == VISIBLE


def _by_selection(adapter: Any, model: Any, component: Any, visible: bool) -> None:
    """Select ``component`` alone and hide or show it as the tree does."""
    method = "ShowComponent2" if visible else "HideComponent2"
    _flag(adapter, model, method, "ClearSelection2")
    _attempt(adapter, lambda: model.ClearSelection2(True))
    selection = _attempt(
        adapter,
        lambda: _sw_type_info.flagged(
            model.SelectionManager, "ISelectionMgr"
        ).CreateSelectData(),
    )
    try:
        if _attempt(adapter, lambda: component.Select4(False, selection, False), False):
            _attempt(adapter, lambda: adapter._get_attr_or_call(model, method))
    finally:
        _attempt(adapter, lambda: model.ClearSelection2(True))


def set_one(
    adapter: Any, model: Any, name: str, component: Any, visible: bool
) -> dict[str, Any]:
    """Hide or show one component and read it back.

    Returns:
        dict[str, Any]: ``name``, ``before`` and ``after`` (shown or not, None
        when SolidWorks can't say) and ``method`` (``Visible``,
        ``ShowComponent2`` / ``HideComponent2``, or None when it already was).

    Raises:
        RuntimeError: SolidWorks kept it as it was.
    """
    before = is_visible(adapter, component)
    if before is visible:
        return {"name": name, "before": before, "after": before, "method": None}
    method = "Visible"
    _attempt(
        adapter,
        lambda: setattr(component, "Visible", VISIBLE if visible else HIDDEN),
    )
    after = is_visible(adapter, component)
    if after is not visible:
        method = "ShowComponent2" if visible else "HideComponent2"
        _by_selection(adapter, model, component, visible)
        after = is_visible(adapter, component)
    if after is not None and after is not visible:
        raise RuntimeError(
            f"SolidWorks kept {name} {'hidden' if visible else 'shown'} "
            "(IComponent2.Visible and the tree's Hide/Show both left it)."
        )
    return {"name": name, "before": before, "after": after, "method": method}


def apply(
    adapter: Any,
    model: Any,
    components: dict[str, Any],
    visible: bool,
    done: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Hide or show each component, appending each one's state to ``done`` as it goes.

    ``done`` is filled one component at a time, so a caller that has to put
    them back after a failure knows which ones changed.

    Returns:
        list[dict[str, Any]]: ``done``.

    Raises:
        RuntimeError: SolidWorks kept one as it was (``done`` holds those
            before it).
    """
    for name, component in components.items():
        done.append(set_one(adapter, model, name, component, visible))
    _attempt(adapter, lambda: model.GraphicsRedraw2())
    return done


def restore(
    adapter: Any, model: Any, components: dict[str, Any], done: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Put back each component :func:`apply` changed, from its ``before``. Never raises.

    Returns:
        list[dict[str, Any]]: ``name``, ``before`` and ``restored`` (whether
        it reads back as it was; True for one that didn't change) per entry.
    """
    report = []
    for entry in done:
        name, before = entry["name"], entry.get("before")
        restored = True
        if before is not None and entry.get("after") is not before:
            try:
                set_one(adapter, model, name, components[name], before)
            except Exception:  # noqa: BLE001 - restoring must reach every component
                pass
            restored = is_visible(adapter, components[name]) is before
        report.append({"name": name, "before": before, "restored": restored})
    _attempt(adapter, lambda: model.GraphicsRedraw2())
    return report
