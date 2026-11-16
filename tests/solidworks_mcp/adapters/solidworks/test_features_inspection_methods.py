"""The adapter methods behind get_feature_definition and get_sketch."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import inspection
from solidworks_mcp.adapters.solidworks.features import SolidWorksFeaturesMixin


class _Harness(SolidWorksFeaturesMixin):
    def __init__(self, model: Any = None) -> None:
        self.currentModel = model
        self.operations: list[str] = []

    def _handle_com_operation(self, name: str, operation: Any, *args: Any) -> Any:
        self.operations.append(name)
        try:
            return AdapterResult(
                status=AdapterResultStatus.SUCCESS, data=operation(*args)
            )
        except Exception as exc:  # noqa: BLE001 - mirror the real adapter
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=f"Error in {name}: {exc}"
            )


@pytest.mark.parametrize("method", ["get_feature_definition", "get_sketch"])
async def test_no_model_is_said_plainly(method: str) -> None:
    result = await getattr(_Harness(), method)("Sketch1")

    assert result.error == "No active model"


async def test_each_reads_in_its_own_operation(monkeypatch) -> None:
    monkeypatch.setattr(
        inspection, "feature_definition", lambda _a, name: {"feature": name}
    )
    monkeypatch.setattr(inspection, "read_sketch", lambda _a, name: {"sketch": name})
    harness = _Harness(SimpleNamespace())

    definition = await harness.get_feature_definition("Cut1")
    sketch = await harness.get_sketch("Sketch1")

    assert definition.data == {"feature": "Cut1"}
    assert sketch.data == {"sketch": "Sketch1"}
    assert harness.operations == ["get_feature_definition", "get_sketch"]


async def test_errors_lose_the_operation_prefix(monkeypatch) -> None:
    def refuse(*_a: Any) -> Any:
        raise ValueError("'Fillet1' is a Fillet, not a sketch.")

    monkeypatch.setattr(inspection, "read_sketch", refuse)

    result = await _Harness(SimpleNamespace()).get_sketch("Fillet1")

    assert result.error == "'Fillet1' is a Fillet, not a sketch."


def test_they_live_on_the_features_mixin() -> None:
    pytest.importorskip("win32com", reason="pywin32 is Windows-only")
    from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter

    for name in ("get_feature_definition", "get_sketch"):
        owner = next(k.__name__ for k in PyWin32Adapter.__mro__ if name in vars(k))
        assert owner == "SolidWorksFeaturesMixin"
