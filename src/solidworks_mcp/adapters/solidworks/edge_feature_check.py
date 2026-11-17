"""What a new fillet or chamfer took: the edges selected, the edges it holds, the rollback bar.

The Dipper (SOLIDWORKS 2026 SP4.1): with the rollback bar above two Save
Bodies features, ``select_edges`` selected 4 straight edges and ``add_fillet``
made Fillet3 (R0.8) with no complaint. After ``set_rollback`` to the end and
``rebuild_model``, Fillet3 warned ``swFeatureErrorFilletNoEdge`` and the 2
edges on the second body were no longer filleted. The reply said ``edges:
[]`` (the selection was used) and gave no volume change, so a partial fillet
stayed invisible.

* :func:`selection_counts`: the edges and other entities selected just before
  the feature call (``ISelectionMgr.GetSelectedObjectType3``).
* :func:`newest_feature`: the feature the call returned, else
  ``IModelDocExtension.GetLastFeatureAdded()``: ``FeatureFillet3`` on
  SolidWorks 2025+ returns a number.
* :func:`feature_facts`: the edges the feature's definition holds
  (``feature_editing.edge_count``, read without ``AccessSelections``, so
  nothing is marked changed) and the features below the rollback bar after
  it (``GetNextFeature``, ``IsRolledBack``): a new feature goes in at the bar.
* :func:`warnings`: what a reply should say about those.

Which of these read right on SolidWorks 2026 is what
``tests/scripts/probe_dipper_fixes.py`` settles.
"""

from __future__ import annotations

from typing import Any

from .com_utils import attempt as _attempt
from .com_utils import flag as _flag
from .feature_editing import SELECTED_EDGE, edge_count

#: Runaway guard for the walk past the new feature.
_MAX_FEATURES = 10000
#: The most rolled-back feature names a warning lists.
_NAMES_SHOWN = 5


def selection_counts(adapter: Any) -> dict[str, int] | None:
    """How many edges, and how many other entities, are selected now.

    Returns:
        dict[str, int] | None: ``edges`` and ``other``, or None when the
        selection can't be read.
    """
    model = adapter.currentModel
    manager = _attempt(adapter, lambda: model.SelectionManager)
    if manager is None:
        return None
    _flag(adapter, manager, "GetSelectedObjectCount2", "GetSelectedObjectType3")
    count = _attempt(adapter, lambda: manager.GetSelectedObjectCount2(-1))
    if not isinstance(count, int) or isinstance(count, bool):
        return None
    edges = sum(
        1
        for index in range(1, count + 1)
        if _attempt(adapter, lambda i=index: manager.GetSelectedObjectType3(i, -1))
        == SELECTED_EDGE
    )
    return {"edges": edges, "other": count - edges}


def newest_feature(adapter: Any, returned: Any) -> Any:
    """The new feature: ``returned`` when the call gave one, else the last one added.

    Args:
        adapter: A connected adapter with a ``currentModel``.
        returned: What the feature call returned: an ``IFeature``, or a
            number (``IModelDoc2.FeatureFillet3`` on SolidWorks 2025+).

    Returns:
        Any: The ``IFeature``, or None when it can't be had.
    """
    if returned is not None and not isinstance(returned, int | float):
        return returned
    model = adapter.currentModel
    extension = _attempt(adapter, lambda: model.Extension)
    if extension is None:
        return None
    _flag(adapter, extension, "GetLastFeatureAdded")
    return _attempt(adapter, lambda: extension.GetLastFeatureAdded())


def features_below_bar(adapter: Any, feature: Any) -> list[str] | None:
    """The features after ``feature`` that are below the rollback bar.

    A feature made while the bar is up goes in just above it, so every
    feature after it is rolled back; with the bar at the end nothing follows.

    Returns:
        list[str] | None: Their names in tree order (empty with the bar at
        the end), or None when the tree can't be read.
    """
    names: list[str] = []
    current = feature
    for _ in range(_MAX_FEATURES):
        _flag(adapter, current, "GetNextFeature")
        following = _attempt(adapter, lambda c=current: c.GetNextFeature())
        if following is None:
            return names
        _flag(adapter, following, "IsRolledBack")
        rolled = _attempt(adapter, lambda f=following: f.IsRolledBack())
        if not isinstance(rolled, bool):
            return None
        if rolled:
            names.append(str(_attempt(adapter, lambda f=following: f.Name, "") or ""))
        current = following
    return names


def feature_facts(adapter: Any, feature: Any) -> dict[str, Any]:
    """The new feature's name, the edges it holds and the features below the bar.

    Returns:
        dict[str, Any]: ``checked_feature`` (its name), ``edges_held`` and
        ``features_below_bar``; each None when it can't be read.
    """
    if feature is None:
        return {"checked_feature": None, "edges_held": None, "features_below_bar": None}
    _flag(adapter, feature, "GetDefinition")
    name = _attempt(adapter, lambda: feature.Name)
    data = _attempt(adapter, lambda: feature.GetDefinition())
    held = edge_count(adapter, data) if data is not None else None
    return {
        "checked_feature": name if isinstance(name, str) else None,
        "edges_held": held,
        "features_below_bar": features_below_bar(adapter, feature),
    }


def warnings(kind: str, name: str, facts: dict[str, Any]) -> list[str]:
    """What a fillet's or chamfer's reply should warn about.

    Args:
        kind: ``"fillet"`` or ``"chamfer"``.
        name: The feature's name.
        facts: ``edges_selected``, ``selection_other``, ``edges_held`` and
            ``features_below_bar`` as the adapter read them.

    Returns:
        list[str]: A warning when it holds fewer edges than were selected
        (only edges selected: a face gives a chamfer all its edges), and one
        when the rollback bar is up.
    """
    out: list[str] = []
    selected = facts.get("edges_selected")
    held = facts.get("edges_held")
    only_edges = not facts.get("selection_other")
    if (
        isinstance(selected, int)
        and isinstance(held, int)
        and only_edges
        and held < selected
    ):
        out.append(
            f"{name} holds {held} of the {selected} edges selected: SolidWorks "
            f"left {selected - held} out. To add them, select every edge it "
            "should treat (select_edges) and call edit_feature with edges: "
            '"selection", or undo and select them again.'
        )
    below = facts.get("features_below_bar")
    if below:
        shown = ", ".join(below[:_NAMES_SHOWN]) + (
            f" and {len(below) - _NAMES_SHOWN} more"
            if len(below) > _NAMES_SHOWN
            else ""
        )
        out.append(
            f"The rollback bar is up: {len(below)} feature(s) after {name} "
            f"({shown}) are not built yet. They rebuild when you roll forward "
            f"(set_rollback with no input), and a {kind} made with the bar up "
            "has lost edges then. After rolling forward, check the warnings it "
            f"reports and {name}'s edge count (get_feature_definition)."
        )
    return out
