"""``set_component_transform`` with a turn about an axis through a pivot (``io``)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import io as _io_module

from .test_io import TestAssemblyAndDrawingSuccessPaths as _Paths


def _placing(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[Any]]:
    adapter, comp, _calls = _Paths._state_adapter(monkeypatch)
    comp.Transform2 = SimpleNamespace(
        ArrayData=(1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0, 0.1, 0.0, 0.0, 1.0, 0, 0, 0)
    )
    comp.GetMates = lambda: None
    seen: list[Any] = []

    def place(_adapter, _model, _assembly, component, at, rotation, fixed):
        seen.append((at, rotation, fixed))
        return {"placed": True, "moved": True, "fixed": False}

    monkeypatch.setattr(_io_module._placement, "place", place)
    return adapter, seen


@pytest.mark.asyncio
async def test_a_component_turned_about_the_z_axis(monkeypatch) -> None:
    adapter, seen = _placing(monkeypatch)

    result = await adapter.set_component_transform(
        "crank-1", None, None, None, [0.0, 0.0, 1.0], None, 90.0
    )

    assert result.is_success, result.error
    ((at, rotation, fixed),) = seen
    # Its origin, 100 mm out on X, swings to Y; its X axis points along Y.
    assert at == pytest.approx([0.0, 0.1, 0.0])
    assert rotation[0] == pytest.approx([0.0, 1.0, 0.0])
    assert fixed is None


@pytest.mark.asyncio
async def test_a_turn_about_a_pivot_after_a_new_origin(monkeypatch) -> None:
    adapter, seen = _placing(monkeypatch)

    await adapter.set_component_transform(
        "crank-1", [200.0, 0.0, 0.0], None, True, [0.0, 0.0, 1.0], [150.0, 0, 0], 180
    )

    ((at, _rotation, fixed),) = seen
    assert at == pytest.approx([0.1, 0.0, 0.0])
    assert fixed is True


@pytest.mark.asyncio
async def test_no_turn_keeps_the_rows_asked(monkeypatch) -> None:
    adapter, seen = _placing(monkeypatch)

    await adapter.set_component_transform("crank-1", None, None, None, [0, 0, 1], None)

    assert seen == [(None, None, None)]
