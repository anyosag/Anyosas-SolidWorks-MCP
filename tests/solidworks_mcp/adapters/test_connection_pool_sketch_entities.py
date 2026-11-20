"""The connection pool forwards the many-entities sketch call."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.connection_pool import ConnectionPoolAdapter


async def test_the_pool_passes_every_argument_through() -> None:
    ok = AdapterResult(status=AdapterResultStatus.SUCCESS, data={"count": 1})
    stub = SimpleNamespace(add_sketch_entities=AsyncMock(return_value=ok))
    pool = ConnectionPoolAdapter(adapter_factory=lambda: stub, pool_size=1)
    names: list[str] = []

    async def passthrough(operation_name: str, operation: object) -> object:
        names.append(operation_name)
        return await operation(stub)  # type: ignore[operator]

    pool._execute_with_pool = AsyncMock(side_effect=passthrough)  # type: ignore[method-assign]
    items = [{"tool": "add_circle", "center_x": 0, "center_y": 0, "radius": 1}]

    assert (await pool.add_sketch_entities(items, rollback_on_error=False)) is ok

    assert names == ["add_sketch_entities"]
    stub.add_sketch_entities.assert_awaited_once_with(items, False)
