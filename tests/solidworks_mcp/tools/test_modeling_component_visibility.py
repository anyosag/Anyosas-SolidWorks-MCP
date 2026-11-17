"""The ``set_component_visibility`` tool: a cover hidden instead of moved aside."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP
from pydantic import ValidationError

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.modeling import (
    SetComponentVisibilityInput,
    register_modeling_tools,
)


@pytest.fixture
async def tools(mock_config: Any) -> dict[str, Any]:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_assembly()
    await adapter.insert_component("C:/base.sldprt")
    await adapter.insert_component("C:/cover.sldprt")
    mcp = FastMCP("component-visibility")
    await register_modeling_tools(mcp, adapter, mock_config)
    return {tool.name: tool.fn for tool in await mcp.list_tools()}


async def test_a_cover_is_hidden_then_shown(tools: dict[str, Any]) -> None:
    hidden = await tools["set_component_visibility"](
        {"components": ["component-2"], "visible": False}
    )
    shown = await tools["set_component_visibility"](
        {"components": ["component-2", "component-1"], "visible": True}
    )

    assert hidden["status"] == "success", hidden
    assert hidden["message"] == "Hid component-2"
    assert hidden["data"]["components"][0]["before"] is True
    assert shown["message"] == "Showed component-2; component-1 already shown"


async def test_nothing_to_change(tools: dict[str, Any]) -> None:
    reply = await tools["set_component_visibility"](
        {"components": ["component-1"], "visible": True}
    )

    assert reply["message"] == "Nothing changed; component-1 already shown"


async def test_a_missing_component_is_an_error(tools: dict[str, Any]) -> None:
    reply = await tools["set_component_visibility"](
        {"components": ["lid-1"], "visible": False}
    )

    assert reply["status"] == "error"
    assert "lid-1" in reply["message"]


def test_names_are_trimmed_and_needed() -> None:
    model = SetComponentVisibilityInput(
        components=[" cover-1 ", "cover-1", ""], visible=False
    )
    assert model.components == ["cover-1"]
    with pytest.raises(ValidationError, match="at least one component"):
        SetComponentVisibilityInput(components=["  "], visible=False)
