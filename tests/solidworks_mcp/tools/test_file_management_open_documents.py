"""activate_document, save_as and save_body_as_part reply with the open documents."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.file_management import (
    register_file_management_tools,
    with_open_documents,
)

LISTED = [{"title": "Lid.SLDPRT", "type": "Part", "active": True, "modified": False}]


@pytest.fixture
async def mock() -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({"mock_model_delay": 0})
    await adapter.connect()
    await adapter.create_part("Bracket")
    await adapter.create_part("Lid")
    return adapter


async def _tools(adapter: Any) -> dict[str, Any]:
    mcp = FastMCP("files")
    await register_file_management_tools(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


async def test_activate_document_lists_after_the_switch(
    mock: MockSolidWorksAdapter,
) -> None:
    tools = await _tools(mock)

    reply = await tools["activate_document"]({"title_or_path": "Bracket"})

    assert reply["message"] == "Activated Bracket"
    assert [(d["title"], d["active"]) for d in reply["open_documents"]] == [
        ("Bracket", True),
        ("Lid", False),
    ]


async def test_activate_document_takes_a_name(mock: MockSolidWorksAdapter) -> None:
    tools = await _tools(mock)

    reply = await tools["activate_document"]({"name": "Bracket"})

    assert reply["status"] == "success", reply
    assert reply["data"]["activated"] == "Bracket"


async def test_save_as_in_a_solidworks_format_lists_the_documents(
    mock: MockSolidWorksAdapter, tmp_path: Path
) -> None:
    tools = await _tools(mock)

    reply = await tools["save_as"]({"file_path": str(tmp_path / "lid.sldprt")})

    assert reply["status"] == "success", reply
    assert [d["title"] for d in reply["open_documents"]] == ["Bracket", "Lid"]


async def test_an_export_through_save_as_lists_nothing(tmp_path: Path) -> None:
    adapter = AsyncMock()
    adapter.export_file = AsyncMock(
        return_value=AdapterResult(status=AdapterResultStatus.SUCCESS, data={})
    )
    del adapter.save_file
    tools = await _tools(adapter)

    reply = await tools["save_as"](
        {"file_path": str(tmp_path / "lid.step"), "format_type": "step"}
    )

    assert reply["status"] == "success", reply
    assert "open_documents" not in reply


async def test_save_body_as_part_lists_the_documents(
    mock: MockSolidWorksAdapter, tmp_path: Path
) -> None:
    tools = await _tools(mock)

    reply = await tools["save_body_as_part"](
        {"body_name": "Boss-Extrude1", "file_path": str(tmp_path / "boss.sldprt")}
    )

    assert reply["status"] == "success", reply
    assert [d["title"] for d in reply["open_documents"]] == ["Bracket", "Lid"]


def test_a_result_without_the_list_adds_nothing() -> None:
    plain = AdapterResult(status=AdapterResultStatus.SUCCESS)
    listed = AdapterResult(
        status=AdapterResultStatus.SUCCESS, metadata={"open_documents": LISTED}
    )

    assert with_open_documents({"status": "success"}, plain) == {"status": "success"}
    assert with_open_documents({}, None) == {}
    assert with_open_documents({}, listed) == {"open_documents": LISTED}
