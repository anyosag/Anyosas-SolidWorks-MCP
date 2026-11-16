"""The ``lookup_api_enum`` tool, answered from the committed swconst index."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP

from solidworks_mcp.tools import docs_discovery
from solidworks_mcp.tools.docs_discovery import register_docs_discovery_tools


async def _call(arguments: dict[str, Any]) -> dict[str, Any]:
    mcp = FastMCP("enums")
    await register_docs_discovery_tools(mcp, object(), {})
    result = await mcp.call_tool("lookup_api_enum", {"input_data": arguments})
    return dict(result.structured_content or {})


async def test_an_enum_by_name_with_the_release_it_came_from() -> None:
    reply = await _call({"query": "swBodyOperationType_e"})

    assert reply["status"] == "success"
    assert reply["enums"] == [
        {
            "enum": "swBodyOperationType_e",
            "member_count": 3,
            "members": {
                "SWBODYINTERSECT": 15901,
                "SWBODYCUT": 15902,
                "SWBODYADD": 15903,
            },
            "members_truncated": False,
        }
    ]
    assert reply["source"]["assembly"] == "SolidWorks.Interop.swconst"
    assert reply["message"].endswith(reply["source"]["solidworks_version"])


async def test_part_of_a_name_the_session_searched_for() -> None:
    reply = await _call({"query": "swInsertPart"})

    assert reply["enums"][0]["enum"] == "swInsertPartOptions_e"
    assert reply["enums"][0]["members"]["swInsertPartImportSolids"] == 1


async def test_a_value_finds_its_member() -> None:
    reply = await _call({"query": "15902"})

    assert reply["members"] == [
        {"enum": "swBodyOperationType_e", "member": "SWBODYCUT", "value": 15902}
    ]


async def test_nothing_matches() -> None:
    reply = await _call({"query": "no-such-thing-xyz"})

    assert reply["status"] == "success"
    assert reply["message"] == "No swconst enum or member matches 'no-such-thing-xyz'"
    assert (reply["enums"], reply["members"]) == ([], [])


async def test_an_unreadable_index_says_how_to_regenerate_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> dict[str, Any]:
        raise OSError("gone")

    monkeypatch.setattr(docs_discovery, "load_enum_index", broken)

    reply = await _call({"query": "swDocPART"})

    assert reply["status"] == "error"
    assert "generate_swconst_enums.py" in reply["message"]
