"""A new fillet or chamfer reports what it took: edges given, edges held, the bar.

The Dipper: ``select_edges`` picked 4 edges, ``add_fillet`` made Fillet3 with
the rollback bar above two Save Bodies features, and after rolling forward it
rounded 2 of them. The reply said nothing of it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import features

from .test_edge_feature_check import _chain, _Feature, _selection
from .test_features import _build_adapter, _FakeFeatureAdapter, _FilletAdapterSW2026


def _model(newest: Any, selected: list[int], counts: list[int]) -> Any:
    """A SolidWorks 2026 part: FeatureFillet3 returns 1, the tree grows."""
    return SimpleNamespace(
        FeatureFillet3=lambda *_a: 1,
        FeatureManager=SimpleNamespace(
            GetFeatureCount=lambda _top: counts.pop(0),
            InsertFeatureChamfer=lambda *_a: newest,
        ),
        Extension=SimpleNamespace(GetLastFeatureAdded=lambda: newest),
        SelectionManager=_selection(selected),
    )


def _adapter(model: Any) -> _FakeFeatureAdapter:
    adapter = _FilletAdapterSW2026()
    adapter.swApp = SimpleNamespace(RevisionNumber="34.0.0")
    adapter.currentModel = model
    return adapter


def test_a_fillet_records_the_edges_given_and_held_and_the_bar() -> None:
    fillet = _chain(
        _Feature("Fillet3", edges=2),
        _Feature("Save Bodies1", rolled=True),
        _Feature("Save Bodies2", rolled=True),
    )
    adapter = _adapter(_model(fillet, [1, 1, 1, 1], [160, 161]))

    result = features._add_fillet_impl(adapter, 0.8, [])

    assert result.is_success, result.error
    properties = result.data.properties
    assert properties["edges_selected"] == 4
    assert properties["selection_other"] == 0
    assert properties["edges_held"] == 2
    assert properties["checked_feature"] == "Fillet3"
    assert properties["features_below_bar"] == ["Save Bodies1", "Save Bodies2"]


def test_a_chamfer_records_them_from_the_feature_it_returns() -> None:
    chamfer = _Feature("Chamfer1", edges=3)
    adapter = _adapter(_model(chamfer, [1, 1, 2], [5, 6]))

    result = features._add_chamfer_impl(adapter, 0.5, [])

    assert result.is_success, result.error
    properties = result.data.properties
    assert (properties["edges_selected"], properties["selection_other"]) == (2, 1)
    assert properties["edges_held"] == 3
    assert properties["features_below_bar"] == []


def test_an_unreadable_selection_records_nothing_selected() -> None:
    model = _model(_Feature("Fillet1"), [], [1, 2])
    model.SelectionManager = None
    adapter = _adapter(model)

    result = features._add_fillet_impl(adapter, 1.0, [])

    assert result.data.properties["edges_selected"] is None
    assert result.data.properties["selection_other"] is None


class _Named(_FakeFeatureAdapter):
    """An adapter whose part finds features by name."""

    def __init__(self, *tree: _Feature) -> None:
        super().__init__()
        self.reads: list[str] = []
        named = {f.Name: f for f in tree}
        self.currentModel = SimpleNamespace(FeatureByName=named.get)

    def _handle_com_operation(self, name: str, callback: Any) -> Any:
        self.reads.append(name)
        return super()._handle_com_operation(name, callback)


def _fillet(name: str, **properties: Any) -> Any:
    return features.SolidWorksFeature(
        name=name, type="Fillet", id="", parameters={}, properties=dict(properties)
    )


def test_facts_of_another_feature_are_read_again_by_the_trees_name() -> None:
    adapter = _Named(
        _chain(_Feature("Fillet3", edges=2), _Feature("Cut4", rolled=True))
    )
    fillet = _fillet("Fillet3", checked_feature="Fillet2", edges_held=6)

    features._facts_by_name(adapter, fillet)

    assert adapter.reads == ["read_new_feature"]
    assert fillet.properties["checked_feature"] == "Fillet3"
    assert fillet.properties["edges_held"] == 2
    assert fillet.properties["features_below_bar"] == ["Cut4"]


def test_facts_that_cannot_be_read_by_name_are_dropped() -> None:
    adapter = _Named()
    fillet = _fillet("Fillet3", checked_feature="Fillet2", edges_held=6)

    features._facts_by_name(adapter, fillet)

    assert fillet.properties["edges_held"] is None
    assert fillet.properties["features_below_bar"] is None


@pytest.mark.parametrize(
    "fillet",
    [
        _fillet("Fillet3", checked_feature="Fillet3", edges_held=2),
        _fillet("Fillet", checked_feature="Fillet7"),  # the tree gave no name
    ],
)
def test_facts_of_the_right_feature_are_kept(fillet: Any) -> None:
    adapter = _Named(_Feature("Fillet3", edges=9))
    kept = dict(fillet.properties)

    features._facts_by_name(adapter, fillet)

    assert adapter.reads == []
    assert fillet.properties == kept


def _summary(volume: float | None) -> AdapterResult:
    return AdapterResult(
        status=AdapterResultStatus.SUCCESS,
        data={"volume_mm3": volume, "solid_body_count": 2},
    )


def _feature(kind: str) -> AdapterResult:
    return AdapterResult(
        status=AdapterResultStatus.SUCCESS,
        data=features.SolidWorksFeature(
            name=kind, type=kind, id="", parameters={}, properties={}
        ),
    )


@pytest.mark.asyncio
async def test_a_fillet_keeps_the_summaries_it_read(monkeypatch) -> None:
    adapter = _build_adapter(monkeypatch)
    adapter.get_model_geometry_summary = AsyncMock(
        side_effect=[_summary(4800.0), _summary(4797.4)]
    )
    monkeypatch.setattr(features, "_add_fillet_impl", lambda *_a: _feature("Fillet"))

    result = await adapter.add_fillet(0.8, [])

    assert result.data.properties["geometry_before"]["volume_mm3"] == 4800.0
    assert result.data.properties["geometry_after"]["volume_mm3"] == 4797.4


@pytest.mark.asyncio
async def test_a_chamfer_reads_the_part_before_and_after(monkeypatch) -> None:
    adapter = _build_adapter(monkeypatch)
    adapter.get_model_geometry_summary = AsyncMock(
        side_effect=[_summary(4800.0), _summary(4799.0)]
    )
    monkeypatch.setattr(features, "_add_chamfer_impl", lambda *_a: _feature("Chamfer"))

    result = await adapter.add_chamfer(0.5, [], 45.0, False)

    assert result.is_success
    assert result.data.properties["geometry_before"]["volume_mm3"] == 4800.0
    assert result.data.properties["geometry_after"]["volume_mm3"] == 4799.0


@pytest.mark.asyncio
async def test_a_chamfer_without_a_volume_reads_only_once(monkeypatch) -> None:
    adapter = _build_adapter(monkeypatch)
    adapter.get_model_geometry_summary = AsyncMock(return_value=_summary(None))
    monkeypatch.setattr(features, "_add_chamfer_impl", lambda *_a: _feature("Chamfer"))

    result = await adapter.add_chamfer(0.5, [])

    assert "geometry_before" not in result.data.properties
    adapter.get_model_geometry_summary.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failed_chamfer_is_returned_unchanged(monkeypatch) -> None:
    adapter = _build_adapter(monkeypatch)
    adapter.get_model_geometry_summary = AsyncMock(return_value=_summary(4800.0))
    failed = AdapterResult(status=AdapterResultStatus.ERROR, error="nope")
    monkeypatch.setattr(features, "_add_chamfer_impl", lambda *_a: failed)

    assert await adapter.add_chamfer(0.5, ["0,0,0"]) is failed
