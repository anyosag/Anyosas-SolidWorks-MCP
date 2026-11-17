"""The circuit breaker forwards ``set_component_visibility``."""

from __future__ import annotations

from unittest.mock import AsyncMock

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.circuit_breaker import CircuitBreakerAdapter
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


async def test_the_breaker_passes_the_arguments_through() -> None:
    inner = MockSolidWorksAdapter({})
    ok = AdapterResult(status=AdapterResultStatus.SUCCESS, data={})
    inner.set_component_visibility = AsyncMock(return_value=ok)  # type: ignore[method-assign]
    breaker = CircuitBreakerAdapter(adapter=inner)

    assert (await breaker.set_component_visibility(["cover-1"], True)) is ok

    inner.set_component_visibility.assert_awaited_once_with(["cover-1"], True)
