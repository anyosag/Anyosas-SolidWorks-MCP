"""``set_component_visibility`` on the PyWin32 adapter's selection mixin."""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import component_visibility as cv
from solidworks_mcp.adapters.solidworks.selection import SolidWorksSelectionMixin

from .test_component_visibility import _Assembly, _Component


class _Harness(SolidWorksSelectionMixin):
    def __init__(self, model: Any) -> None:
        self.currentModel = model
        self.swApp = None
        self.operations: list[str] = []

    def _handle_com_operation(self, name: str, operation: Any) -> Any:
        self.operations.append(name)
        try:
            return AdapterResult(status=AdapterResultStatus.SUCCESS, data=operation())
        except Exception as exc:  # noqa: BLE001 - mirror the real adapter
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=f"Error in {name}: {exc}"
            )

    def _attempt(self, operation: Any, default: Any = None) -> Any:
        try:
            return operation()
        except Exception:  # noqa: BLE001
            return default

    def _get_attr_or_call(self, obj: Any, name: str) -> Any:
        value = getattr(obj, name)
        return value() if callable(value) else value


class _AssemblyDoc(_Assembly):
    def GetType(self) -> int:  # noqa: N802 - COM name
        return 2


@pytest.fixture(autouse=True)
def _plain_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cv, "_as_com", lambda _adapter, obj, _iface: obj)


def _harness() -> _Harness:
    return _Harness(_AssemblyDoc([_Component("base-1"), _Component("cover-1")]))


async def test_a_component_is_hidden_and_reported() -> None:
    harness = _harness()

    result = await harness.set_component_visibility(["cover-1", "cover-1"], False)

    assert result.is_success, result.error
    assert result.data == {
        "visible": False,
        "components": [
            {"name": "cover-1", "before": True, "after": False, "method": "Visible"}
        ],
    }
    assert harness.operations == ["set_component_visibility"]
    assert harness.currentModel.components[0].Visible == cv.VISIBLE


async def test_a_missing_component_changes_nothing() -> None:
    harness = _harness()

    result = await harness.set_component_visibility(["cover-1", "lid-1"], False)

    assert result.error.startswith("Component(s) not found: lid-1")
    assert harness.currentModel.components[1].Visible == cv.VISIBLE


@pytest.mark.parametrize(
    ("names", "model", "words"),
    [
        ([], _AssemblyDoc([]), "Give the components"),
        (["cover-1"], None, "No active model"),
        (["cover-1"], _Assembly([]), "needs an assembly"),
    ],
    ids=["no names", "no model", "a part"],
)
async def test_refusals(names: list[str], model: Any, words: str) -> None:
    result = await _Harness(model).set_component_visibility(names, False)

    assert words in (result.error or "")
