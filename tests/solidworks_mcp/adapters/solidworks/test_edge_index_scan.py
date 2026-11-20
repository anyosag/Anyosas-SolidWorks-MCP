"""How many edges a scan would still describe, read before any is (``edge_index``)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import edge_index as ei

from .fake_pocket_plate import pocket_plate


class _Adapter:
    def _attempt(self, operation: Any, default: Any = None) -> Any:
        try:
            return operation()
        except Exception:  # noqa: BLE001
            return default


@pytest.mark.parametrize(
    ("raw", "limit"),
    [(None, 500), ("", 500), ("1200", 1200), ("0", 0), ("-3", 0), ("lots", 500)],
)
def test_the_scan_limit_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, limit: int
) -> None:
    if raw is None:
        monkeypatch.delenv("SOLIDWORKS_MCP_EDGE_SCAN_LIMIT", raising=False)
    else:
        monkeypatch.setenv("SOLIDWORKS_MCP_EDGE_SCAN_LIMIT", raw)

    assert ei.scan_limit() == limit


def test_a_body_not_read_yet_is_counted_in_one_call() -> None:
    model, log = pocket_plate()
    index = ei.PartIndex(_Adapter(), model, 7)

    assert index.edges_to_describe(index.bodies()) == 1_944
    assert log.calls["GetEdgeCount"] == 2
    assert log.calls["GetEdges"] == 0
    assert index.counts["edges_described"] == 0


def test_edges_already_described_are_not_counted_again() -> None:
    model, log = pocket_plate()
    index = ei.PartIndex(_Adapter(), model, 7)
    block = index.bodies()[1]
    for edge in index.body_edges(block)[:5]:
        index.describe(edge)

    # The block's edges are held: 7 of its 12 still to describe, no call.
    assert index.edges_to_describe([block]) == 7
    assert log.calls["GetEdgeCount"] == 0
    assert index.undescribed(index.body_edges(block)) == 7


@pytest.mark.parametrize("count", [None, "12", True, -1])
def test_a_count_that_does_not_read_falls_back_to_the_edges(count: Any) -> None:
    edges = [object(), object(), object()]
    body = SimpleNamespace(GetEdgeCount=lambda: count, GetEdges=lambda: edges)
    model = SimpleNamespace(GetBodies2=lambda *_a: [body])
    index = ei.PartIndex(_Adapter(), model, None)

    assert index.edges_to_describe(index.bodies()) == 3
