"""The base adapter's hole and thread defaults report a missing capability."""

from __future__ import annotations

import pytest

from solidworks_mcp.adapters.base import SolidWorksAdapter
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter

_CALLS = {
    "create_thread": ("M10x1.5", [5, 0, 40], 20.0),
    "create_hole_wizard_hole": ("clearance", "M3", [20, 10, 10]),
}


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "args"), _CALLS.items())
async def test_base_default_says_the_adapter_lacks_it(method, args) -> None:
    result = await getattr(SolidWorksAdapter, method)(MockSolidWorksAdapter({}), *args)

    assert not result.is_success
    assert result.error == f"{method} is not implemented by this adapter"
