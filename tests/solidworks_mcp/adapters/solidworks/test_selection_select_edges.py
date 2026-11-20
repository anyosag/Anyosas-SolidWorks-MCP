"""The adapter's ``select_edges``: ids out, what was read, a kept index gone stale."""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import edge_index as ei
from solidworks_mcp.adapters.solidworks.selection import SolidWorksSelectionMixin

from .fake_pocket_plate import pocket_box_mm, pocket_plate


class _Adapter(SolidWorksSelectionMixin):
    def __init__(self, model: Any) -> None:
        self.currentModel = model
        self.swApp = None

    def _attempt(self, operation: Any, default: Any = None) -> Any:
        try:
            return operation()
        except Exception:  # noqa: BLE001
            return default

    def _handle_com_operation(self, name: str, operation: Any, *args: Any) -> Any:
        try:
            return AdapterResult(
                status=AdapterResultStatus.SUCCESS, data=operation(*args)
            )
        except Exception as exc:  # noqa: BLE001 - mirror the real adapter
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=f"Error in {name}: {exc}"
            )


async def test_ids_for_the_selected_edges_and_what_was_read() -> None:
    model, _log = pocket_plate()
    adapter = _Adapter(model)

    result = await adapter.select_edges(
        {"inside_box": pocket_box_mm(1, 2), "with_ids": True}
    )
    again = await adapter.select_edges({"inside_box": pocket_box_mm(1, 2)})

    assert result.is_success, result.error
    data = result.data or {}
    assert data["selected_edges"] == 12
    assert len(data["edge_ids"]) == 12 and all(data["edge_ids"])
    assert data["read"] == {
        "kept_from_earlier_call": False,
        "edges_described": 12,
        "face_boxes": 806,
    }
    assert (again.data or {})["read"] == {
        "kept_from_earlier_call": True,
        "edges_described": 0,
        "face_boxes": 0,
    }
    assert "edge_ids" not in (again.data or {})


async def test_ids_come_back_as_the_same_edges() -> None:
    model, _log = pocket_plate()
    adapter = _Adapter(model)
    first = await adapter.select_edges(
        {"inside_box": pocket_box_mm(0, 3), "with_ids": True}
    )
    model.stamp = 8  # rebuilt

    again = await adapter.select_edges({"edge_ids": (first.data or {})["edge_ids"]})

    assert again.is_success, again.error
    assert (again.data or {})["edges"] == (first.data or {})["edges"]


async def test_too_many_edges_for_ids_select_nothing() -> None:
    model, _log = pocket_plate()

    result = await _Adapter(model).select_edges(
        {"feature": "Boss-Extrude1", "with_ids": True}
    )

    assert result.error == (
        "with_ids gives ids for up to 100 edges, and these rules match 652; narrow "
        "them (inside_box, a feature or face source), or leave with_ids out."
    )
    assert not any(e.selected for e in model.bodies[0].edges)


async def test_edges_from_a_kept_index_that_will_not_select_are_read_again() -> None:
    model, log = pocket_plate()
    adapter = _Adapter(model)
    await adapter.select_edges({"inside_box": pocket_box_mm(4, 4)})
    kept = adapter._edge_index
    assert isinstance(kept, ei.PartIndex)
    stale = next(iter(kept._info.values()))[0]
    stale.refuse_select = 1  # gone stale: the next Select4 is refused
    boxes = log.calls["GetBox"]

    result = await adapter.select_edges({"inside_box": pocket_box_mm(4, 4)})

    assert result.is_success, result.error
    assert adapter._edge_index is not kept
    assert log.calls["GetBox"] - boxes == 806  # the part was read anew


async def test_a_fresh_index_that_will_not_select_is_an_error() -> None:
    model, _log = pocket_plate()
    for edge in model.bodies[1].edges:
        edge.refuse_select = 99

    result = await _Adapter(model).select_edges({"body": "Block"})

    assert result.is_error
    assert "could not be selected" in (result.error or "")


# ---- the scan limit --------------------------------------------------------------


async def test_a_scan_of_the_whole_part_is_refused_with_its_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SOLIDWORKS_MCP_EDGE_SCAN_LIMIT", raising=False)
    model, log = pocket_plate()

    result = await _Adapter(model).select_edges({"parallel_to": "x"})

    assert result.is_error
    assert (result.error or "").startswith(
        "Not scanned: the part has 1944 edges to read, over the edge scan limit of 500"
    )
    assert result.data == {
        "edges_to_read": 1944,
        "estimated_s": 194,
        "scan_limit": 500,
        "source": "the part",
        "inside_box": False,
    }
    assert log.calls["GetCurve"] == 0
    assert not any(e.selected for b in model.bodies for e in b.edges)


async def test_scan_whole_part_reads_them_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOLIDWORKS_MCP_EDGE_SCAN_LIMIT", raising=False)
    model, log = pocket_plate()

    result = await _Adapter(model).select_edges(
        {"body": "Block", "parallel_to": "x", "scan_whole_part": True}
    )
    whole = await _Adapter(pocket_plate()[0]).select_edges(
        {"edge_type": "linear", "scan_whole_part": True}
    )

    assert result.is_success, result.error
    assert (result.data or {})["selected_edges"] == 4
    assert log.calls["GetEdgeCount"] == 0  # nothing to count against
    assert whole.is_success, whole.error
    assert (whole.data or {})["selected_edges"] == 1944


async def test_a_limit_set_to_0_turns_the_check_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOLIDWORKS_MCP_EDGE_SCAN_LIMIT", "0")
    model, _log = pocket_plate()

    result = await _Adapter(model).select_edges({"parallel_to": "y"})

    assert result.is_success, result.error


async def test_any_rule_over_the_limit_refuses_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOLIDWORKS_MCP_EDGE_SCAN_LIMIT", "100")
    model, _log = pocket_plate()

    result = await _Adapter(model).select_edges(
        {"any_of": [{"feature": "Cut-Extrude1"}, {"body": "Plate"}]}
    )

    assert result.is_error
    assert (result.data or {})["source"] == "body 'Plate'"
    assert (result.data or {})["scan_limit"] == 100
    assert not any(e.selected for b in model.bodies for e in b.edges)
