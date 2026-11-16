"""The connection pool forwards the read-only inspection methods."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.connection_pool import ConnectionPoolAdapter


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("open_model_read_only", ("C:/a.sldprt",)),
        ("get_feature_definition", ("Boss-Extrude1",)),
        ("get_sketch", ("Sketch1",)),
        ("list_references", ("C:/a.sldasm", True)),
        (
            "move_document",
            ("C:/a.sldprt", "C:/b.sldprt", ["C:/f.sldasm"], ["C:/"], False),
        ),
    ],
)
async def test_the_pool_passes_the_arguments(method: str, args: tuple) -> None:
    ok = AdapterResult(status=AdapterResultStatus.SUCCESS, data={})
    stub = SimpleNamespace(**{method: AsyncMock(return_value=ok)})
    pool = ConnectionPoolAdapter(adapter_factory=lambda: stub, pool_size=1)

    async def passthrough(_name: str, operation: object) -> object:
        return await operation(stub)  # type: ignore[operator]

    pool._execute_with_pool = AsyncMock(side_effect=passthrough)  # type: ignore[method-assign]

    assert (await getattr(pool, method)(*args)) is ok
    getattr(stub, method).assert_awaited_once_with(*args)
