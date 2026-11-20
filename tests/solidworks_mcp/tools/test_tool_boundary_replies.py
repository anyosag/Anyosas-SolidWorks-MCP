"""What the boundary adds to a reply: the server's time, the timing, sent content.

``execution_time`` is a tool's own timing, often one COM call; 83 of 253
minutes of server round trips were outside it (2026-11-20 sample). Every
success now also says the whole call's time in the server, the server log
gets one line per call saying where it went, and ``SOLIDWORKS_MCP_TIMING=1``
puts that breakdown in the reply. A tool can send MCP content (a picture)
beside its reply, which stays the call's structured content.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import ANY

import pytest
from fastmcp import Client, FastMCP
from fastmcp.tools import ToolResult
from fastmcp.utilities.types import Image
from loguru import logger

from solidworks_mcp.tools.support_levels import Support, SupportLevel
from solidworks_mcp.tools.tool_boundary import (
    ToolOperationError,
    ToolRegistrar,
    timing_in_replies,
    tool_boundary,
    with_content,
)
from solidworks_mcp.tools.tool_hints import ToolHints

#: The smallest PNG there is: 1x1, white.
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff"
    b"\xff\x00\x05\xfe\x02\xfe\xdc\xcaY\xe7\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def log_lines() -> Iterator[list[str]]:
    """The messages logged while the test runs."""
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(message.record["message"]))
    yield lines
    logger.remove(sink)


# ---- server_time_s (a success's own is in test_tool_boundary.py) -------------------


async def test_a_success_keeps_its_own_execution_time() -> None:
    @tool_boundary
    async def tool() -> dict[str, Any]:
        return {"status": "success", "execution_time": 0.25}

    result = await tool()

    assert result == {"status": "success", "execution_time": 0.25, "server_time_s": ANY}
    assert result["server_time_s"] >= 0.0


async def test_an_error_keeps_its_shape() -> None:
    @tool_boundary
    async def tool() -> dict[str, Any]:
        raise ToolOperationError("Failed to create extrusion", 0.5)

    result = await tool()

    assert set(result) == {"status", "message", "error_type", "hint", "execution_time"}


async def test_a_reply_neither_success_nor_error_passes_through() -> None:
    @tool_boundary
    async def tool() -> dict[str, Any]:
        return {"status": "warning", "message": "partly"}

    assert await tool() == {"status": "warning", "message": "partly"}


# ---- the timing line and SOLIDWORKS_MCP_TIMING ----------------------------------------


async def test_each_call_logs_one_timing_line(log_lines: list[str]) -> None:
    @tool_boundary
    async def inner() -> dict[str, Any]:
        return {"status": "success"}

    @tool_boundary
    async def outer() -> dict[str, Any]:
        await inner()
        return {"status": "success"}

    await outer()

    timing = [line for line in log_lines if line.startswith("Timing ")]
    assert len(timing) == 2
    assert timing[0].startswith("Timing inner (in outer): ")
    assert timing[1].startswith("Timing outer: ")
    assert timing[1].endswith("0 COM operations")


async def test_timing_comes_back_in_every_reply_when_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOLIDWORKS_MCP_TIMING", "1")

    @tool_boundary
    async def works() -> dict[str, Any]:
        return {"status": "success"}

    @tool_boundary
    async def fails() -> dict[str, Any]:
        raise ToolOperationError("Feature not found: X")

    ok, failed = await works(), await fails()

    for result in (ok, failed):
        assert result["timing"] == {
            "server_s": ANY,
            "com_operations": 0,
            "pace_s": 0.0,
            "pre_checks_s": 0.0,
            "closure_s": 0.0,
            "post_checks_s": 0.0,
            "other_s": ANY,
            "operations": {},
        }
    assert ok["timing"]["server_s"] == ok["server_time_s"]


@pytest.mark.parametrize(
    ("value", "asked"),
    [
        ("1", True),
        (" TRUE ", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("", False),
    ],
)
def test_timing_is_asked_for_with_a_true_value(
    monkeypatch: pytest.MonkeyPatch, value: str, asked: bool
) -> None:
    monkeypatch.setenv("SOLIDWORKS_MCP_TIMING", value)

    assert timing_in_replies() is asked


def test_timing_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOLIDWORKS_MCP_TIMING", raising=False)

    assert timing_in_replies() is False


# ---- content beside the reply -------------------------------------------------------


async def test_content_goes_beside_the_reply() -> None:
    @tool_boundary
    async def picture() -> dict[str, Any]:
        reply = {"status": "success", "message": "Exported image"}
        return with_content(reply, Image(data=_PNG, format="png"))

    result = await picture()

    assert isinstance(result, ToolResult)
    assert result.structured_content == {
        "status": "success",
        "message": "Exported image",
        "server_time_s": ANY,
    }
    text, image = result.content
    assert json.loads(text.text) == result.structured_content
    assert image.type == "image"
    assert image.mimeType == "image/png"


async def test_a_reply_with_no_content_stays_a_dict() -> None:
    @tool_boundary
    async def nothing_beside() -> dict[str, Any]:
        return with_content({"status": "success"})

    result = await nothing_beside()

    assert type(result) is dict
    assert result == {"status": "success", "server_time_s": ANY}


async def test_an_mcp_client_gets_the_image_block_and_the_reply() -> None:
    mcp = FastMCP("content")
    hints = {
        "body": ToolHints("Body", read_only=True, destructive=False, idempotent=True)
    }
    registrar = ToolRegistrar(
        mcp, hints=hints, levels={"body": Support(SupportLevel.BETA, "new.")}
    )

    @registrar.tool()
    async def body() -> dict[str, Any]:
        return with_content({"status": "success"}, Image(data=_PNG, format="png"))

    async with Client(mcp) as client:
        result = await client.call_tool("body", {})

    assert [block.type for block in result.content] == ["text", "image"]
    # The level's warning and the server time reach the structured reply.
    assert result.structured_content == {
        "status": "success",
        "warning": "Beta: new.",
        "server_time_s": ANY,
    }
    assert result.data == result.structured_content
