"""``list_model_dimensions`` says which dimensions an equation drives (``driven_by``)."""

from __future__ import annotations

from types import SimpleNamespace

from .fake_equations import FakeManager
from .test_documentation import _dim, _Feature, _Harness


def _part(texts: list[str]) -> tuple[SimpleNamespace, FakeManager]:
    mgr = FakeManager(texts)
    boss = _Feature("Boss-Extrude1", [_dim("D1@Boss-Extrude1@Part1.Part", 10.0)])
    sketch = _Feature("Sketch1", [_dim("D1@Sketch1@Part1.Part", 45.0)], boss)
    model = SimpleNamespace(
        GetType=lambda: 1, FirstFeature=lambda: sketch, GetEquationMgr=lambda: mgr
    )
    return model, mgr


async def test_a_driven_dimension_names_its_equation() -> None:
    model, _ = _part(['"W" = 45', '"D1@Sketch1" = "W"'])

    result = await _Harness(model).list_model_dimensions()

    assert result.is_success, result.error
    assert {d["full_name"]: d["driven_by"] for d in result.data} == {
        "D1@Sketch1": '"D1@Sketch1" = "W"',
        "D1@Boss-Extrude1": None,
    }


async def test_without_equations_it_is_one_count() -> None:
    model, mgr = _part([])

    result = await _Harness(model).list_model_dimensions()

    assert result.is_success, result.error
    assert all(d["driven_by"] is None for d in result.data)
    assert mgr.counts == 1
    assert mgr.reads == []
