"""Listed tool schemas drop nullable wrappers and input aliases.

Only what ``tools/list`` advertises changes; a call is still validated against
the tool function's own pydantic types.
"""

from __future__ import annotations

import pytest
from fastmcp import FastMCP
from pydantic import BaseModel, Field

from solidworks_mcp.tools.schema_compaction import (
    DESCRIPTION_CHARS,
    INPUT_DATA_CHARS,
    CompactToolSchemas,
    compact_schema,
    compact_tool_schema,
    shorten_description,
)


def test_a_nullable_field_becomes_its_plain_type() -> None:
    schema = {
        "anyOf": [{"type": "number"}, {"type": "null"}],
        "default": None,
        "description": "Depth in mm",
    }

    assert compact_schema(schema) == {"type": "number", "description": "Depth in mm"}


def test_a_real_default_is_kept() -> None:
    schema = {"anyOf": [{"type": "string"}, {"type": "null"}], "default": "Blind"}

    assert compact_schema(schema) == {"type": "string", "default": "Blind"}


def test_a_union_of_two_real_types_is_left_alone() -> None:
    schema = {"anyOf": [{"type": "number"}, {"type": "string"}]}

    assert compact_schema(schema) == schema


def test_optional_aliases_are_dropped_but_required_fields_kept() -> None:
    schema = {
        "type": "object",
        "required": ["x1"],
        "properties": {
            "x1": {"type": "number", "description": "Start X alias"},
            "start_x": {"type": "number", "description": "Start point X alias"},
            "note": {
                "type": "string",
                "description": "A long description that happens to mention an "
                "alias somewhere in the middle of a real sentence",
            },
        },
    }

    compacted = compact_schema(schema)

    assert set(compacted["properties"]) == {"x1", "note"}


def test_nested_schemas_and_lists_are_compacted() -> None:
    schema = {
        "properties": {
            "input_data": {
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                    }
                }
            }
        },
        "examples": [{"anyOf": [{"type": "boolean"}, {"type": "null"}]}],
    }

    compacted = compact_schema(schema)

    inner = compacted["properties"]["input_data"]["properties"]["items"]
    assert inner["items"] == {"type": "integer"}
    assert compacted["examples"] == [{"type": "boolean"}]


def test_the_input_is_not_modified() -> None:
    schema = {"anyOf": [{"type": "number"}, {"type": "null"}], "default": None}

    compact_schema(schema)

    assert schema == {"anyOf": [{"type": "number"}, {"type": "null"}], "default": None}


class _LineInput(BaseModel):
    x1: float | None = Field(default=None, description="Start X in mm")
    start_x: float | None = Field(default=None, description="Start X alias")


@pytest.mark.asyncio
async def test_listing_is_compact_and_calls_still_accept_aliases_and_nulls() -> None:
    mcp = FastMCP("compaction")

    @mcp.tool()
    async def draw(input_data: _LineInput) -> dict:
        """Draw a line."""
        return {"x": input_data.x1 if input_data.x1 is not None else input_data.start_x}

    mcp.add_transform(CompactToolSchemas())

    (tool,) = await mcp.list_tools()
    listed = tool.parameters
    fields = (
        listed.get("$defs", {}).get("_LineInput", {}).get("properties")
        or (listed["properties"]["input_data"]["properties"])
    )
    assert fields == {"x1": {"type": "number", "description": "Start X in mm"}}

    by_alias = await mcp.call_tool("draw", {"input_data": {"start_x": 4}})
    with_null = await mcp.call_tool("draw", {"input_data": {"x1": None, "start_x": 5}})
    assert by_alias.structured_content == {"x": 4.0}
    assert with_null.structured_content == {"x": 5.0}


def test_a_nullable_union_keeps_its_real_options() -> None:
    schema = {
        "anyOf": [{"type": "string"}, {"type": "array"}, {"type": "null"}],
        "default": None,
    }

    assert compact_schema(schema) == {"anyOf": [{"type": "string"}, {"type": "array"}]}


def test_titles_and_open_objects_are_left_out_but_a_title_field_stays() -> None:
    schema = {
        "title": "Input",
        "type": "object",
        "additionalProperties": True,
        "properties": {"title": {"type": "string", "title": "Title"}},
    }

    assert compact_schema(schema) == {
        "type": "object",
        "properties": {"title": {"type": "string"}},
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Short.", "Short."),
        ("Depth  in\n   mm", "Depth in mm"),
        (
            "Take the edges this feature made, e.g. 'Cut-Extrude2'. Give one source.",
            "Take the edges this feature made, e.g. 'Cut-Extrude2'. ...",
        ),
        (
            "One. Two. " + "Three is a much longer sentence " * 4,
            "One. Two. ...",
        ),
        (
            "Which way the arc runs from start to end (x right, y up), counted "
            "around its centre, which the reply also gives in mm",
            "Which way the arc runs from start to end (x right, y up) ...",
        ),
        (
            "Take the edges of the furthest flat face facing this way: top, "
            "bottom, front, back, right, left or one of the six axis directions",
            "Take the edges of the furthest flat face facing this way: top, "
            "bottom, front, back, right, left or one of the six axis directions",
        ),
    ],
)
def test_long_descriptions_are_cut_cleanly(text: str, expected: str) -> None:
    assert shorten_description(text, 60) == expected


def test_a_boolean_property_schema_is_kept() -> None:
    schema = {"properties": {"anything": True}}

    assert compact_schema(schema) == schema


def test_describe_keeps_descriptions_whole() -> None:
    text = "One sentence. " * 20
    schema = {"properties": {"a": {"type": "string", "description": text}}}

    assert compact_schema(schema, limit=None)["properties"]["a"]["description"] == text


def test_a_tool_schema_drops_its_root_flag_and_keeps_more_of_input_data() -> None:
    args = (
        "The cut. `sketch_name` and `depth` (mm) are required; the rest default. " * 2
    )
    parameters = {
        "additionalProperties": False,
        "type": "object",
        "properties": {
            "input_data": {
                "type": "object",
                "additionalProperties": False,
                "description": args,
            }
        },
    }

    listed = compact_tool_schema(parameters)

    assert "additionalProperties" not in listed
    data = listed["properties"]["input_data"]
    assert data["additionalProperties"] is False
    assert data["description"] == shorten_description(args, INPUT_DATA_CHARS)
    assert len(data["description"]) > DESCRIPTION_CHARS
