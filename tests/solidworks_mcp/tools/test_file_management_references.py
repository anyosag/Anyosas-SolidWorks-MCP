"""``list_references`` and ``move_document``: files and the documents using them."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.file_management import register_file_management_tools


async def _tools(adapter: Any, config: Any) -> dict[str, Any]:
    mcp = FastMCP("files")
    await register_file_management_tools(mcp, adapter, config)
    return {t.name: t.fn for t in await mcp.list_tools()}


@pytest.fixture
async def mock(tmp_path: Path) -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({"mock_model_delay": 0})
    await adapter.connect()
    for name in ("bracket.sldprt", "frame.sldasm"):
        (tmp_path / name).write_bytes(b"sw")
    adapter.document_references = {
        str(tmp_path / "frame.sldasm"): [
            str(tmp_path / "bracket.sldprt"),
            str(tmp_path / "gone.sldprt"),
        ]
    }
    return adapter


async def test_references_are_listed(mock, mock_config, tmp_path) -> None:
    tools = await _tools(mock, mock_config)

    result = await tools["list_references"](
        {"file_path": str(tmp_path / "frame.sldasm")}
    )
    clean = await tools["list_references"](
        {"file_path": str(tmp_path / "bracket.sldprt")}
    )

    assert result["status"] == "success"
    assert result["message"] == "2 reference(s), 1 missing"
    assert result["data"]["missing"] == [str(tmp_path / "gone.sldprt")]
    assert clean["message"] == "0 reference(s), all found"


async def test_a_missing_file_is_not_found(mock, mock_config, tmp_path) -> None:
    tools = await _tools(mock, mock_config)

    result = await tools["list_references"]({"file_path": str(tmp_path / "x.sldprt")})

    assert result["error_type"] == "not_found"


async def test_a_dry_run_says_what_would_change(mock, mock_config, tmp_path) -> None:
    tools = await _tools(mock, mock_config)

    result = await tools["move_document"](
        {
            "source": str(tmp_path / "bracket.sldprt"),
            "destination": str(tmp_path / "plate.sldprt"),
        }
    )

    assert result["status"] == "success"
    assert result["message"] == (
        f"Dry run: moving {tmp_path / 'bracket.sldprt'} to "
        f"{tmp_path / 'plate.sldprt'} would repoint 1 document(s); nothing was "
        "changed"
    )
    assert result["data"]["can_move"] is True
    # Only the experimental level's sentence: nothing stops the move.
    assert result["warning"].startswith("Experimental:")


async def test_a_move_moves_and_repoints(mock, mock_config, tmp_path) -> None:
    tools = await _tools(mock, mock_config)

    result = await tools["move_document"](
        {
            "source": str(tmp_path / "bracket.sldprt"),
            "destination": str(tmp_path / "plate.sldprt"),
            "dry_run": False,
        }
    )

    assert result["message"].endswith("and repointed 1 document(s)")
    assert (tmp_path / "plate.sldprt").exists()


async def test_what_stops_the_move_is_a_warning_on_a_dry_run(
    mock, mock_config, tmp_path
) -> None:
    await mock.open_model(str(tmp_path / "bracket.sldprt"))
    tools = await _tools(mock, mock_config)
    move = {
        "source": str(tmp_path / "bracket.sldprt"),
        "destination": str(tmp_path / "plate.sldprt"),
    }

    dry = await tools["move_document"](move)
    real = await tools["move_document"]({**move, "dry_run": False})

    assert dry["warning"].startswith("It can't be moved yet: ")
    assert "is open in SolidWorks" in dry["warning"]
    assert real["error_type"] == "invalid_input"


async def test_a_truncated_scan_and_an_unverified_repoint_are_warnings(
    mock_config,
) -> None:
    data = {
        "dry_run": False,
        "source": "C:/a.sldprt",
        "destination": "C:/b.sldprt",
        "users": [{"path": "C:/f.sldasm"}],
        "blocked": [],
        "scan_truncated": True,
        "updated": [{"path": "C:/f.sldasm", "verified": False}],
    }
    stub = SimpleNamespace(
        move_document=AsyncMock(
            return_value=AdapterResult(status=AdapterResultStatus.SUCCESS, data=data)
        )
    )
    tools = await _tools(stub, mock_config)

    result = await tools["move_document"](
        {"source": "C:/a.sldprt", "destination": "C:/b.sldprt", "dry_run": False}
    )

    assert "only the first ones were checked" in result["warning"]
    assert "did not show the new path: C:/f.sldasm" in result["warning"]
    stub.move_document.assert_awaited_once_with(
        "C:/a.sldprt", "C:/b.sldprt", None, None, False
    )


async def test_a_plain_file_move_and_a_sync_folder_are_said(mock_config) -> None:
    method = "file move (MoveDocument returned 2 (swMoveCopyErrorFail))"
    data = {
        "dry_run": False,
        "source": "G:/Shared drives/T/a.sldprt",
        "destination": "G:/Shared drives/T/b.sldprt",
        "users": [],
        "blocked": [],
        "warnings": ["G:/Shared drives/T/a.sldprt is in a Google Drive folder."],
        "method": method,
        "updated": [],
    }
    stub = SimpleNamespace(
        move_document=AsyncMock(
            return_value=AdapterResult(status=AdapterResultStatus.SUCCESS, data=data)
        )
    )
    tools = await _tools(stub, mock_config)

    result = await tools["move_document"](
        {"source": data["source"], "destination": data["destination"], "dry_run": False}
    )

    assert result["message"] == (
        f"Moved {data['source']} to {data['destination']} by {method} and "
        "repointed 0 document(s)"
    )
    assert "is in a Google Drive folder." in result["warning"]


async def test_a_dry_run_in_a_sync_folder_warns(mock, mock_config, tmp_path) -> None:
    drive = tmp_path / "My Drive"
    drive.mkdir()
    (drive / "lid.sldprt").write_bytes(b"sw")
    tools = await _tools(mock, mock_config)

    result = await tools["move_document"](
        {
            "source": str(drive / "lid.sldprt"),
            "destination": str(drive / "lid2.sldprt"),
        }
    )

    assert result["data"]["can_move"] is True
    assert f"{drive / 'lid.sldprt'} is in a Google Drive folder" in result["warning"]


async def test_a_bad_input_is_refused(mock, mock_config) -> None:
    tools = await _tools(mock, mock_config)

    assert (await tools["move_document"]({"source": "C:/a.sldprt"}))[
        "error_type"
    ] == "invalid_input"
    assert (await tools["list_references"]({"file_path": ""}))[
        "error_type"
    ] == "invalid_input"


@pytest.mark.parametrize(
    ("tool", "payload"),
    [
        ("list_references", {"file_path": "C:/a.sldprt"}),
        ("move_document", {"source": "C:/a.sldprt", "destination": "C:/b.sldprt"}),
    ],
)
async def test_an_adapter_without_them_is_not_supported(
    mock_config, tool: str, payload: dict
) -> None:
    tools = await _tools(SimpleNamespace(), mock_config)

    assert (await tools[tool](payload))["error_type"] == "not_supported"
