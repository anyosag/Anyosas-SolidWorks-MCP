"""``execute_workflow`` sends back the last picture its steps took.

99 of 142 image exports in the 2026-11-20 sample were followed by a ``Read``
of the file, 78 in a turn of their own. Over MCP, a workflow now sends back
the picture of its last ``export_image`` step, and of any step that sets
``inline: true``; the others stay in their files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.tools import ToolResult

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools import export
from solidworks_mcp.tools.automation import register_automation_tools
from solidworks_mcp.tools.export import register_export_tools
from solidworks_mcp.tools.sketching import register_sketching_tools


@pytest.fixture
def no_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mock's 0.3 s a picture, skipped (not with a client: it awaits too)."""
    monkeypatch.setattr("asyncio.sleep", AsyncMock())


@pytest.fixture
async def server(mock_config: Any) -> FastMCP:
    """A part open, with the sketching, export and automation tools."""
    adapter = MockSolidWorksAdapter({"mock_model_delay": 0})
    await adapter.connect()
    await adapter.create_part()
    mcp = FastMCP("pictures")
    await register_sketching_tools(mcp, adapter, mock_config)
    await register_export_tools(mcp, adapter, mock_config)
    await register_automation_tools(mcp, adapter, mock_config)
    return mcp


async def _run(mcp: FastMCP) -> Any:
    return next(t.fn for t in await mcp.list_tools() if t.name == "execute_workflow")


def _shots(tmp_path: Path, *inline: bool | None) -> list[dict[str, Any]]:
    """One export_image step per entry: its ``inline``, or None to leave it out."""
    steps = []
    for number, asked in enumerate(inline):
        arguments: dict[str, Any] = {"file_path": str(tmp_path / f"shot{number}.png")}
        if asked is not None:
            arguments["inline"] = asked
        steps.append({"export_image": arguments})
    return steps


async def test_an_mcp_client_gets_the_last_picture(
    server: FastMCP, tmp_path: Path
) -> None:
    steps = [{"create_sketch": {"plane": "Top"}}, *_shots(tmp_path, None, None)]

    async with Client(server) as client:
        result = await client.call_tool(
            "execute_workflow", {"input_data": {"steps": steps}}
        )

    assert [block.type for block in result.content] == ["text", "image"]
    assert result.content[1].mimeType == "image/png"
    assert result.structured_content["status"] == "success"
    assert result.structured_content["completed"] == 3


@pytest.mark.parametrize(
    ("inline", "sent"),
    [
        ((None, None), 1),  # the last only
        ((True, None), 2),  # the one asked for, and the last
        ((True, True), 2),  # both asked for: the last isn't sent twice
        ((None, False), 0),  # the last said no; an earlier one isn't the last
        ((True, False), 1),  # only the one asked for
    ],
)
async def test_the_last_picture_and_those_asked_for_come_back(
    server: FastMCP,
    no_waits: None,
    tmp_path: Path,
    inline: tuple[bool | None, ...],
    sent: int,
) -> None:
    run = await _run(server)

    result = await run({"steps": _shots(tmp_path, *inline)}, AsyncMock())

    reply = result.structured_content if isinstance(result, ToolResult) else result
    images = (
        [block for block in result.content if block.type == "image"]
        if isinstance(result, ToolResult)
        else []
    )
    assert reply["status"] == "success", reply
    assert len(images) == sent
    for step in tmp_path.iterdir():
        assert step.stat().st_size > 0  # every file is still written


async def test_a_direct_call_gets_the_reply_alone(
    server: FastMCP, no_waits: None, tmp_path: Path
) -> None:
    run = await _run(server)

    result = await run({"steps": _shots(tmp_path, True, None)})

    assert type(result) is dict
    assert result["warnings"] == []


async def test_a_picture_too_big_to_send_is_a_warning(
    server: FastMCP, no_waits: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export, "INLINE_IMAGE_MAX_BYTES", 10)
    run = await _run(server)

    result = await run({"steps": _shots(tmp_path, True, None, None)}, AsyncMock())

    assert type(result) is dict
    [first, last] = result["warnings"]
    assert first["steps"] == [0]
    assert last["steps"] == [2]
    assert first["warning"].startswith("The picture is not in this reply: it is ")
    assert "over the 0 MB a reply carries" in first["warning"]
    assert str(tmp_path / "shot2.png") in last["warning"]
