"""``export_image`` takes ``view_direction``, ``up``, ``up_axis`` and ``hide_components``."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP
from pydantic import ValidationError

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.adapters.solidworks import bitmap
from solidworks_mcp.tools.export import ExportImageInput, register_export_tools


async def _export_image(config: Any, adapter: Any = None) -> Any:
    if adapter is None:
        adapter = MockSolidWorksAdapter({})
        await adapter.connect()
        await adapter.create_assembly()
        await adapter.insert_component("C:/base.sldprt")
        await adapter.insert_component("C:/cover.sldprt")
    mcp = FastMCP("image-views")
    await register_export_tools(mcp, adapter, config)
    return next(t.fn for t in await mcp.list_tools() if t.name == "export_image")


async def test_a_cover_off_picture_of_a_z_up_part(
    mock_config: Any, tmp_path: Path
) -> None:
    export = await _export_image(mock_config)

    result = await export(
        {
            "file_path": str(tmp_path / "dipper.png"),
            "up_axis": "z",
            "hide_components": ["component-2"],
        }
    )

    assert result["status"] == "success", result
    assert result["data"]["view"] == "isometric"
    assert result["data"]["view_axes"]["up"][2] > 0.8
    assert result["data"]["hidden_components"][0]["name"] == "component-2"
    assert "could not be shown again" not in result.get("warning", "")


async def test_a_view_from_a_direction(mock_config: Any, tmp_path: Path) -> None:
    export = await _export_image(mock_config)

    result = await export(
        {"file_path": str(tmp_path / "p.png"), "view_direction": [0, -1, 0]}
    )

    assert result["data"]["view"] == "custom"
    assert result["data"]["view_axes"]["toward_viewer"] == [0.0, -1.0, 0.0]


async def test_components_left_hidden_are_a_warning(
    mock_config: Any, tmp_path: Path
) -> None:
    path = tmp_path / "p.png"
    bitmap.write_png(path, 2, 2, [b"\x80" * 6, b"\x80" * 6])
    adapter = MockSolidWorksAdapter({})
    adapter.export_image = AsyncMock(  # type: ignore[method-assign]
        return_value=AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "file_path": str(path),
                "hidden_components": [
                    {"name": "cover-1", "before": True, "restored": False},
                    {"name": "base-1", "before": True, "restored": True},
                ],
                "section": {"plane": "Front Plane", "removed": False},
            },
        )
    )
    export = await _export_image(mock_config, adapter)

    result = await export({"file_path": str(path), "hide_components": ["cover-1"]})

    assert "Front Plane could not be taken off" in result["warning"]
    assert "cover-1 could not be shown again" in result["warning"]
    assert "base-1" not in result["warning"]


@pytest.mark.parametrize(
    ("fields", "words"),
    [
        ({"view_direction": [0, 0, 1], "view_orientation": "top"}, "not both"),
        ({"view_direction": [0, 0, 1], "orientation": "top"}, "not both"),
        ({"up": [0, 0, 1]}, "up goes with view_direction"),
        ({"view_direction": [0, 0, 0]}, "zero vector"),
        ({"view_direction": [0, 0, 1], "up": [0, 0, -1]}, "up lies along"),
        ({"view_orientation": "current", "up_axis": "z"}, "'current' isn't one"),
        ({"hide_components": [" "]}, "at least one component"),
    ],
)
def test_views_that_cant_be_set_are_refused(fields: dict[str, Any], words: str) -> None:
    with pytest.raises(ValidationError, match=words):
        ExportImageInput(file_path="C:/p.png", **fields)


def test_names_to_hide_are_trimmed() -> None:
    model = ExportImageInput(
        file_path="C:/p.png", hide_components=[" cover-1", "cover-1 ", ""]
    )

    assert model.hide_components == ["cover-1"]
