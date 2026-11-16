"""The base adapter's defaults for the multibody methods."""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.base import SolidWorksAdapter
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter

_CALLS: list[tuple[str, tuple[Any, ...]]] = [
    ("list_bodies", ()),
    ("insert_part", ("C:/h.sldprt",)),
    ("combine_bodies", ("add", "A", ["B"])),
    ("move_copy_body", (["A"], [1, 0, 0])),
    ("delete_keep_body", (["A"],)),
    ("split_body", ("Top",)),
    ("body_clearance", ("A", "B")),
    ("make_mating_negative", ("Lid", ["L1"], 0.15)),
]


@pytest.mark.parametrize(("name", "args"), _CALLS)
async def test_an_adapter_without_it_says_so(name: str, args: tuple[Any, ...]) -> None:
    result = await getattr(SolidWorksAdapter, name)(MockSolidWorksAdapter({}), *args)

    assert result.error == f"{name} is not implemented by this adapter"
