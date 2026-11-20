"""The document tools reply with the open documents, and open_model with a summary.

An agent called ``list_open_documents`` after a document operation, and
``list_features`` after ``open_model`` (speed report, section 3); the replies
now carry what those reads gave.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import (
    AdapterResult,
    AdapterResultStatus,
    ExtrusionParameters,
)
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.modeling import model_summary, register_modeling_tools


@pytest.fixture
async def mock() -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({"mock_model_delay": 0})
    await adapter.connect()
    return adapter


async def _tools(adapter: Any) -> dict[str, Any]:
    mcp = FastMCP("documents")
    await register_modeling_tools(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


def _row(name: str, kind: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "type": kind, "component": None, **extra}


async def test_open_model_gives_the_summary_and_the_open_documents(
    mock: MockSolidWorksAdapter,
) -> None:
    tools = await _tools(mock)
    await mock.create_part("Plate")
    await mock.create_sketch("Front Plane")
    await mock.add_rectangle(0, 0, 10, 10)
    await mock.exit_sketch()
    await mock.create_extrusion(ExtrusionParameters(depth=5))

    reply = await tools["open_model"]({"file_path": "C:/work/Lid.SLDPRT"})

    assert reply["status"] == "success", reply
    assert reply["summary"] == {
        "features": 1,
        "by_type": {"Extrusion": 1},
        "last": ["Boss-Extrude1"],
        "bodies": {"count": 1, "names": ["Boss-Extrude1"]},
    }
    assert reply["open_documents"] == [
        {"title": "Plate", "type": "Part", "active": False, "modified": False},
        {"title": "Lid.SLDPRT", "type": "Part", "active": True, "modified": False},
    ]


async def test_a_read_only_open_summarises_too(mock: MockSolidWorksAdapter) -> None:
    tools = await _tools(mock)

    reply = await tools["open_model"](
        {"file_path": "C:/work/Rack.SLDASM", "read_only": True}
    )

    assert reply["model"]["read_only"] is True
    assert reply["summary"] == {"features": 0, "by_type": {}, "last": []}
    assert reply["open_documents"][0]["type"] == "Assembly"


async def test_an_adapter_without_them_replies_as_before() -> None:
    adapter = AsyncMock()
    adapter.open_model = AsyncMock(
        return_value=AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"title": "Lid", "type": "Part", "path": "C:/Lid.SLDPRT"},
        )
    )
    tools = await _tools(adapter)

    reply = await tools["open_model"]({"file_path": "C:/Lid.SLDPRT"})

    assert reply["status"] == "success"
    assert "summary" not in reply and "open_documents" not in reply
    adapter.open_model.assert_awaited_once_with(
        "C:/Lid.SLDPRT", list_documents=True, summarize=True
    )


async def test_new_and_closed_documents_list_the_open_ones(
    mock: MockSolidWorksAdapter,
) -> None:
    tools = await _tools(mock)

    part = await tools["create_part"]({})
    assembly = await tools["create_assembly"]({"name": "Rig"})
    closed = await tools["close_model"]({})

    assert part["part"]["name"] == "Part1"
    assert [d["title"] for d in part["open_documents"]] == ["Part1"]
    assert [(d["title"], d["active"]) for d in assembly["open_documents"]] == [
        ("Part1", False),
        ("Rig", True),
    ]
    assert closed["message"] == "Model closed successfully"
    assert [d["title"] for d in closed["open_documents"]] == ["Part1"]


async def test_close_leftovers_alone_has_no_list(mock: MockSolidWorksAdapter) -> None:
    tools = await _tools(mock)
    await mock.create_part("Plate")

    reply = await tools["close_model"]({"close_leftovers": True, "keep_active": True})

    assert reply["status"] == "success"
    assert "open_documents" not in reply


async def test_the_name_of_a_new_document_is_optional(
    mock: MockSolidWorksAdapter,
) -> None:
    tools = await _tools(mock)

    drawing = await tools["create_drawing"]({"name": "  "})
    assembly = await tools["create_assembly"]({})

    assert drawing["status"] == "success" and drawing["drawing"]["name"]
    assert assembly["status"] == "success"
    assert assembly["assembly"]["name"].startswith("Assembly")


# ---- model_summary ------------------------------------------------------------


def test_the_summary_leaves_out_the_tree_furniture() -> None:
    rows = [
        _row("Comments", "CommentsFolder"),
        _row("Front Plane", "RefPlane"),
        _row("Top Plane", "RefPlane"),
        _row("Right Plane", "RefPlane"),
        _row("Origin", "OriginProfileFeature"),
        _row("Sketch1", "ProfileFeature"),
        _row("Boss-Extrude1", "Extrusion"),
        _row("Plane1", "RefPlane"),
        _row("Sketch2", "ProfileFeature"),
        _row("Cut-Extrude1", "Cut", suppressed=True),
        _row("Fillet1", "Fillet"),
        _row("Fillet2", "Fillet"),
        _row("Pin-1", "Extrusion", component="Pin-1"),
    ]

    summary = model_summary({"features": rows, "bodies": ["Body"] * 12})

    assert summary == {
        "features": 7,
        "by_type": {
            "ProfileFeature": 2,
            "Extrusion": 1,
            "RefPlane": 1,
            "Cut": 1,
            "Fillet": 2,
        },
        "last": ["Plane1", "Sketch2", "Cut-Extrude1", "Fillet1", "Fillet2"],
        "suppressed": 1,
        "bodies": {"count": 12, "names": ["Body"] * 10},
    }


@pytest.mark.parametrize("contents", [None, {}, {"features": None}, "rows"])
def test_no_tree_read_means_no_summary(contents: Any) -> None:
    assert model_summary(contents) is None


def test_odd_rows_are_named_unknown_and_an_assembly_has_no_bodies() -> None:
    summary = model_summary({"features": [{"name": None}, "junk"], "bodies": None})

    assert summary == {"features": 1, "by_type": {"Unknown": 1}, "last": [""]}
