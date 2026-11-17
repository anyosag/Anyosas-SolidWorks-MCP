"""The circuit breaker forwards ``add_sketch_text`` and ``pattern_linear``."""

from __future__ import annotations

from unittest.mock import AsyncMock

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.circuit_breaker import CircuitBreakerAdapter
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter

_OK = AdapterResult(status=AdapterResultStatus.SUCCESS, data={})


async def test_the_breaker_passes_every_text_argument() -> None:
    inner = MockSolidWorksAdapter({})
    inner.add_sketch_text = AsyncMock(return_value=_OK)  # type: ignore[method-assign]

    result = await CircuitBreakerAdapter(adapter=inner).add_sketch_text(
        "MAX", 1.0, 2.0, 4.0, "Arial", 90.0, True, False
    )

    assert result is _OK
    inner.add_sketch_text.assert_awaited_once_with(
        "MAX", 1.0, 2.0, 4.0, "Arial", 90.0, True, False
    )


async def test_the_breaker_passes_every_pattern_argument() -> None:
    inner = MockSolidWorksAdapter({})
    inner.pattern_linear = AsyncMock(return_value=_OK)  # type: ignore[method-assign]
    args = (["Cut-Extrude1"], "x", 8, 12.0, "-z", 2, 20.0, True, False, False)

    result = await CircuitBreakerAdapter(adapter=inner).pattern_linear(*args)

    assert result is _OK
    inner.pattern_linear.assert_awaited_once_with(*args)
