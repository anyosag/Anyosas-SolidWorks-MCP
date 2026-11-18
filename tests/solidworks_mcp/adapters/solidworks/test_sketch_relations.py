"""Tests for solidworks_mcp.adapters.solidworks.sketch_relations (fake COM)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import sketch_relations


class _Adapter:
    def _attempt(self, callback, default=None):
        try:
            return callback()
        except Exception:
            return default


class _Manager:
    def __init__(self, relations: list[Any], counts: dict[int, int] | None = None):
        self._relations, self._counts = relations, counts or {}

    def GetRelations(self, _kind: int) -> tuple:
        return tuple(self._relations)

    def GetRelationsCount(self, kind: int) -> int:
        if kind not in self._counts:
            raise RuntimeError("no count")
        return self._counts[kind]


def _relation(kind: int, entities: list[Any], types: list[int]) -> SimpleNamespace:
    return SimpleNamespace(
        GetRelationType=lambda: kind,
        GetEntities=lambda: tuple(entities),
        GetEntitiesType=lambda: tuple(types),
    )


def _segment(key: tuple[int, int]) -> SimpleNamespace:
    return SimpleNamespace(GetID=lambda: key)


@pytest.mark.parametrize(
    ("raw", "state", "fully"),
    [
        (3, "fully_defined", True),
        (2, "under_defined", False),
        (4, "over_defined", False),
        (5, "no_solution", False),
        (1, "unknown", None),
        (7, "autosolve_off", None),
    ],
)
def test_constrained_status_maps_the_enum(raw: int, state: str, fully: bool | None):
    sketch = SimpleNamespace(
        GetConstrainedStatus=lambda: raw,
        RelationManager=_Manager([], {1: 0, 2: 1}),
    )

    reply = sketch_relations.constrained_status(_Adapter(), sketch)

    assert reply == {
        "is_fully_defined": fully,
        "definition_state": state,
        "source": "ISketch.GetConstrainedStatus",
        "raw_status": raw,
        "over_defining_relations": 1,
        "dangling_relations": 0,
    }


def test_constrained_status_that_does_not_read_is_unknown() -> None:
    reply = sketch_relations.constrained_status(_Adapter(), SimpleNamespace())

    assert reply == {
        "is_fully_defined": None,
        "definition_state": "unknown",
        "source": "unavailable",
        "raw_status": None,
    }


def test_relation_count_reads_or_gives_none() -> None:
    manager = _Manager([], {0: 4})

    assert sketch_relations.relation_count(_Adapter(), manager) == 4
    assert sketch_relations.relation_count(_Adapter(), manager, 2) is None
    assert sketch_relations.relation_count(_Adapter(), None) is None


@pytest.mark.parametrize(
    ("value", "name"), [(9, "coincident"), (17, "fix"), (14, "equal"), (99, "type_99")]
)
def test_relation_name(value: int, name: str) -> None:
    assert sketch_relations.relation_name(value) == name
    assert sketch_relations.relation_name(None) == "unknown"


def test_entity_key() -> None:
    assert sketch_relations.entity_key(_Adapter(), _segment((2, 7))) == (2, 7)
    assert sketch_relations.entity_key(_Adapter(), SimpleNamespace()) is None


def test_list_relations_names_segments_and_points_and_counts_dimensions() -> None:
    line, circle = _segment((0, 1)), _segment((1, 2))
    point = SimpleNamespace(X=0.008, Y=0.0)
    sketch = SimpleNamespace(
        RelationManager=_Manager(
            [
                _relation(9, [point, line], [2, 3]),  # coincident: point on line
                _relation(5, [line], [3]),  # vertical
                _relation(15, [circle], [4]),  # a diameter dimension
                _relation(10, [circle, SimpleNamespace(X="?", Y=0)], [4, 2]),
                _relation(4, [_segment((9, 9))], []),
            ]
        )
    )

    reply = sketch_relations.list_relations(_Adapter(), sketch, {(0, 1): 0, (1, 2): 1})

    assert reply["relations"] == [
        {
            "type": "coincident",
            "entities": [
                {"kind": "point", "at_mm": [8.0, 0.0]},
                {"kind": "line", "segment": 0},
            ],
        },
        {"type": "vertical", "entities": [{"kind": "line", "segment": 0}]},
        {
            "type": "concentric",
            "entities": [{"kind": "arc", "segment": 1}, {"kind": "point"}],
        },
        {"type": "horizontal", "entities": [{"kind": "unknown"}]},
    ]
    assert reply["relation_counts"] == {
        "coincident": 1,
        "vertical": 1,
        "diameter": 1,
        "concentric": 1,
        "horizontal": 1,
    }
    assert reply["dimension_relations"] == 1
    assert "relations_truncated" not in reply


def test_list_relations_stops_describing_past_the_limit() -> None:
    sketch = SimpleNamespace(
        RelationManager=_Manager([_relation(17, [], []) for _ in range(3)])
    )

    reply = sketch_relations.list_relations(_Adapter(), sketch, limit=2)

    assert len(reply["relations"]) == 2
    assert reply["relation_counts"] == {"fix": 3}
    assert reply["relations_truncated"] is True


def test_list_relations_without_a_manager_lists_none() -> None:
    reply = sketch_relations.list_relations(_Adapter(), SimpleNamespace())

    assert reply == {"relations": [], "relation_counts": {}, "dimension_relations": 0}


def test_raw_relations_are_wrapped(monkeypatch) -> None:
    class PyIDispatch:
        pass

    from solidworks_mcp.adapters.solidworks import io

    wrapped = _relation(6, [], [])
    monkeypatch.setattr(io, "_dynamic", SimpleNamespace(Dispatch=lambda _o: wrapped))
    sketch = SimpleNamespace(RelationManager=_Manager([PyIDispatch()]))

    reply = sketch_relations.list_relations(_Adapter(), sketch)

    assert reply["relations"] == [{"type": "tangent", "entities": []}]
