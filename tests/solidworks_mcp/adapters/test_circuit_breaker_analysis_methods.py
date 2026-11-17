"""The circuit breaker forwards the turn, plane-volume and density methods whole."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.circuit_breaker import CircuitBreakerAdapter
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter

_CALLS: list[tuple[str, tuple[Any, ...]]] = [
    (
        "rotation_clearance",
        ("lid-1", "base-1", [0, 0, 1], [0, 5, 0], [0, 170], 2.0, 0.05),
    ),
    ("volume_by_plane", (None, [0, 4, 0], [0, 1, 0], ["Cup"], True, 1.15)),
    ("set_density", (1.15, True)),
    (
        "set_component_transform",
        ("lid-1", None, None, True, [0, 0, 1], [0, 5, 0], 30.0),
    ),
]


@pytest.mark.parametrize(("name", "args"), _CALLS)
async def test_the_breaker_passes_every_argument(
    name: str, args: tuple[Any, ...]
) -> None:
    inner = MockSolidWorksAdapter({})
    ok = AdapterResult(status=AdapterResultStatus.SUCCESS, data={})
    setattr(inner, name, AsyncMock(return_value=ok))

    result = await getattr(CircuitBreakerAdapter(adapter=inner), name)(*args)

    assert result is ok
    getattr(inner, name).assert_awaited_once_with(*args)
