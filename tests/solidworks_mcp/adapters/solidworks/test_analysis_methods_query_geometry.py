"""The analysis mixin's ``query_geometry``: a part only, a bad query refused first."""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import geometry_query as gq

from .test_analysis_methods import _Harness


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    seen: list[tuple[Any, ...]] = []

    def query(_adapter: Any, *args: Any) -> dict[str, Any]:
        seen.append(args)
        return {"method": "index"}

    monkeypatch.setattr(gq, "query_geometry", query)
    return seen


async def test_a_part_is_queried_with_every_argument(asked: list[Any]) -> None:
    harness = _Harness(1)

    result = await harness.query_geometry([1.0, 2.0, 3.0], 4.0, "edges", 5, True)

    assert result.data == {"method": "index"}
    assert asked == [([1.0, 2.0, 3.0], 4.0, "edges", 5, True)]
    assert harness.operations == ["query_geometry"]


async def test_an_assembly_is_refused(asked: list[Any]) -> None:
    result = await _Harness(2).query_geometry([0.0, 0.0, 0.0])

    assert result.error == (
        "query_geometry needs an active part document (active document is assembly)."
    )
    assert asked == []


async def test_a_bad_query_is_refused_before_solidworks(asked: list[Any]) -> None:
    harness = _Harness(1)

    result = await harness.query_geometry([0.0, 0.0], 1.0)

    assert result.error == "point_mm must be [x, y, z] in millimetres."
    assert harness.operations == [] and asked == []
