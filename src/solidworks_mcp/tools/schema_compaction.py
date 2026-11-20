"""Smaller tool schemas in ``tools/list``, without changing what a call accepts.

Every tool definition the client lists goes into the agent's context, so its
size costs tokens on every turn that misses the prompt cache. These parts of
the schemas pydantic generates carry little an agent needs to pick a field:

* An optional field is written ``anyOf: [{type: X}, {type: "null"}]`` with
  ``default: null``. Advertising plain ``{type: X}`` says the same thing to an
  agent, which simply leaves the field out.
* Input aliases (``start_x`` beside ``x1``) are optional duplicates kept so old
  callers still work. They stay accepted; they just aren't advertised.
* ``title`` and ``additionalProperties: true`` repeat the field name and the
  JSON Schema default.
* Long field descriptions: the listing keeps the sentences that fit in
  :data:`DESCRIPTION_CHARS` (always the first). The whole text, with its
  limits and examples, comes from the ``describe_tool_inputs`` tool.

Only the *listed* schema changes. FastMCP validates a call against the tool
function's own pydantic types, so a client that still sends ``null`` or an
alias gets exactly the behaviour it had before.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from fastmcp.server.transforms import Transform
from fastmcp.tools.base import Tool

#: A short field description naming the field an alias ("Start point X alias").
_ALIAS_DESCRIPTION = re.compile(r"\balias\b", re.IGNORECASE)
_NULL = {"type": "null"}

#: The listed length a field description is cut to, at a sentence end, or
#: failing that at a clause end.
DESCRIPTION_CHARS = 80

#: The listed length of ``input_data``'s description: one per tool, and it
#: names the required fields, so it keeps more.
INPUT_DATA_CHARS = 120

#: What a cut description ends with, so an agent knows there is more.
CUT_MARK = " ..."

#: A sentence end: a full stop and a space before a capital, a quote, a
#: backtick or a bracket, not after "e.g.", "i.e.", "etc." or "vs.".
_SENTENCE_END = re.compile(
    r"(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\betc\.)(?<!\bvs\.)(?<=[.!?])\s+(?=[A-Z'\"`(\[])"
)
#: A clause end inside a long sentence.
_CLAUSE_END = re.compile(r"[,;:]\s")


def _is_alias(field_schema: Any) -> bool:
    """Whether a property's schema describes it as an input alias."""
    if not isinstance(field_schema, dict):
        return False
    description = str(field_schema.get("description") or "")
    return len(description) < 60 and bool(_ALIAS_DESCRIPTION.search(description))


def _depth(text: str) -> int:
    """How many brackets are open at the end of ``text``."""
    return sum(text.count(o) - text.count(c) for o, c in ("()", "[]", "{}"))


def shorten_description(text: str, limit: int = DESCRIPTION_CHARS) -> str:
    """The start of ``text`` that fits in ``limit`` characters, cut cleanly.

    Whole sentences are kept while they fit. A first sentence longer than
    ``limit`` is cut at its last clause end (a comma, semicolon or colon)
    that fits, or kept whole when it has none. A cut text ends with
    :data:`CUT_MARK`. Whitespace (docstring line breaks) is folded to single
    spaces.

    Args:
        text: A field description.
        limit: The most characters to keep.

    Returns:
        str: The shortened description.
    """
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    sentences = _SENTENCE_END.split(text)
    kept = sentences[0]
    if len(kept) > limit:
        ends = [m.start() for m in _CLAUSE_END.finditer(kept) if m.start() <= limit]
        # Never cut inside brackets, nor a list off its colon or short: a
        # comma after a colon is inside one ("facing: top, bottom, ...").
        colon = kept.find(":")
        ends = [
            e
            for e in ends
            if _depth(kept[:e]) == 0 and (kept[e] == ";" or colon < 0 or e < colon)
        ]
        if ends and ends[-1] >= limit // 3:
            kept = kept[: ends[-1]]
    else:
        for sentence in sentences[1:]:
            if len(kept) + 1 + len(sentence) > limit:
                break
            kept = f"{kept} {sentence}"
    return kept if kept == text else kept + CUT_MARK


def _drop_null(compacted: dict[str, Any]) -> dict[str, Any]:
    """Fold a nullable ``anyOf`` into its other option(s)."""
    options = compacted.get("anyOf")
    if not isinstance(options, list) or _NULL not in options:
        return compacted
    others = [option for option in options if option != _NULL]
    del compacted["anyOf"]
    if len(others) == 1:
        compacted = {**others[0], **compacted}
    elif others:
        compacted["anyOf"] = others
    if "default" in compacted and compacted["default"] is None:
        del compacted["default"]
    return compacted


def compact_schema(schema: Any, *, limit: int | None = DESCRIPTION_CHARS) -> Any:
    """Return ``schema`` slimmed for listing (see the module docstring).

    Args:
        schema: A JSON schema (or any part of one).
        limit: Cut descriptions to this length (see
            :func:`shorten_description`); None keeps them whole, as
            ``describe_tool_inputs`` does.

    Returns:
        Any: A new schema; the input is not modified.
    """
    if isinstance(schema, list):
        return [compact_schema(item, limit=limit) for item in schema]
    if not isinstance(schema, dict):
        return schema

    compacted: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            # Property names are data, not keywords: a field may be "title".
            compacted[key] = {
                name: compact_schema(field, limit=limit)
                for name, field in value.items()
            }
        elif key == "title" or (key == "additionalProperties" and value is True):
            continue
        elif key == "description" and isinstance(value, str):
            compacted[key] = (
                value if limit is None else shorten_description(value, limit)
            )
        else:
            compacted[key] = compact_schema(value, limit=limit)

    compacted = _drop_null(compacted)

    properties = compacted.get("properties")
    if isinstance(properties, dict):
        required = set(compacted.get("required") or ())
        compacted["properties"] = {
            name: field
            for name, field in properties.items()
            if name in required or not _is_alias(field)
        }
    return compacted


def compact_tool_schema(parameters: dict[str, Any]) -> dict[str, Any]:
    """A tool's listed input schema.

    :func:`compact_schema`, with the root's ``additionalProperties: false``
    left out (a tool's arguments are its listed properties; calls are still
    validated against the function), and ``input_data``'s own description,
    which names the required fields, cut to :data:`INPUT_DATA_CHARS`.

    Args:
        parameters: The tool's input schema.

    Returns:
        dict[str, Any]: The schema to list.
    """
    compacted: dict[str, Any] = compact_schema(parameters)
    if compacted.get("additionalProperties") is False:
        del compacted["additionalProperties"]
    original = (parameters.get("properties") or {}).get("input_data")
    listed = (compacted.get("properties") or {}).get("input_data")
    if isinstance(original, dict) and isinstance(listed, dict):
        text = original.get("description")
        if isinstance(text, str):
            listed["description"] = shorten_description(text, INPUT_DATA_CHARS)
    return compacted


class CompactToolSchemas(Transform):
    """List every tool with :func:`compact_tool_schema` applied to its schema."""

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        """Return copies of ``tools`` with compacted input schemas."""
        return [
            tool.model_copy(update={"parameters": compact_tool_schema(tool.parameters)})
            for tool in tools
        ]
