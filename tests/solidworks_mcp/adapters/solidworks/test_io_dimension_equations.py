"""``set_dimension`` refuses a dimension an equation sets, before writing (D7)."""

from __future__ import annotations

from typing import Any

from solidworks_mcp.tools.error_classifier import ErrorClassifier

from .fake_equations import FakeManager
from .test_io_dimension_units import LENGTH, _Dimension, _Harness


class _EquationHarness(_Harness):
    def __init__(self, dimension: Any, texts: list[str]) -> None:
        super().__init__(dimension)
        self.mgr = FakeManager(texts)
        self.currentModel.GetEquationMgr = lambda: self.mgr

    def _get_attr_or_call(self, obj: Any, name: str) -> Any:
        value = getattr(obj, name, None)
        return value() if callable(value) else value


async def test_a_driven_dimension_is_refused_before_writing() -> None:
    dimension = _Dimension(LENGTH, 0.045)
    harness = _EquationHarness(dimension, ['"W" = 45', '"D1@Sketch1" = "W"'])

    result = await harness.set_dimension("d1@sketch1", 30)

    assert result.is_error
    error = result.error or ""
    assert '`"D1@Sketch1" = "W"`' in error
    assert "set_equations" in error
    assert ErrorClassifier().classify(error).error_type == "invalid_input"
    assert dimension.written == []
    assert harness.currentModel.rebuilds == 0


async def test_a_free_dimension_is_written() -> None:
    dimension = _Dimension(LENGTH, 0.045)
    harness = _EquationHarness(dimension, ['"W" = 45', '"D1@Sketch1" = "W"'])

    result = await harness.set_dimension("D2@Sketch1", 30)

    assert result.is_success, result.error
    assert dimension.written == [0.03]
