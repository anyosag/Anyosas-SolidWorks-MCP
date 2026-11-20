"""The circuit breaker passes the document tools' extra reads on, only when asked.

Passing a flag left False would break an adapter that doesn't take it, so
the breaker calls the adapter as before unless the flag is set.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, call

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.circuit_breaker import CircuitBreakerAdapter


def _ok() -> AdapterResult[Any]:
    return AdapterResult(status=AdapterResultStatus.SUCCESS)


@pytest.fixture
def inner() -> AsyncMock:
    adapter = AsyncMock()
    for name in (
        "open_model",
        "open_model_read_only",
        "close_model",
        "save_file",
        "create_part",
        "create_assembly",
        "activate_document",
        "save_body_as_part",
    ):
        setattr(adapter, name, AsyncMock(return_value=_ok()))
    return adapter


async def test_the_flags_reach_the_adapter_when_set(inner: AsyncMock) -> None:
    breaker = CircuitBreakerAdapter(adapter=inner)

    await breaker.open_model("C:/a.SLDPRT", list_documents=True, summarize=True)
    await breaker.open_model_read_only("C:/a.SLDPRT", summarize=True)
    await breaker.close_model(True, False, list_documents=True)
    await breaker.save_file("C:/b.SLDPRT", False, list_documents=True)
    await breaker.create_part("p", "mm", None, list_documents=True)
    await breaker.create_assembly("a", None, list_documents=True)
    await breaker.activate_document("Part1", list_documents=True)
    await breaker.save_body_as_part("Comb", "C:/c.sldprt", None, list_documents=True)

    assert inner.open_model.await_args == call(
        "C:/a.SLDPRT", list_documents=True, summarize=True
    )
    assert inner.open_model_read_only.await_args == call("C:/a.SLDPRT", summarize=True)
    assert inner.close_model.await_args == call(True, False, list_documents=True)
    assert inner.save_file.await_args == call("C:/b.SLDPRT", False, list_documents=True)
    assert inner.create_part.await_args == call("p", "mm", None, list_documents=True)
    assert inner.create_assembly.await_args == call("a", None, list_documents=True)
    assert inner.activate_document.await_args == call("Part1", list_documents=True)
    assert inner.save_body_as_part.await_args == call(
        "Comb", "C:/c.sldprt", None, list_documents=True
    )


async def test_the_calls_are_unchanged_without_them(inner: AsyncMock) -> None:
    breaker = CircuitBreakerAdapter(adapter=inner)

    await breaker.open_model("C:/a.SLDPRT")
    await breaker.open_model_read_only("C:/a.SLDPRT")
    await breaker.close_model()
    await breaker.save_file("C:/b.SLDPRT")
    await breaker.activate_document("Part1")
    await breaker.save_body_as_part("Comb", "C:/c.sldprt")

    assert inner.open_model.await_args == call("C:/a.SLDPRT")
    assert inner.open_model_read_only.await_args == call("C:/a.SLDPRT")
    assert inner.close_model.await_args == call(False, False)
    assert inner.save_file.await_args == call("C:/b.SLDPRT", False)
    assert inner.activate_document.await_args == call("Part1")
    assert inner.save_body_as_part.await_args == call("Comb", "C:/c.sldprt", None)
