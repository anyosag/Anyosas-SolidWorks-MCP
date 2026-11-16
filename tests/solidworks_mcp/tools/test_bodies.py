"""The multibody tools on the mock: replies, input rules, warnings and errors."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.bodies import register_bodies_tools
from solidworks_mcp.tools.target_document import takes_document


@pytest.fixture
async def lid() -> tuple[MockSolidWorksAdapter, dict[str, Any]]:
    """A lid with two labels sunk 1 mm into its underside, and the tools."""
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    adapter.part_bodies_mm = {
        "Lid": [0.0, 0.0, 0.0, 40.0, 10.0, 40.0],
        "Label1": [5.0, 9.0, 5.0, 15.0, 11.0, 8.0],
        "Label2": [20.0, 9.0, 5.0, 30.0, 11.0, 8.0],
    }
    await adapter.create_part()
    mcp = FastMCP("bodies")
    count = await register_bodies_tools(mcp, adapter, {})
    tools = {t.name: t.fn for t in await mcp.list_tools()}
    assert count == len(tools) == 8
    return adapter, tools


def _stub(adapter: MockSolidWorksAdapter, method: str, data: dict[str, Any]) -> None:
    ok = AdapterResult(status=AdapterResultStatus.SUCCESS, data=data)
    setattr(adapter, method, AsyncMock(return_value=ok))


def test_the_family_takes_document() -> None:
    assert takes_document("make_mating_negative", "bodies")
    assert takes_document("list_bodies", "bodies")


async def test_list_bodies(lid: Any) -> None:
    _adapter, tools = lid

    result = await tools["list_bodies"]()

    assert result["status"] == "success", result
    assert result["message"] == "The part has 3 solid bodies: Lid, Label1, Label2"
    assert result["data"]["count"] == 3
    assert result["warning"].startswith("Experimental:")


async def test_a_long_list_is_cut_short_in_the_message(lid: Any) -> None:
    adapter, tools = lid
    _stub(
        adapter,
        "list_bodies",
        {"bodies": [{"name": f"B{i}"} for i in range(25)], "count": 25},
    )

    result = await tools["list_bodies"]({"visible_only": True})

    assert result["message"].endswith("B19 ...")
    adapter.list_bodies.assert_awaited_once_with(True)


async def test_an_empty_part(lid: Any) -> None:
    adapter, tools = lid
    _stub(adapter, "list_bodies", {"bodies": [], "count": 0})

    assert (await tools["list_bodies"]())["message"] == "The part has 0 solid bodies"


async def test_insert_part(lid: Any, tmp_path: Path) -> None:
    _adapter, tools = lid
    holder = tmp_path / "holder.SLDPRT"
    holder.write_text("")

    result = await tools["insert_part"](
        {"file_path": f"  {holder}  ", "translate_mm": [0, 9, 0], "break_link": True}
    )

    assert result["status"] == "success", result
    assert result["message"] == f"Inserted 1 body from {holder}: holder-1"
    assert result["data"]["move_features"] == ["Body-Move/Copy1"]


@pytest.mark.parametrize(
    ("arguments", "fragment"),
    [
        ({"file_path": "C:/parts/holder.step"}, "must be a .sldprt"),
        ({"file_path": "  "}, "file_path is required"),
        ({"file_path": "C:/h.sldprt", "translate_mm": [1, 2]}, "translate_mm"),
        ({"file_path": "C:/h.sldprt", "axis": "w"}, "axis"),
    ],
)
async def test_insert_part_input(
    lid: Any, arguments: dict[str, Any], fragment: str
) -> None:
    _adapter, tools = lid

    result = await tools["insert_part"](arguments)

    assert result["error_type"] == "invalid_input"
    assert fragment in result["message"]


async def test_a_missing_part_file_is_not_found(lid: Any, tmp_path: Path) -> None:
    _adapter, tools = lid

    result = await tools["insert_part"]({"file_path": str(tmp_path / "gone.sldprt")})

    assert result["error_type"] == "not_found"


async def test_combine_bodies(lid: Any) -> None:
    _adapter, tools = lid

    result = await tools["combine_bodies"](
        {"operation": "subtract", "main": "Lid", "tools": [" Label1 "]}
    )

    assert result["status"] == "success", result
    assert result["message"] == ("Combined (subtract) Lid with Label1 in Combine1: Lid")


async def test_a_combine_that_yields_nothing_says_so(lid: Any) -> None:
    adapter, tools = lid
    _stub(adapter, "combine_bodies", {"feature": "Combine1", "result": []})

    result = await tools["combine_bodies"](
        {"operation": "common", "main": "Lid", "tools": ["Label1"]}
    )

    assert result["message"].endswith(": no body")


@pytest.mark.parametrize(
    ("arguments", "fragment"),
    [
        ({"operation": "add", "main": "Lid", "tools": ["Lid"]}, "also among the tools"),
        (
            {"operation": "add", "main": "Lid", "tools": ["A", " A"]},
            "names a body twice",
        ),
        ({"operation": "add", "main": "Lid", "tools": [""]}, "blank body name"),
        ({"operation": "add", "main": "Lid", "tools": []}, "tools"),
        ({"operation": "xor", "main": "Lid", "tools": ["A"]}, "operation"),
    ],
)
async def test_combine_input(
    lid: Any, arguments: dict[str, Any], fragment: str
) -> None:
    _adapter, tools = lid

    result = await tools["combine_bodies"](arguments)

    assert result["error_type"] == "invalid_input"
    assert fragment in result["message"]


async def test_an_unknown_body_is_not_found(lid: Any) -> None:
    _adapter, tools = lid

    result = await tools["combine_bodies"](
        {"operation": "add", "main": "Lid", "tools": ["Label9"]}
    )

    assert result["error_type"] == "not_found"
    assert "The part's solid bodies: Lid, Label1, Label2" in result["message"]


async def test_move_and_copy(lid: Any) -> None:
    _adapter, tools = lid

    moved = await tools["move_copy_body"](
        {"bodies": ["Label1"], "translate_mm": [0, 1, 0]}
    )
    copied = await tools["move_copy_body"](
        {"bodies": ["Label2"], "translate_mm": [1, 0, 0], "copy": True, "copies": 2}
    )

    assert moved["message"] == "Moved Label1"
    assert moved["warning"].startswith("Experimental:")
    assert copied["message"] == (
        "Copied Label2 into Body-Move/Copy2[1], Body-Move/Copy2[2]"
    )


async def test_a_copy_that_made_nothing_says_so(lid: Any) -> None:
    adapter, tools = lid
    _stub(adapter, "move_copy_body", {"new_bodies": []})

    result = await tools["move_copy_body"](
        {"bodies": ["Label2"], "rotate_deg": 90, "copy": True}
    )

    assert result["message"] == "Copied Label2 into no new body"
    assert adapter.move_copy_body.await_args.args == (
        ["Label2"],
        None,
        90.0,
        "z",
        None,
        True,
        1,
    )


async def test_a_shift_not_as_asked_is_a_warning(lid: Any) -> None:
    adapter, tools = lid
    _stub(
        adapter,
        "move_copy_body",
        {"moved": [{"name": "Label1", "center_shift_mm": [0.0, 0.5, 0.0]}]},
    )

    result = await tools["move_copy_body"](
        {"bodies": ["Label1"], "translate_mm": [0, 1, 0]}
    )

    assert result["warning"].startswith(
        "SolidWorks shifted Label1 by [0.0, 0.5, 0.0] mm, not the [0.0, 1.0, 0.0] mm"
    )


@pytest.mark.parametrize(
    ("arguments", "fragment"),
    [
        ({"bodies": ["A"]}, "nothing would move"),
        (
            {"bodies": ["A"], "translate_mm": [1, 0, 0], "copies": 3},
            "copies needs copy",
        ),
        ({"bodies": ["A"], "rotate_deg": 400}, "rotate_deg"),
    ],
)
async def test_move_input(lid: Any, arguments: dict[str, Any], fragment: str) -> None:
    _adapter, tools = lid

    result = await tools["move_copy_body"](arguments)

    assert result["error_type"] == "invalid_input"
    assert fragment in result["message"]


async def test_delete_keep_body(lid: Any) -> None:
    _adapter, tools = lid

    result = await tools["delete_keep_body"]({"bodies": ["Label1", "Label2"]})

    assert result["message"] == "Deleted Label1, Label2"
    assert result["warning"].startswith("Experimental:")


async def test_removing_other_bodies_than_expected_is_a_warning(lid: Any) -> None:
    adapter, tools = lid
    _stub(adapter, "delete_keep_body", {"removed": [], "expected_removed": ["Label1"]})

    result = await tools["delete_keep_body"]({"bodies": ["Label1"]})

    assert result["message"] == "Deleted no body"
    assert result["warning"].startswith("SolidWorks removed nothing, not Label1;")


async def test_keeping_every_body_is_invalid(lid: Any) -> None:
    _adapter, tools = lid

    result = await tools["delete_keep_body"](
        {"bodies": ["Lid", "Label1", "Label2"], "keep": True}
    )

    assert result["error_type"] == "invalid_input"


async def test_split_body(lid: Any) -> None:
    adapter, tools = lid
    adapter._mock_bodies()["Lid"]["box"] = [-20.0, 0.0, 0.0, 20.0, 10.0, 40.0]

    result = await tools["split_body"]({"plane": "Right", "body": " Lid "})

    assert (
        result["message"]
        == "Split along Right Plane into 2 pieces: Split1[1], Split1[2]"
    )


@pytest.mark.parametrize(
    "arguments", [{}, {"plane": "Top", "sketch": "Sketch1"}, {"plane": "  "}]
)
async def test_split_needs_one_tool(lid: Any, arguments: dict[str, Any]) -> None:
    _adapter, tools = lid

    result = await tools["split_body"](arguments)

    assert result["error_type"] == "invalid_input"
    assert "give plane or sketch" in result["message"]


async def test_body_clearance(lid: Any) -> None:
    _adapter, tools = lid

    apart = await tools["body_clearance"]({"body_a": "Label1", "body_b": "Label2"})
    overlap = await tools["body_clearance"]({"body_a": "Label1", "body_b": "Lid"})

    assert apart["message"] == "Clearance between Label1 and Label2: 5.0 mm"
    assert overlap["message"] == "Label1 and Lid overlap by 30.0 mm3"
    assert overlap["warning"].startswith(
        "Label1 and Lid overlap by 30.0 mm3, so there is no clearance to report."
    )


async def test_bodies_that_only_touch(lid: Any) -> None:
    adapter, tools = lid
    _stub(adapter, "body_clearance", {"touching": True, "overlap_mm3": 0.0})

    result = await tools["body_clearance"]({"body_a": "A", "body_b": "B"})

    assert result["message"] == "A and B touch"


@pytest.mark.parametrize(
    ("arguments", "fragment"),
    [
        ({"body_a": "Lid", "body_b": " Lid"}, "both 'Lid'"),
        ({"body_a": "Lid", "body_b": ""}, "both required"),
    ],
)
async def test_clearance_input(
    lid: Any, arguments: dict[str, Any], fragment: str
) -> None:
    _adapter, tools = lid

    result = await tools["body_clearance"](arguments)

    assert result["error_type"] == "invalid_input"
    assert fragment in result["message"]


async def test_make_mating_negative(lid: Any) -> None:
    _adapter, tools = lid

    result = await tools["make_mating_negative"](
        {"target": "Lid", "tools": ["Label1", "Label2"], "clearance_mm": 0.15}
    )

    assert result["status"] == "success", result
    assert result["message"].startswith("Cut 78.177 mm3 from Lid around 2 tools with")
    assert result["message"].endswith(
        "0.15 mm clearance (shifted_copies); the tool bodies are still in the part"
    )
    assert result["warning"].startswith("Experimental:")


async def test_one_tool_by_the_name_tool(lid: Any) -> None:
    adapter, tools = lid
    adapter.make_mating_negative = AsyncMock(  # type: ignore[method-assign]
        wraps=adapter.make_mating_negative
    )

    result = await tools["make_mating_negative"](
        {"target": "Lid", "tool": "Label1", "clearance_mm": 0.2, "directions": 8}
    )

    assert result["status"] == "success", result
    adapter.make_mating_negative.assert_awaited_once_with(
        "Lid", ["Label1"], 0.2, 8, "y", "auto"
    )


async def test_a_negative_that_cut_nothing_or_left_overlap_warns(lid: Any) -> None:
    adapter, tools = lid
    _stub(
        adapter,
        "make_mating_negative",
        {"volume_removed_mm3": 0.0, "residual_overlap_mm3": 0.5, "method": "indent"},
    )

    result = await tools["make_mating_negative"](
        {"target": "Lid", "tools": "Label1", "clearance_mm": 0.15}
    )

    assert result["warning"].startswith("Nothing was removed: the tools, grown by")
    assert "still overlap the result by 0.5 mm3" in result["warning"]


@pytest.mark.parametrize(
    ("arguments", "fragment"),
    [
        ({"target": "Lid", "tools": ["Lid"], "clearance_mm": 0.1}, "also among"),
        ({"target": "Lid", "tools": ["A"], "clearance_mm": 0}, "clearance_mm"),
        (
            {"target": "Lid", "tools": ["A"], "clearance_mm": 0.1, "directions": 2},
            "directions",
        ),
        ({"target": " ", "tools": ["A"], "clearance_mm": 0.1}, "target is required"),
    ],
)
async def test_negative_input(
    lid: Any, arguments: dict[str, Any], fragment: str
) -> None:
    _adapter, tools = lid

    result = await tools["make_mating_negative"](arguments)

    assert result["error_type"] == "invalid_input"
    assert fragment in result["message"]


async def test_an_assembly_is_the_wrong_document(lid: Any) -> None:
    adapter, tools = lid
    await adapter.create_assembly()

    result = await tools["list_bodies"]()

    assert result["error_type"] == "wrong_document_type"


async def test_an_adapter_without_the_method_is_not_supported() -> None:
    class Bare:
        pass

    mcp = FastMCP("bare")
    await register_bodies_tools(mcp, Bare(), {})  # type: ignore[arg-type]
    tool = next(t for t in await mcp.list_tools() if t.name == "body_clearance")

    result = await tool.fn({"body_a": "A", "body_b": "B"})

    assert result["error_type"] == "not_supported"
