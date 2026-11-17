"""The ``add_sketch_text`` tool: its input, its reply and its warnings."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.tools.sketching import (
    AddSketchTextInput,
    register_sketching_tools,
    text_warnings,
)


async def _tool(mcp_server, adapter) -> Any:
    await register_sketching_tools(mcp_server, adapter, {})
    return {t.name: t.fn for t in await mcp_server.list_tools()}["add_sketch_text"]


async def test_the_text_is_written_and_read_back(mcp_server, mock_adapter) -> None:
    await mock_adapter.create_part()
    await mock_adapter.create_sketch("Top")
    tool = await _tool(mcp_server, mock_adapter)

    reply = await tool({"text": "MAX", "x": 5, "y": 4, "height": 3, "font": "Arial"})

    assert reply["status"] == "success", reply
    assert reply["message"] == "Added text 'MAX' at (5, 4) mm"
    assert reply["text"]["id"].startswith("Text_")
    assert reply["text"]["height_mm"] == 3
    # Only the experimental level's sentence: nothing about the text.
    assert reply["warning"].startswith("Experimental:")
    assert "font" not in reply["warning"]


async def test_without_an_open_sketch_it_says_so(mcp_server, mock_adapter) -> None:
    await mock_adapter.create_part()
    tool = await _tool(mcp_server, mock_adapter)

    reply = await tool({"text": "MAX"})

    assert reply["status"] == "error"
    assert reply["error_type"] == "no_active_sketch"


async def test_what_did_not_take_is_a_warning(mcp_server, mock_adapter) -> None:
    mock_adapter.add_sketch_text = AsyncMock(
        return_value=AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "id": "Text_3",
                "height_mm": 5.0,
                "font": "Century Gothic",
                "format_applied": False,
                "shares_sketch_with": ["Rectangle_1"],
            },
        )
    )
    tool = await _tool(mcp_server, mock_adapter)

    reply = await tool({"text": "V2", "height": 4, "font": "Arial", "angle": 90})

    mock_adapter.add_sketch_text.assert_awaited_once_with(
        "V2", 0.0, 0.0, 4.0, "Arial", 90.0, False, False
    )
    warning = reply["warning"]
    assert "also holds Rectangle_1" in warning
    assert "did not take the font settings" in warning
    assert "5 mm high, not the 4 mm asked" in warning
    assert "font 'Century Gothic', not 'Arial'" in warning


def test_text_as_asked_has_no_warning() -> None:
    asked = AddSketchTextInput(text="MIN", height=4.0, font="arial")

    assert text_warnings(asked, {"height_mm": 4.0, "font": "Arial"}) == []
    assert text_warnings(AddSketchTextInput(text="MIN"), {}) == []


@pytest.mark.parametrize(
    "fields",
    [{"text": "  "}, {"text": "A", "height": 0}, {"text": "A", "angle": 400}],
)
def test_input_that_cannot_be_written_is_refused(fields: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        AddSketchTextInput(**fields)
