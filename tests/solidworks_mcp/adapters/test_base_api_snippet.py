"""The base adapter's default for ``run_api_snippet``."""

from __future__ import annotations

from solidworks_mcp.adapters.base import SolidWorksAdapter
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


async def test_an_adapter_without_it_says_so() -> None:
    result = await SolidWorksAdapter.run_api_snippet(
        MockSolidWorksAdapter({}), "return 1;"
    )

    assert result.error == "run_api_snippet is not implemented by this adapter"
