"""A rule with no source is refused before it describes too many edges.

One session's 31 ``select_edges`` queries without a ``feature`` took 33 of
the tool's 42 minutes, about 0.1 s an edge described. ``fake_pocket_plate``
has 1,944 edges on two bodies; every COM call is counted.
"""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import edge_rules as er

from .fake_pocket_plate import pocket_box_mm, pocket_plate


class _Adapter:
    def __init__(self, model: Any) -> None:
        self.currentModel = model

    def _attempt(self, operation: Any, default: Any = None) -> Any:
        try:
            return operation()
        except Exception:  # noqa: BLE001
            return default


#: A box over the whole plate: every pocket's edges are in reach.
_LOOSE_BOX = [[-1.0, -1.0, -1.0], [201.0, 161.0, 11.0]]


def test_the_whole_part_is_refused_before_an_edge_is_read() -> None:
    model, log = pocket_plate()
    selector = er.EdgeRuleSelector(_Adapter(model), scan_limit=500)

    with pytest.raises(er.ScanTooLarge) as refused:
        selector.candidates({"parallel_to": "x"})

    assert str(refused.value) == (
        "Not scanned: the part has 1944 edges to read, over the edge scan limit of "
        "500 (SOLIDWORKS_MCP_EDGE_SCAN_LIMIT); describing them would take about "
        "194 s. Nothing was read or selected. Take the edges from a feature, "
        "face_point, side or plane, or narrow the scan with inside_box; "
        "scan_whole_part: true reads them all."
    )
    assert refused.value.data() == {
        "edges_to_read": 1944,
        "estimated_s": 194,
        "scan_limit": 500,
        "source": "the part",
        "inside_box": False,
    }
    # Two counts; no edge listed or described.
    assert log.calls["GetEdgeCount"] == 2
    assert log.calls["GetEdges"] == 0 and log.calls["GetCurve"] == 0


def test_one_small_body_is_under_the_limit() -> None:
    model, log = pocket_plate()
    selector = er.EdgeRuleSelector(_Adapter(model), scan_limit=500)

    kept = selector.candidates({"body": "Block"})

    assert len(kept) == 12
    assert log.calls["GetEdgeCount"] == 1


def test_a_loose_box_is_refused_after_its_face_boxes() -> None:
    model, log = pocket_plate()
    selector = er.EdgeRuleSelector(_Adapter(model), scan_limit=500)

    with pytest.raises(er.ScanTooLarge) as refused:
        selector.candidates({"inside_box": _LOOSE_BOX})

    assert refused.value.boxed is True
    assert refused.value.edges == 1932  # the plate's; the block is out of reach
    assert str(refused.value).startswith(
        "Not scanned: inside_box reaches 1932 edges of the part to read, over the "
        "edge scan limit of 500"
    )
    assert "the face boxes read are kept. Give a tighter inside_box" in str(
        refused.value
    )
    # The face boxes were read (and kept); no edge was described.
    assert log.calls["GetBox"] == 806
    assert log.calls["GetCurve"] == 0
    assert selector.index.counts["edges_described"] == 0


def test_a_tight_box_on_the_same_part_goes_ahead() -> None:
    model, _log = pocket_plate()
    selector = er.EdgeRuleSelector(_Adapter(model), scan_limit=500)

    kept = selector.candidates({"inside_box": pocket_box_mm(2, 3)})

    assert len(kept) == 12


def test_a_source_is_never_counted() -> None:
    model, log = pocket_plate()
    selector = er.EdgeRuleSelector(_Adapter(model), scan_limit=10)

    kept = selector.candidates({"feature": "Boss-Extrude1"})

    assert len(kept) > 10
    assert log.calls["GetEdgeCount"] == 0


@pytest.mark.parametrize("limit", [None, 0])
def test_without_a_limit_nothing_is_counted(limit: int | None) -> None:
    model, log = pocket_plate()
    selector = er.EdgeRuleSelector(_Adapter(model), scan_limit=limit)

    kept = selector.candidates({"body": "Plate"})

    assert len(kept) == 1932
    assert log.calls["GetEdgeCount"] == 0


def test_edges_the_index_has_described_are_free() -> None:
    model, log = pocket_plate()
    adapter = _Adapter(model)
    er.EdgeRuleSelector(adapter).candidates({"body": "Plate"})  # all described

    again = er.EdgeRuleSelector(adapter, scan_limit=500)
    kept = again.candidates({"body": "Plate"})

    assert again.index.reused is True
    assert len(kept) == 1932
    assert again.index.counts["edges_described"] == 0
    assert log.calls["GetEdgeCount"] == 0


@pytest.mark.parametrize(
    ("rule", "sourced"),
    [
        ({}, False),
        ({"body": "Plate", "inside_box": _LOOSE_BOX}, False),
        ({"feature": ""}, False),
        ({"feature": "Cut-Extrude1"}, True),
        ({"edge_ids": ["AP8="]}, True),
        ({"between_faces": [[0, 0, 0], [1, 1, 1]]}, True),
    ],
)
def test_which_rules_have_a_source(rule: dict[str, Any], sourced: bool) -> None:
    assert er.has_source(rule) is sourced


def test_the_estimate_rounds_to_whole_seconds() -> None:
    assert er.estimated_seconds(2904) == 290
    assert er.estimated_seconds(4) == 0
