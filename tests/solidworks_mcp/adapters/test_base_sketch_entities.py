"""The base adapter's default for drawing many sketch entities in one call."""

from __future__ import annotations

from solidworks_mcp.adapters.base import SolidWorksAdapter
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


async def test_an_adapter_without_it_says_so() -> None:
    """Called on the base class: an error naming the method, not a crash."""
    adapter = MockSolidWorksAdapter({})

    result = await SolidWorksAdapter.add_sketch_entities(adapter, [], False)

    assert result.is_error
    assert result.error == "add_sketch_entities is not implemented by this adapter"
