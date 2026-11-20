"""The Save Bodies tools, and list_open_documents' check of hidden documents."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.tools.file_management import (
    EditSaveBodiesInput,
    ListOpenDocumentsInput,
    SaveBodiesFeatureInput,
    SaveBodyAsPartInput,
    register_file_management_tools,
)


def _ok(data: Any, warning: str | None = None) -> AdapterResult[Any]:
    return AdapterResult(
        status=AdapterResultStatus.SUCCESS,
        data=data,
        execution_time=0.1,
        metadata={"warning": warning} if warning else None,
    )


async def _tool(adapter: Any, name: str) -> Any:
    mcp = FastMCP("test")
    await register_file_management_tools(mcp, adapter, {})
    return next(t.fn for t in await mcp.list_tools() if t.name == name)


def test_inputs_refuse_what_cannot_be_used() -> None:
    with pytest.raises(ValueError, match="must end in .sldasm"):
        SaveBodyAsPartInput(body_name="B", file_path="C:/b.sldprt", assembly_path="x")
    with pytest.raises(ValueError, match="feature_name is required"):
        EditSaveBodiesInput(feature_name=" ", consume_body=True)
    with pytest.raises(ValueError, match="give at least one of"):
        EditSaveBodiesInput(feature_name="Save Bodies1")
    assert EditSaveBodiesInput(
        feature_name="Save Bodies1", assembly_path=""
    ).changes() == {"assembly_path": ""}


async def test_save_body_as_part_passes_the_assembly_and_the_warning() -> None:
    adapter = SimpleNamespace(
        save_body_as_part=AsyncMock(
            return_value=_ok(
                {"file_path": "C:/c.sldprt", "closed_documents": ["Assem3"]},
                "SolidWorks showed a dialog during the call",
            )
        )
    )
    save = await _tool(adapter, "save_body_as_part")

    reply = await save(
        SaveBodyAsPartInput(
            body_name="Comb", file_path="C:/c.sldprt", assembly_path="C:/a.SLDASM"
        )
    )

    adapter.save_body_as_part.assert_awaited_once_with(
        "Comb", "C:/c.sldprt", "C:/a.SLDASM", list_documents=True
    )
    assert reply["status"] == "success"
    assert reply["warning"] == "SolidWorks showed a dialog during the call"
    assert reply["data"]["closed_documents"] == ["Assem3"]


async def test_list_open_documents_checks_hidden_ones() -> None:
    documents = [
        {"title": "Master.SLDPRT", "is_active": True},
        {"title": "Comb.SLDPRT", "is_active": False, "leftover": True},
    ]
    adapter = SimpleNamespace(
        list_open_documents=AsyncMock(return_value=_ok(documents[:1])),
        list_loaded_documents=AsyncMock(
            return_value=_ok(
                {"documents": documents, "leftovers": ["Comb.SLDPRT"], "count": 2}
            )
        ),
    )
    listing = await _tool(adapter, "list_open_documents")

    plain = await listing()
    checked = await listing(ListOpenDocumentsInput(check_hidden=True))

    assert plain["count"] == 1 and "leftovers" not in plain
    assert checked["leftovers"] == ["Comb.SLDPRT"]
    assert checked["message"] == (
        "2 document(s) open; active: Master.SLDPRT; 1 left over with no window"
    )


async def test_list_open_documents_check_needs_the_capability() -> None:
    adapter = SimpleNamespace(list_open_documents=AsyncMock(return_value=_ok([])))
    listing = await _tool(adapter, "list_open_documents")

    reply = await listing({"check_hidden": True})

    assert reply["status"] == "error"
    assert reply["error_type"] == "not_supported"


async def test_list_save_bodies() -> None:
    features = [{"feature": "Save Bodies1", "bodies": [{"body": "A"}, {"body": "B"}]}]
    adapter = SimpleNamespace(
        get_save_bodies=AsyncMock(return_value=_ok({"features": features}))
    )
    listing = await _tool(adapter, "list_save_bodies")

    reply = await listing(SaveBodiesFeatureInput(feature_name="Save Bodies1"))
    everything = await listing()

    adapter.get_save_bodies.assert_any_await("Save Bodies1")
    adapter.get_save_bodies.assert_any_await(None)
    assert reply["message"] == "1 Save Bodies feature(s), 2 body file(s)"
    assert everything["features"] == features


async def test_edit_save_bodies() -> None:
    adapter = SimpleNamespace(
        edit_save_bodies=AsyncMock(return_value=_ok({"changed": ["consume_body"]}))
    )
    edit = await _tool(adapter, "edit_save_bodies")
    request = EditSaveBodiesInput(feature_name="Save Bodies1", consume_body=True)

    done = await edit(request)

    adapter.edit_save_bodies.assert_awaited_once_with(
        "Save Bodies1", {"consume_body": True}
    )
    assert done["message"] == "Changed Save Bodies1: consume_body"


async def test_resplit_bodies_reports_each_file() -> None:
    files = [
        {"file_path": "C:/a.sldprt", "status": "updated", "holds_body": True},
        {"file_path": "C:/b.sldprt", "status": "updated", "holds_body": False},
        {"file_path": "C:/c.sldprt", "status": "kept_modified"},
    ]
    adapter = SimpleNamespace(
        resplit_bodies=AsyncMock(
            side_effect=[
                _ok({"files": files, "counts": {}}, "SolidWorks showed a dialog."),
                _ok({"files": files[2:], "counts": {}}),
                _ok({"files": files[:1], "counts": {}}),
            ]
        )
    )
    resplit = await _tool(adapter, "resplit_bodies")

    mixed = await resplit()
    nothing = await resplit(SaveBodiesFeatureInput(feature_name="Save Bodies1"))
    clean = await resplit()

    assert mixed["message"] == "Updated 2 of 3 part file(s)"
    assert mixed["warning"].startswith("SolidWorks showed a dialog.")
    assert "C:/b.sldprt" in mixed["warning"]
    assert "1 file(s) were not updated" in mixed["warning"]
    assert nothing["status"] == "error"
    assert "No part file was updated" in nothing["message"]
    assert "not updated" not in clean.get("warning", "")  # only the level note


@pytest.mark.parametrize(
    "tool", ["list_save_bodies", "edit_save_bodies", "resplit_bodies"]
)
async def test_the_tools_need_the_capability(tool: str) -> None:
    run = await _tool(object(), tool)
    payload = {"feature_name": "Save Bodies1", "consume_body": True}

    reply = await run(payload)

    assert reply["status"] == "error"
    assert reply["error_type"] == "not_supported"


async def test_the_mock_round_trip(mock_adapter: Any, tmp_path: Path) -> None:
    await mock_adapter.create_part()
    save = await _tool(mock_adapter, "save_body_as_part")
    listing = await _tool(mock_adapter, "list_save_bodies")

    saved = await save(
        SaveBodyAsPartInput(body_name="Comb", file_path=str(tmp_path / "c.sldprt"))
    )
    listed = await listing()

    assert saved["data"]["file_check"]["exists"] is True
    assert listed["features"][0]["bodies"][0]["body"] == "Comb"
