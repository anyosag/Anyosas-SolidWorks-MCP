"""The connection pool forwards the turn, plane-volume and density methods whole."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.connection_pool import ConnectionPoolAdapter

_CALLS: list[tuple[str, tuple[Any, ...]]] = [
    (
        "rotation_clearance",
        ("lid-1", "base-1", [0, 0, 1], [0, 5, 0], [0, 170], 2.0, 0.05),
    ),
    ("volume_by_plane", (None, [0, 4, 0], [0, 1, 0], ["Cup"], True, 1.15)),
    ("set_density", (1.15, True)),
    (
        "set_component_transform",
        ("lid-1", None, None, True, [0, 0, 1], [0, 5, 0], 30.0),
    ),
]


@pytest.mark.parametrize(("name", "args"), _CALLS)
async def test_the_pool_passes_every_argument(name: str, args: tuple[Any, ...]) -> None:
    ok = AdapterResult(status=AdapterResultStatus.SUCCESS, data={})
    stub = SimpleNamespace(**{name: AsyncMock(return_value=ok)})
    pool = ConnectionPoolAdapter(adapter_factory=lambda: stub, pool_size=1)

    async def passthrough(_name: str, operation: Any) -> Any:
        return await operation(stub)

    pool._execute_with_pool = AsyncMock(side_effect=passthrough)  # type: ignore[method-assign]

    assert (await getattr(pool, name)(*args)) is ok
    getattr(stub, name).assert_awaited_once_with(*args)
    assert pool._execute_with_pool.await_args.args[0] == name
