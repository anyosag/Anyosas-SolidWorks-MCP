"""``delete_feature`` names what went without listing the tree twice.

The features a delete can take are read beforehand (``IFeature.GetChildren``,
followed down, and the sketches absorbed), each is looked up by name
afterwards, and the tree is listed once only when ``GetFeatureCount`` fell by
another number. The fake tree deletes as SolidWorks was measured to
(``fake_feature_tree``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import features
from solidworks_mcp.adapters.solidworks.features import (
    _children_of,
    _delete_feature_impl,
    _tree_order,
)

from .fake_feature_tree import FakeTree


def _adapter(monkeypatch: pytest.MonkeyPatch, tree: Any) -> PyWin32Adapter:
    """A PyWin32Adapter with no COM behind it, on ``tree``."""
    target = "solidworks_mcp.adapters.pywin32_adapter"
    monkeypatch.setattr(f"{target}.PYWIN32_AVAILABLE", True)
    monkeypatch.setattr(f"{target}.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        f"{target}.pywintypes", SimpleNamespace(com_error=RuntimeError), raising=False
    )
    monkeypatch.setattr(
        f"{target}._ComSessionCoordinator",
        lambda _adapter: SimpleNamespace(),
        raising=False,
    )
    adapter = PyWin32Adapter({})
    adapter.currentModel = tree
    adapter.list_features = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda **_options: AdapterResult(
            status=AdapterResultStatus.SUCCESS, data=tree.listing()
        )
    )
    return adapter


def _plate() -> FakeTree:
    """A boss, a sketch on its face and the cut from it, and a fillet on both.

    The fillet is built on the boss and the cut; ``GetChildren`` on the boss
    lists it before the sketch, so found order alone would put it above the
    cut it is built on.
    """
    tree = FakeTree()
    tree.add("Sketch1", "ProfileFeature")
    tree.add("Boss-Extrude1", sketch="Sketch1")
    tree.add("Sketch2", "ProfileFeature", on=("Boss-Extrude1",))
    tree.add("Cut-Extrude1", "Cut", sketch="Sketch2")
    tree.add("Fillet1", "Fillet", on=("Boss-Extrude1", "Cut-Extrude1"))
    tree.features["Boss-Extrude1"].children.reverse()
    tree.add("Plane1", "RefPlane")
    return tree


async def test_a_feature_goes_with_everything_built_on_it_in_tree_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _plate()
    adapter = _adapter(monkeypatch, tree)

    result = await adapter.delete_feature("Boss-Extrude1")

    assert result.is_success, result.error
    assert result.data["removed_features"] == [
        "Boss-Extrude1",
        "Sketch2",
        "Cut-Extrude1",
        "Fillet1",
    ]
    assert result.data["features_removed"] == 4
    assert result.data["absorbed_sketches_left"] == ["Sketch1"]
    assert "removed_unnamed" not in result.data
    assert tree.present == ["Sketch1", "Plane1"]
    adapter.list_features.assert_not_awaited()


async def test_a_feature_reached_twice_is_read_once_and_placed_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _plate()
    tree.features["Boss-Extrude1"].children.reverse()  # GetChildren: sketch first
    adapter = _adapter(monkeypatch, tree)

    result = await adapter.delete_feature("Boss-Extrude1")

    # The fillet is waiting to be read when the cut reaches it again.
    assert result.data["removed_features"] == [
        "Boss-Extrude1",
        "Sketch2",
        "Cut-Extrude1",
        "Fillet1",
    ]


async def test_with_its_sketch_the_boss_takes_the_whole_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _plate()
    adapter = _adapter(monkeypatch, tree)

    result = await adapter.delete_feature("Boss-Extrude1", True)

    assert result.data["removed_features"] == [
        "Sketch1",
        "Boss-Extrude1",
        "Sketch2",
        "Cut-Extrude1",
        "Fillet1",
    ]
    assert result.data["absorbed_sketches_left"] == []
    assert tree.present == ["Plane1"]
    # Each feature that could go was looked up once before the delete and
    # once after it; the tree was never walked.
    assert tree.lookups.count("Fillet1") == 2
    adapter.list_features.assert_not_awaited()


async def test_a_dependent_this_document_cannot_find_is_not_a_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Say an in-context feature of another document, built on this one's cut."""
    tree = _plate()
    adapter = _adapter(monkeypatch, tree)
    monkeypatch.setattr(
        features,
        "_removal_candidates",
        lambda *_a: ["Cut-Extrude1", "Extrude-Thin1", "Fillet1"],
    )

    result = await adapter.delete_feature("Cut-Extrude1")

    # Never found here, before the delete or after: not reported as gone.
    assert result.data["removed_features"] == ["Cut-Extrude1", "Fillet1"]
    adapter.list_features.assert_not_awaited()


async def test_a_shared_sketch_takes_its_other_cut_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = FakeTree()
    tree.add("Boss-Extrude1")
    tree.add("Sketch3", "ProfileFeature")
    tree.add("Cut-Extrude1", "Cut", sketch="Sketch3")
    tree.add("Cut-Extrude2", "Cut", sketch="Sketch3")
    adapter = _adapter(monkeypatch, tree)

    result = await adapter.delete_feature("Cut-Extrude1", True)

    assert result.data["removed_features"] == [
        "Sketch3",
        "Cut-Extrude1",
        "Cut-Extrude2",
    ]
    assert result.data["features_removed"] == 3
    adapter.list_features.assert_not_awaited()


async def test_more_gone_than_named_lists_the_tree_once_and_counts_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _plate()
    tree.miscount = -1  # one more feature went than the dependents name
    adapter = _adapter(monkeypatch, tree)

    result = await adapter.delete_feature("Cut-Extrude1")

    assert result.data["removed_features"] == ["Cut-Extrude1", "Fillet1"]
    assert result.data["features_removed"] == 3
    assert result.data["removed_unnamed"] == 1
    adapter.list_features.assert_awaited_once_with(include_suppressed=True)


async def test_a_name_still_in_the_tree_is_dropped_after_the_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _plate()
    tree.unfindable = {"Sketch2"}  # FeatureByName misses it; the tree lists it
    adapter = _adapter(monkeypatch, tree)

    result = await adapter.delete_feature("Cut-Extrude1")

    # Sketch2 looked gone, so the count (2) disagreed with three names.
    assert result.data["removed_features"] == ["Cut-Extrude1", "Fillet1"]
    assert "removed_unnamed" not in result.data
    adapter.list_features.assert_awaited_once()


async def test_fewer_gone_than_named_keeps_the_names_the_tree_lacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _plate()
    tree.miscount = 1  # say, a sketch the count doesn't hold
    adapter = _adapter(monkeypatch, tree)

    result = await adapter.delete_feature("Cut-Extrude1")

    assert result.data["removed_features"] == ["Cut-Extrude1", "Fillet1"]
    assert "removed_unnamed" not in result.data
    adapter.list_features.assert_awaited_once()


async def test_a_listing_that_fails_keeps_the_names_and_counts_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _plate()
    tree.miscount = -2
    adapter = _adapter(monkeypatch, tree)
    adapter.list_features = AsyncMock(  # type: ignore[method-assign]
        return_value=AdapterResult(status=AdapterResultStatus.ERROR, error="no tree")
    )

    result = await adapter.delete_feature("Cut-Extrude1")

    assert result.data["removed_features"] == ["Cut-Extrude1", "Fillet1"]
    assert result.data["removed_unnamed"] == 2


async def test_an_assembly_s_component_features_do_not_hide_a_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _plate()
    tree.miscount = -1
    adapter = _adapter(monkeypatch, tree)
    adapter.list_features = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda **_options: AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=[*tree.listing(), {"name": "Fillet1", "component": "Bracket-1"}],
        )
    )

    result = await adapter.delete_feature("Cut-Extrude1")

    # The component's own Fillet1 is another feature: this one still went.
    assert result.data["removed_features"] == ["Cut-Extrude1", "Fillet1"]


async def test_an_unreadable_count_checks_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _plate()
    tree.FeatureManager = None  # GetFeatureCount can't be read
    adapter = _adapter(monkeypatch, tree)

    result = await adapter.delete_feature("Cut-Extrude1")

    assert result.data["features_removed"] is None
    assert result.data["removed_features"] == ["Cut-Extrude1", "Fillet1"]
    adapter.list_features.assert_not_awaited()


def test_an_unreadable_name_falls_back_to_the_one_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter(monkeypatch, MagicMock())
    nameless = SimpleNamespace(Select2=lambda *_a: True)  # no Name, no children
    adapter.currentModel.FeatureByName.side_effect = [nameless, None]
    adapter.currentModel.FeatureManager.GetFeatureCount.side_effect = [5, 4]

    result = _delete_feature_impl(adapter, "Fillet1@Part1", name_removed=True)

    assert result.is_success, result.error
    assert result.data["removed_features"] == ["Fillet1"]


def test_without_name_removed_nothing_more_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _plate()
    adapter = _adapter(monkeypatch, tree)

    result = _delete_feature_impl(adapter, "Fillet1")

    assert result.is_success, result.error
    assert "removed_features" not in result.data
    assert tree.lookups == ["Fillet1", "Fillet1"]  # the select, then the check


def test_children_without_a_name_are_left_out() -> None:
    adapter = SimpleNamespace(
        _attempt=lambda fn, default=None: _safe(fn, default),
        _get_attr_or_call=lambda obj, name: getattr(obj, name)(),
    )
    cut = SimpleNamespace(Name="Cut-Extrude1")
    sketch = SimpleNamespace(
        GetChildren=lambda: (SimpleNamespace(Name=None), cut, SimpleNamespace())
    )

    assert _children_of(adapter, sketch) == [("Cut-Extrude1", cut)]
    assert _children_of(adapter, SimpleNamespace()) == []


def _safe(fn: Any, default: Any) -> Any:
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


def test_names_in_a_loop_come_last_in_the_order_found() -> None:
    found = {"Plane1": 0, "Sketch1": 1, "Boss-Extrude1": 2, "Fillet1": 3}
    edges = {
        ("Sketch1", "Boss-Extrude1"),
        ("Boss-Extrude1", "Sketch1"),  # no real tree has a loop
        ("Plane1", "Fillet1"),
        ("Plane1", "Plane1"),  # a feature above itself is ignored
        ("Plane1", "Elsewhere"),  # an edge to a name not found is ignored
    }

    assert _tree_order(found, edges) == [
        "Plane1",
        "Fillet1",
        "Sketch1",
        "Boss-Extrude1",
    ]


async def test_the_guard_stops_reading_a_runaway_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = _plate()
    adapter = _adapter(monkeypatch, tree)
    monkeypatch.setattr(features, "_MAX_REMOVAL_CANDIDATES", 2)

    result = await adapter.delete_feature("Boss-Extrude1")

    # Two read: the boss and its own sketch, which stays. The count (4) then
    # lists the tree, which can't name the other three.
    assert result.data["removed_features"] == ["Boss-Extrude1"]
    assert result.data["removed_unnamed"] == 3
    adapter.list_features.assert_awaited_once()
