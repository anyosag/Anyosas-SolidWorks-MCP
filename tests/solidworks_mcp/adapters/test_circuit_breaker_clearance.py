"""The circuit breaker forwards ``measure_clearance``."""

from __future__ import annotations

from unittest.mock import AsyncMock

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.circuit_breaker import CircuitBreakerAdapter
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


async def test_the_breaker_passes_both_components() -> None:
    inner = MockSolidWorksAdapter({})
    ok = AdapterResult(status=AdapterResultStatus.SUCCESS, data={})
    inner.measure_clearance = AsyncMock(return_value=ok)  # type: ignore[method-assign]

    result = await CircuitBreakerAdapter(adapter=inner).measure_clearance(
        "tongue-1", "slot-1"
    )
    coarse = await CircuitBreakerAdapter(adapter=inner).measure_clearance(
        "tongue-1", "slot-1", True, 3.0
    )

    assert result is ok and coarse is ok
    assert inner.measure_clearance.await_args_list[0].args == (
        "tongue-1",
        "slot-1",
        False,
        None,
    )
    assert inner.measure_clearance.await_args_list[1].args == (
        "tongue-1",
        "slot-1",
        True,
        3.0,
    )
