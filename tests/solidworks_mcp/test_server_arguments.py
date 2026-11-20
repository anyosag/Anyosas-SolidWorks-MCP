"""The server puts right the common shape mistakes of a tool call.

From the sessions the speed report read (section 13): calls made without the
tool's schema in the prompt sent ``open_model`` a flat ``file_path``,
``list_features`` only a ``document``, ``exit_sketch`` an ``input_data`` of
``"{}"``, ``get_sketch`` a ``sketch_name`` and ``describe_tool_inputs``
``tool_names``. Each failed validation and cost a turn.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp.exceptions import ValidationError

from solidworks_mcp.server import SolidWorksMCPServer


@pytest.fixture
async def server(mock_config: Any) -> SolidWorksMCPServer:
    built = SolidWorksMCPServer(mock_config)
    await built.setup()
    return built


async def _call(server: SolidWorksMCPServer, name: str, arguments: Any) -> Any:
    result = await server.mcp.call_tool(name, arguments)
    return result.structured_content


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("open_model", {"file_path": "C:/work/bracket.sldprt"}),
        ("open_model", {"input_data": '{"file_path": "C:/work/bracket.sldprt"}'}),
        ("create_part", {}),
        ("create_assembly", {"input_data": {}}),
        ("describe_tool_inputs", {"tool_names": ["save_as"]}),
        ("describe_tool_inputs", {"input_data": {"tool_names": ["save_as"]}}),
    ],
)
async def test_the_shapes_agents_send_now_work(
    server: SolidWorksMCPServer, name: str, arguments: dict[str, Any]
) -> None:
    reply = await _call(server, name, arguments)

    assert reply["status"] == "success", reply


async def test_a_document_alone_and_an_empty_text_input_run_the_tool(
    server: SolidWorksMCPServer,
) -> None:
    await _call(server, "create_part", {"name": "bracket"})

    listed = await _call(server, "list_features", {"document": "bracket"})
    closed = await _call(server, "exit_sketch", {"input_data": "{}"})
    sketch = await _call(server, "get_sketch", {"input_data": {"sketch_name": "S9"}})

    assert listed["status"] == "success", listed
    # The tool ran: what it says is the model's, not a validation error.
    assert closed["message"].startswith("Failed to exit sketch")
    assert "S9" in sketch["message"]


async def test_a_call_that_cannot_be_read_still_fails_clearly(
    server: SolidWorksMCPServer,
) -> None:
    with pytest.raises(ValidationError, match="file_pth"):
        await server.mcp.call_tool("open_model", {"file_pth": "C:/work/a.sldprt"})
    with pytest.raises(ValidationError, match="input_data"):
        await server.mcp.call_tool("exit_sketch", {"input_data": {"x": 1}})


async def test_an_unknown_tool_is_left_to_the_server(
    server: SolidWorksMCPServer,
) -> None:
    from fastmcp.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        await server.mcp.call_tool("no_such_tool", {"x": 1})
