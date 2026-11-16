"""The circuit breaker forwards every multibody method with all its arguments."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.circuit_breaker import CircuitBreakerAdapter
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter

_CALLS: list[tuple[str, tuple[Any, ...]]] = [
    ("list_bodies", (True,)),
    ("insert_part", ("C:/h.sldprt", "Default", [0, 1, 0], 90.0, "y", [1, 2, 3], True)),
    ("combine_bodies", ("subtract", "A", ["B", "C"])),
    ("move_copy_body", (["A"], [1, 0, 0], 45.0, "x", [0, 0, 1], True, 3)),
    ("delete_keep_body", (["A"], True)),
    ("split_body", (None, "Sketch2", "A")),
    ("body_clearance", ("A", "B")),
    ("make_mating_negative", ("Lid", ["L1", "L2"], 0.15, 16, "z", "indent")),
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
