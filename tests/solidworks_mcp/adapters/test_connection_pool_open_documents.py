"""The connection pool passes the document tools' extra reads on, only when asked."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, call

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.connection_pool import ConnectionPoolAdapter


@pytest.fixture
def pooled() -> tuple[ConnectionPoolAdapter, AsyncMock]:
    ok: AdapterResult[Any] = AdapterResult(status=AdapterResultStatus.SUCCESS)
    inner = AsyncMock()
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
        setattr(inner, name, AsyncMock(return_value=ok))
    pool = ConnectionPoolAdapter(adapter_factory=lambda: inner, pool_size=1)

    async def passthrough(_name: str, operation: Any) -> Any:
        return await operation(inner)

    pool._execute_with_pool = AsyncMock(side_effect=passthrough)  # type: ignore[method-assign]
    return pool, inner


async def test_the_flags_reach_the_adapter_when_set(
    pooled: tuple[ConnectionPoolAdapter, AsyncMock],
) -> None:
    pool, inner = pooled

    await pool.open_model("C:/a.SLDPRT", list_documents=True, summarize=True)
    await pool.open_model_read_only("C:/a.SLDPRT", list_documents=True)
    await pool.close_model(False, True, list_documents=True)
    await pool.save_file("C:/b.SLDPRT", True, list_documents=True)
    await pool.create_part(None, None, "C:/t.prtdot", list_documents=True)
    await pool.create_assembly(None, None, list_documents=True)
    await pool.activate_document("Part1", list_documents=True)
    await pool.save_body_as_part(
        "Comb", "C:/c.sldprt", "C:/a.sldasm", list_documents=True
    )

    assert inner.open_model.await_args == call(
        "C:/a.SLDPRT", list_documents=True, summarize=True
    )
    assert inner.open_model_read_only.await_args == call(
        "C:/a.SLDPRT", list_documents=True
    )
    assert inner.close_model.await_args == call(False, True, list_documents=True)
    assert inner.save_file.await_args == call("C:/b.SLDPRT", True, list_documents=True)
    assert inner.create_part.await_args == call(
        None, None, "C:/t.prtdot", list_documents=True
    )
    assert inner.create_assembly.await_args == call(None, None, list_documents=True)
    assert inner.activate_document.await_args == call("Part1", list_documents=True)
    assert inner.save_body_as_part.await_args == call(
        "Comb", "C:/c.sldprt", "C:/a.sldasm", list_documents=True
    )


async def test_the_calls_are_unchanged_without_them(
    pooled: tuple[ConnectionPoolAdapter, AsyncMock],
) -> None:
    pool, inner = pooled

    await pool.open_model("C:/a.SLDPRT")
    await pool.open_model_read_only("C:/a.SLDPRT")
    await pool.close_model()
    await pool.save_file()
    await pool.activate_document("Part1")
    await pool.save_body_as_part("Comb", "C:/c.sldprt")

    assert inner.open_model.await_args == call("C:/a.SLDPRT")
    assert inner.open_model_read_only.await_args == call("C:/a.SLDPRT")
    assert inner.close_model.await_args == call(False, False)
    assert inner.save_file.await_args == call(None, False)
    assert inner.activate_document.await_args == call("Part1")
    assert inner.save_body_as_part.await_args == call("Comb", "C:/c.sldprt", None)
