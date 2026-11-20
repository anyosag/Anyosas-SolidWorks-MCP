"""The circuit breaker forwards the many-entities sketch call."""

from __future__ import annotations

from unittest.mock import AsyncMock

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.circuit_breaker import CircuitBreakerAdapter
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


async def test_the_breaker_passes_every_argument_through() -> None:
    inner = MockSolidWorksAdapter({})
    ok = AdapterResult(status=AdapterResultStatus.SUCCESS, data={"count": 1})
    inner.add_sketch_entities = AsyncMock(return_value=ok)  # type: ignore[method-assign]
    breaker = CircuitBreakerAdapter(adapter=inner)
    items = [{"tool": "add_line", "x1": 0, "y1": 0, "x2": 1, "y2": 0}]

    assert (await breaker.add_sketch_entities(items, rollback_on_error=False)) is ok
    assert (await breaker.add_sketch_entities(items)) is ok

    assert inner.add_sketch_entities.await_args_list[0].args == (items, False)
    assert inner.add_sketch_entities.await_args_list[1].args == (items, True)
