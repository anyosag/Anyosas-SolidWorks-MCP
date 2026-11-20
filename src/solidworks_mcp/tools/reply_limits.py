"""Long lists in replies, cut to a page so a reply doesn't fill the agent's context.

A tool that returns a list that can run to hundreds of entries (a sketch's
segments, a part's edges, a feature tree, an assembly's components) returns
:data:`DEFAULT_LIMIT` of them by default. A cut reply says so: ``truncated:
true``, ``full_count`` (how many there are) and ``next_offset`` (the
``offset`` that asks for the next page). ``limit`` and ``offset`` in the
tool's input page through the rest.

Check blocks (``measured_change``, ``warning``, ``mesh_check``,
``file_check``) are never paged.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

#: How many entries a paged list returns unless asked for more.
DEFAULT_LIMIT = 50

#: The most entries one page may hold.
MAX_LIMIT = 1000


def limit_field() -> Any:
    """The ``limit`` input field of a paged tool."""
    return Field(
        default=DEFAULT_LIMIT,
        ge=1,
        le=MAX_LIMIT,
        description=f"Most entries to return (default {DEFAULT_LIMIT})",
    )


def offset_field() -> Any:
    """The ``offset`` input field of a paged tool."""
    return Field(
        default=0,
        ge=0,
        description="Entries to skip first, e.g. a reply's next_offset",
    )


def page(
    items: list[Any], limit: int = DEFAULT_LIMIT, offset: int = 0
) -> tuple[list[Any], dict[str, Any]]:
    """One page of ``items``, and what a cut page adds to the reply.

    Args:
        items: The whole list.
        limit: The most entries to return.
        offset: How many entries to skip first.

    Returns:
        tuple: The page, and ``{"truncated": True, "full_count": n,
        "next_offset": k}`` when entries were left out after it (with
        ``next_offset`` left out when the page reaches the end), or
        ``{"offset": k, "full_count": n}`` for a later page that does;
        an empty dict when the page is the whole list.
    """
    total = len(items)
    chunk = items[offset : offset + limit]
    end = offset + len(chunk)
    if offset == 0 and end >= total:
        return chunk, {}
    notes: dict[str, Any] = {"full_count": total}
    if offset:
        notes["offset"] = offset
    if end < total:
        notes = {"truncated": True, **notes, "next_offset": end}
    return chunk, notes
