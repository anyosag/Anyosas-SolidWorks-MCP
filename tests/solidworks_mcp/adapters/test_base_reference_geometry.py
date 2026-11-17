"""The base adapter's defaults for sketch frames, plane axes, and axes and planes anywhere."""

from __future__ import annotations

import pytest

from solidworks_mcp.adapters.base import SolidWorksAdapter
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("get_open_sketch_axes", ()),
        ("plane_axes", ("Plane1",)),
        ("create_axis_through", ([0.0, 0.0, 0.0], [0.0, 1.0, 0.0])),
        ("create_plane_through", ([20.0, 0.0, 10.0], [0.5, 0.0, 0.866])),
    ],
)
async def test_an_adapter_without_them_says_so(method: str, args: tuple) -> None:
    result = await getattr(SolidWorksAdapter, method)(MockSolidWorksAdapter({}), *args)

    assert result.error == f"{method} is not implemented by this adapter"
