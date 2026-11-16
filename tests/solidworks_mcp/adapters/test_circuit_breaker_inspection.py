"""The circuit breaker forwards the read-only inspection methods."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.circuit_breaker import CircuitBreakerAdapter
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("open_model_read_only", ("C:/a.sldprt",)),
        ("get_feature_definition", ("Boss-Extrude1",)),
        ("get_sketch", ("Sketch1",)),
    ],
)
async def test_the_breaker_passes_the_arguments(method: str, args: tuple) -> None:
    inner = MockSolidWorksAdapter({})
    ok = AdapterResult(status=AdapterResultStatus.SUCCESS, data={})
    setattr(inner, method, AsyncMock(return_value=ok))

    assert (await getattr(CircuitBreakerAdapter(adapter=inner), method)(*args)) is ok
    getattr(inner, method).assert_awaited_once_with(*args)
