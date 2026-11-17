"""What a new fillet or chamfer took (``edge_feature_check``).

The Dipper: a fillet made with the rollback bar up took 4 edges, and after
rolling forward it warned swFeatureErrorFilletNoEdge and rounded 2.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import edge_feature_check as efc


class _Adapter:
    def __init__(self, model: Any = None) -> None:
        self.currentModel = model

    def _attempt(self, operation: Any, default: Any = None) -> Any:
        try:
            return operation()
        except Exception:  # noqa: BLE001
            return default


class _Feature:
    """A tree feature: its definition holds ``edges``; ``rolled`` is below the bar."""

    def __init__(self, name: str, edges: int | None = 2, rolled: Any = False):
        self.Name = name
        self.rolled = rolled
        self.next: _Feature | None = None
        self.data = (
            None if edges is None else SimpleNamespace(GetEdgeCount=lambda: edges)
        )

    def GetDefinition(self) -> Any:  # noqa: N802
        return self.data

    def GetNextFeature(self) -> Any:  # noqa: N802
        return self.next

    def IsRolledBack(self) -> Any:  # noqa: N802
        return self.rolled


def _chain(*features: _Feature) -> _Feature:
    for first, second in zip(features, features[1:], strict=False):
        first.next = second
    return features[0]


def _selection(types: list[int]) -> Any:
    return SimpleNamespace(
        GetSelectedObjectCount2=lambda _mark: len(types),
        GetSelectedObjectType3=lambda index, _mark: types[index - 1],
    )


def test_the_selection_is_counted_as_edges_and_the_rest() -> None:
    model = SimpleNamespace(SelectionManager=_selection([1, 1, 2, 1]))

    assert efc.selection_counts(_Adapter(model)) == {"edges": 3, "other": 1}
    assert efc.selection_counts(
        _Adapter(SimpleNamespace(SelectionManager=_selection([])))
    ) == {"edges": 0, "other": 0}


@pytest.mark.parametrize(
    "model",
    [
        SimpleNamespace(),  # no SelectionManager
        SimpleNamespace(
            SelectionManager=SimpleNamespace(GetSelectedObjectCount2=lambda _m: True)
        ),
        SimpleNamespace(
            SelectionManager=SimpleNamespace(GetSelectedObjectCount2=lambda _m: "2")
        ),
    ],
)
def test_an_unreadable_selection_is_not_counted(model: Any) -> None:
    assert efc.selection_counts(_Adapter(model)) is None


def test_the_new_feature_is_the_one_returned_else_the_last_added() -> None:
    returned, last = _Feature("Chamfer2"), _Feature("Fillet3")
    model = SimpleNamespace(Extension=SimpleNamespace(GetLastFeatureAdded=lambda: last))

    assert efc.newest_feature(_Adapter(model), returned) is returned
    assert efc.newest_feature(_Adapter(model), 1) is last  # FeatureFillet3's number
    assert efc.newest_feature(_Adapter(model), None) is last
    assert efc.newest_feature(_Adapter(SimpleNamespace()), None) is None


def test_the_features_after_a_feature_made_with_the_bar_up_are_named() -> None:
    fillet = _chain(
        _Feature("Fillet3"),
        _Feature("Save Bodies1", rolled=True),
        _Feature("Save Bodies2", rolled=True),
    )

    assert efc.features_below_bar(_Adapter(), fillet) == [
        "Save Bodies1",
        "Save Bodies2",
    ]
    assert efc.features_below_bar(_Adapter(), _Feature("Fillet4")) == []


def test_a_tree_that_does_not_read_gives_no_answer() -> None:
    fillet = _chain(_Feature("Fillet3"), _Feature("Odd", rolled=None))

    assert efc.features_below_bar(_Adapter(), fillet) is None


def test_the_walk_past_the_feature_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    looped = _Feature("Fillet3", rolled=True)
    looped.next = looped
    monkeypatch.setattr(efc, "_MAX_FEATURES", 3)

    assert efc.features_below_bar(_Adapter(), looped) == ["Fillet3"] * 3


def test_a_features_facts_are_its_name_edges_and_the_bar() -> None:
    fillet = _chain(_Feature("Fillet3", edges=2), _Feature("Cut4", rolled=True))

    assert efc.feature_facts(_Adapter(), fillet) == {
        "checked_feature": "Fillet3",
        "edges_held": 2,
        "features_below_bar": ["Cut4"],
    }
    assert efc.feature_facts(_Adapter(), None) == {
        "checked_feature": None,
        "edges_held": None,
        "features_below_bar": None,
    }
    nameless = _Feature("x", edges=None)
    nameless.Name = None  # type: ignore[assignment]
    assert efc.feature_facts(_Adapter(), nameless)["checked_feature"] is None
    assert efc.feature_facts(_Adapter(), nameless)["edges_held"] is None


def test_a_fillet_that_kept_fewer_edges_is_warned_about() -> None:
    warnings = efc.warnings(
        "fillet",
        "Fillet3",
        {"edges_selected": 4, "selection_other": 0, "edges_held": 2},
    )

    assert warnings == [
        "Fillet3 holds 2 of the 4 edges selected: SolidWorks left 2 out. To add "
        "them, select every edge it should treat (select_edges) and call "
        'edit_feature with edges: "selection", or undo and select them again.'
    ]


@pytest.mark.parametrize(
    "facts",
    [
        {"edges_selected": 4, "selection_other": 0, "edges_held": 4},
        {"edges_selected": 2, "selection_other": 0, "edges_held": 6},  # propagated
        {"edges_selected": 1, "selection_other": 1, "edges_held": 0},  # a face too
        {"edges_selected": None, "edges_held": 2},
        {"edges_selected": 4, "edges_held": None},
        {"features_below_bar": []},
        {"features_below_bar": None},
    ],
)
def test_otherwise_nothing_is_said(facts: dict[str, Any]) -> None:
    assert efc.warnings("chamfer", "Chamfer1", facts) == []


def test_a_feature_made_with_the_bar_up_is_to_be_checked_after_rolling_forward() -> (
    None
):
    few = efc.warnings("fillet", "Fillet3", {"features_below_bar": ["Save Bodies1"]})
    many = efc.warnings(
        "chamfer", "Chamfer2", {"features_below_bar": [f"F{i}" for i in range(7)]}
    )

    assert few[0].startswith(
        "The rollback bar is up: 1 feature(s) after Fillet3 (Save Bodies1) are not "
        "built yet."
    )
    assert "set_rollback with no input" in few[0]
    assert "a fillet made with the bar up has lost edges then" in few[0]
    assert "(F0, F1, F2, F3, F4 and 2 more)" in many[0]
