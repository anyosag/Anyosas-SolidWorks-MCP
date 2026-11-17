"""The circuit breaker and the pool forward the Save Bodies and document methods."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.circuit_breaker import CircuitBreakerAdapter

CALLS = [
    ("save_body_as_part", ("Comb", "C:/c.sldprt", "C:/a.SLDASM")),
    ("get_save_bodies", ("Save Bodies1",)),
    ("edit_save_bodies", ("Save Bodies1", {"consume_body": True})),
    ("resplit_bodies", ("Save Bodies1",)),
    ("list_loaded_documents", ()),
    ("close_leftover_documents", ()),
    ("list_assembly_parts", ()),
]


def _inner() -> AsyncMock:
    inner = AsyncMock()
    for name, _args in CALLS:
        setattr(
            inner,
            name,
            AsyncMock(
                return_value=AdapterResult(
                    status=AdapterResultStatus.SUCCESS, data={"called": name}
                )
            ),
        )
    return inner


@pytest.mark.parametrize(("method", "args"), CALLS)
async def test_the_breaker_forwards(method: str, args: tuple) -> None:
    inner = _inner()

    result = await getattr(CircuitBreakerAdapter(adapter=inner), method)(*args)

    assert result.data == {"called": method}
    getattr(inner, method).assert_awaited_once_with(*args)
