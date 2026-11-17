"""The base adapter's defaults for the turn, plane-volume and density methods."""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.base import SolidWorksAdapter
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter

_CALLS: list[tuple[str, tuple[Any, ...]]] = [
    ("rotation_clearance", ("Lid", "Base", [0.0, 0.0, 1.0])),
    ("volume_by_plane", ("Top Plane",)),
    ("set_density", (1.15,)),
]


@pytest.mark.parametrize(("name", "args"), _CALLS)
async def test_an_adapter_without_it_says_so(name: str, args: tuple[Any, ...]) -> None:
    result = await getattr(SolidWorksAdapter, name)(MockSolidWorksAdapter({}), *args)

    assert result.error == f"{name} is not implemented by this adapter"
