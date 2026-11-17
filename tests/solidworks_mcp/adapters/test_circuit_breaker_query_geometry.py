"""The circuit breaker forwards ``query_geometry`` whole."""

from __future__ import annotations

from unittest.mock import AsyncMock

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.circuit_breaker import CircuitBreakerAdapter
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


async def test_the_breaker_passes_every_argument() -> None:
    inner = MockSolidWorksAdapter({})
    ok = AdapterResult(status=AdapterResultStatus.SUCCESS, data={})
    inner.query_geometry = AsyncMock(return_value=ok)  # type: ignore[method-assign]

    result = await CircuitBreakerAdapter(adapter=inner).query_geometry(
        [1, 2, 3], 4.0, "faces", 3, False
    )

    assert result is ok
    inner.query_geometry.assert_awaited_once_with([1, 2, 3], 4.0, "faces", 3, False)
