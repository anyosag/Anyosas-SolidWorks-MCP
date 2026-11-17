"""The connection pool forwards the Save Bodies and document methods with their arguments."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.connection_pool import ConnectionPoolAdapter

_CALLS: list[tuple[str, tuple[Any, ...]]] = [
    ("save_body_as_part", ("Comb", "C:/c.sldprt", "C:/a.SLDASM")),
    ("get_save_bodies", ("Save Bodies1",)),
    ("edit_save_bodies", ("Save Bodies1", {"consume_body": True})),
    ("resplit_bodies", ("Save Bodies1",)),
    ("list_loaded_documents", ()),
    ("close_leftover_documents", ()),
    ("list_assembly_parts", ()),
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
