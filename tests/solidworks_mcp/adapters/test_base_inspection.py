"""The base adapter's defaults for the read-only inspection methods."""

from __future__ import annotations

import pytest

from solidworks_mcp.adapters.base import SolidWorksAdapter
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("open_model_read_only", ("C:/a.sldprt",)),
        ("get_feature_definition", ("Boss-Extrude1",)),
        ("get_sketch", ("Sketch1",)),
    ],
)
async def test_an_adapter_without_them_says_so(method: str, args: tuple) -> None:
    result = await getattr(SolidWorksAdapter, method)(MockSolidWorksAdapter({}), *args)

    assert result.error == f"{method} is not implemented by this adapter"
