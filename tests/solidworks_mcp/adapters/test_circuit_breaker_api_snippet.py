"""The circuit breaker forwards ``run_api_snippet``; a failed snippet isn't a fault."""

from __future__ import annotations

from unittest.mock import AsyncMock

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.circuit_breaker import CircuitBreakerAdapter
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


async def test_the_breaker_passes_every_argument() -> None:
    inner = MockSolidWorksAdapter({})
    ok = AdapterResult(status=AdapterResultStatus.SUCCESS, data={})
    inner.run_api_snippet = AsyncMock(return_value=ok)  # type: ignore[method-assign]

    breaker = CircuitBreakerAdapter(adapter=inner)

    assert (await breaker.run_api_snippet("return 1;", 12.0, True)) is ok
    inner.run_api_snippet.assert_awaited_once_with("return 1;", 12.0, True)


async def test_snippets_that_fail_never_open_the_breaker() -> None:
    inner = MockSolidWorksAdapter({})
    failed = AdapterResult(status=AdapterResultStatus.ERROR, error="did not compile")
    inner.run_api_snippet = AsyncMock(return_value=failed)  # type: ignore[method-assign]
    breaker = CircuitBreakerAdapter(adapter=inner, failure_threshold=2)

    for _ in range(4):
        result = await breaker.run_api_snippet("bad")

    assert result is failed
    assert inner.run_api_snippet.await_count == 4
