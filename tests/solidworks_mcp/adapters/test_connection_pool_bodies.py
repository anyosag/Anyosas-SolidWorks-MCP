"""The connection pool forwards every multibody method with all its arguments."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.connection_pool import ConnectionPoolAdapter

_CALLS: list[tuple[str, tuple[Any, ...]]] = [
    ("list_bodies", (True,)),
    ("insert_part", ("C:/h.sldprt", "Default", [0, 1, 0], 90.0, "y", [1, 2, 3], True)),
    ("combine_bodies", ("subtract", "A", ["B", "C"])),
    ("move_copy_body", (["A"], [1, 0, 0], 45.0, "x", [0, 0, 1], True, 3)),
    ("delete_keep_body", (["A"], True)),
    ("split_body", (None, "Sketch2", "A")),
    ("body_clearance", ("A", "B")),
    ("make_mating_negative", ("Lid", ["L1", "L2"], 0.15, 16, "z", "indent")),
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
