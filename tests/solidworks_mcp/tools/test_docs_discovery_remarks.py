"""The ``get_api_remarks`` tool over an index built from the fixture help topics."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp import FastMCP

from solidworks_mcp.tools import api_remarks, docs_discovery
from solidworks_mcp.tools.docs_discovery import register_docs_discovery_tools

FIXTURE = Path(__file__).parent / "api_help_fixture"


@pytest.fixture
def help_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A help file whose decompiling copies the fixture topics; a fresh index folder."""
    chm = tmp_path / "sldworksapi.chm"
    chm.write_bytes(b"chm")
    builds: list[Path] = []

    def decompile(copy: Path, out: Path) -> None:
        builds.append(copy)
        for path in FIXTURE.iterdir():
            shutil.copy(path, out / path.name)

    monkeypatch.setattr(api_remarks, "decompile_chm", decompile)
    monkeypatch.setattr(api_remarks, "_short_path", lambda path: path)
    monkeypatch.setenv("SOLIDWORKS_MCP_API_CHM", str(chm))
    monkeypatch.setenv("SOLIDWORKS_MCP_API_REMARKS_DIR", str(tmp_path / "index"))
    return chm


async def _call(arguments: dict[str, Any], config: Any = None) -> dict[str, Any]:
    mcp = FastMCP("remarks")
    await register_docs_discovery_tools(mcp, object(), config or {})
    result = await mcp.call_tool("get_api_remarks", {"input_data": arguments})
    return dict(result.structured_content or {})


async def test_the_first_call_builds_the_index_then_reuses_it(
    help_file: Path, tmp_path: Path
) -> None:
    first = await _call(
        {"interface": "IWidgetManager", "member": "insertwidget", "example": "csharp"}
    )

    assert first["status"] == "success"
    assert first["index_status"] == "built"
    assert first["index_dir"] == str(tmp_path / "index" / "default")
    assert first["source_chm"] == str(help_file)
    assert first["message"] == "Help for IWidgetManager.InsertWidget"
    result = first["result"]
    assert result["member"] == "InsertWidget"
    assert result["kind"] == "method"
    assert result["remarks"].startswith("Before calling this method, select:\n1. ")
    assert [p["name"] for p in result["parameters"]] == ["Mode", "Target"]
    assert result["example"]["title"] == "Insert Widget Example (C#)"
    assert "InsertWidget(1, null)" in result["example"]["code"]

    second = await _call({"interface": "WidgetManager", "member": "Count"})
    assert second["index_status"] == "ready"
    assert second["result"]["returns"] == "Number of widgets"


async def test_the_interface_page_lists_its_members(help_file: Path) -> None:
    reply = await _call({"interface": "iwidgetmanager"})

    assert reply["result"] == {
        "found": True,
        "interface": "IWidgetManager",
        "members": ["Count", "InsertWidget"],
        "kind": "interface",
        "summary": "Allows you to make widgets.",
        "accessors": "IModelDoc2::WidgetManager",
    }


async def test_an_example_by_topic_and_one_that_matches_nothing(
    help_file: Path,
) -> None:
    by_topic = await _call(
        {
            "interface": "IWidgetManager",
            "member": "InsertWidget",
            "example": "Insert_Widget_Example_CSharp.htm",
        }
    )
    missing = await _call(
        {"interface": "IWidgetManager", "member": "InsertWidget", "example": "vba"}
    )

    assert by_topic["result"]["example"]["topic"] == "Insert_Widget_Example_CSharp.htm"
    assert "example" not in missing["result"]
    assert missing["result"]["example_note"].startswith("No example matches 'vba'")


async def test_long_text_and_code_are_cut(
    help_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(docs_discovery, "_REMARKS_TEXT_LIMIT", 10)
    monkeypatch.setattr(docs_discovery, "_EXAMPLE_CODE_LIMIT", 5)

    reply = await _call(
        {"interface": "IWidgetManager", "member": "InsertWidget", "example": "csharp"}
    )

    result = reply["result"]
    assert result["remarks"] == "Before cal [...]"
    assert set(result["truncated"]) == {"summary", "remarks", "availability"}
    assert result["example"]["code"] == "using\n// [...]"
    assert result["example"]["code_truncated"] is True


async def test_an_unknown_interface_or_member(help_file: Path) -> None:
    interface = await _call({"interface": "IGadget"})
    member = await _call({"interface": "IWidgetManager", "member": "Insert"})

    assert interface["message"] == "No help topic found"
    assert interface["result"]["found"] is False
    assert "search_solidworks_api_help" in interface["result"]["note"]
    assert member["result"] == {
        "found": False,
        "interface": "IWidgetManager",
        "member": "Insert",
        "note": "IWidgetManager has no help topic for 'Insert'. Similar: InsertWidget.",
    }
    nothing_close = await _call({"interface": "IWidgetManager", "member": "Zzz"})
    assert nothing_close["result"]["note"].endswith("for 'Zzz'.")


async def test_without_building_it_says_how(help_file: Path) -> None:
    reply = await _call(
        {"interface": "IWidgetManager", "build_if_missing": False},
        SimpleNamespace(solidworks_year=2025),
    )

    assert reply["index_status"] == "missing"
    assert reply["year"] == 2025
    assert reply["result"]["found"] is False
    assert (
        "python -m solidworks_mcp.tools.api_remarks --year 2025"
        in (reply["result"]["note"])
    )


async def test_a_changed_help_file_is_reindexed_or_flagged(help_file: Path) -> None:
    await _call({"interface": "IWidgetManager"})
    help_file.write_bytes(b"a service pack later")

    flagged = await _call({"interface": "IWidgetManager", "build_if_missing": False})
    rebuilt = await _call({"interface": "IWidgetManager"})

    assert flagged["index_status"] == "stale"
    assert flagged["result"]["found"] is True
    assert "changed since the index was built" in flagged["result"]["index_note"]
    assert rebuilt["index_status"] == "built"
    assert "index_note" not in rebuilt["result"]


async def test_an_index_outlives_its_help_file(
    help_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _call({"interface": "IWidgetManager"})
    monkeypatch.setenv("SOLIDWORKS_MCP_API_CHM", str(help_file.with_name("gone.chm")))

    reply = await _call({"interface": "IWidgetManager", "member": "Count"})

    assert reply["index_status"] == "ready"
    assert reply["source_chm"] is None
    assert reply["result"]["found"] is True


async def test_no_help_file_and_no_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SOLIDWORKS_MCP_API_CHM", str(tmp_path / "gone.chm"))
    monkeypatch.setenv("SOLIDWORKS_MCP_API_REMARKS_DIR", str(tmp_path / "index"))

    reply = await _call({"interface": "IWidgetManager", "year": 2026})

    assert reply["status"] == "success"
    assert reply["message"] == "The API help index isn't available"
    assert reply["index_status"] == "unavailable"
    assert reply["index_dir"] == str(tmp_path / "index" / "2026")
    assert "which doesn't exist" in reply["result"]["note"]


async def test_a_failed_build_is_an_error(
    help_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(copy: Path, out: Path) -> None:
        raise api_remarks.RemarksIndexError("hh.exe wrote no topics")

    monkeypatch.setattr(api_remarks, "decompile_chm", broken)

    reply = await _call({"interface": "IWidgetManager"})

    assert reply["status"] == "error"
    assert reply["message"] == "hh.exe wrote no topics"
