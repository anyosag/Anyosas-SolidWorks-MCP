"""The connection pool forwards ``query_geometry`` whole."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.connection_pool import ConnectionPoolAdapter


async def test_the_pool_passes_every_argument() -> None:
    ok = AdapterResult(status=AdapterResultStatus.SUCCESS, data={})
    stub = SimpleNamespace(query_geometry=AsyncMock(return_value=ok))
    pool = ConnectionPoolAdapter(adapter_factory=lambda: stub, pool_size=1)

    async def passthrough(_name: str, operation: object) -> object:
        return await operation(stub)  # type: ignore[operator]

    pool._execute_with_pool = AsyncMock(side_effect=passthrough)  # type: ignore[method-assign]

    assert (await pool.query_geometry([1, 2, 3], 4.0, "edges", 5, True)) is ok
    stub.query_geometry.assert_awaited_once_with([1, 2, 3], 4.0, "edges", 5, True)
    assert pool._execute_with_pool.await_args.args[0] == "query_geometry"
