"""The base adapter's default for ``query_geometry``."""

from __future__ import annotations

from solidworks_mcp.adapters.base import SolidWorksAdapter
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


async def test_an_adapter_without_it_says_so() -> None:
    result = await SolidWorksAdapter.query_geometry(
        MockSolidWorksAdapter({}), [0.0, 0.0, 0.0]
    )

    assert result.error == "query_geometry is not implemented by this adapter"
