"""The connection pool forwards ``set_component_visibility``."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.connection_pool import ConnectionPoolAdapter


async def test_the_pool_passes_the_arguments_through() -> None:
    ok = AdapterResult(status=AdapterResultStatus.SUCCESS, data={})
    stub = SimpleNamespace(set_component_visibility=AsyncMock(return_value=ok))
    pool = ConnectionPoolAdapter(adapter_factory=lambda: stub, pool_size=1)

    async def passthrough(_name: str, operation: object) -> object:
        return await operation(stub)  # type: ignore[operator]

    pool._execute_with_pool = AsyncMock(side_effect=passthrough)  # type: ignore[method-assign]

    assert (await pool.set_component_visibility(["cover-1"], False)) is ok
    stub.set_component_visibility.assert_awaited_once_with(["cover-1"], False)
