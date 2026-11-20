"""Every tool has one group; a selection of groups is read and checked."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP

from solidworks_mcp.tools import register_tools
from solidworks_mcp.tools.tool_boundary import tool_family
from solidworks_mcp.tools.tool_groups import (
    CORE,
    CORE_TOOLS,
    FAMILY_GROUPS,
    GROUPS,
    TOOL_GROUPS,
    group_for,
    group_tag,
    hidden_groups,
    parse_groups,
)


async def _tools(adapter: Any) -> list[Any]:
    mcp = FastMCP("groups")
    await register_tools(mcp, adapter, {})
    return list(await mcp.list_tools())


@pytest.mark.asyncio
async def test_every_tool_has_exactly_one_group(mock_adapter) -> None:
    wrong = {
        t.name: tags
        for t in await _tools(mock_adapter)
        if len(tags := {tag for tag in t.tags if tag.startswith("group:")}) != 1
    }

    assert wrong == {}


@pytest.mark.asyncio
async def test_every_family_has_a_group(mock_adapter) -> None:
    families = {tool_family(t.fn) for t in await _tools(mock_adapter)}

    assert families <= set(FAMILY_GROUPS)


@pytest.mark.asyncio
async def test_the_named_tools_exist(mock_adapter) -> None:
    names = {t.name for t in await _tools(mock_adapter)}

    assert CORE_TOOLS - names == set()
    assert set(TOOL_GROUPS) - names == set()


def test_every_group_used_is_described() -> None:
    used = {*FAMILY_GROUPS.values(), *TOOL_GROUPS.values(), CORE}

    assert used == set(GROUPS)


def test_core_and_overrides_win_over_the_family() -> None:
    assert group_for("create_extrusion", "modeling") == CORE
    assert group_for("add_mate", "modeling") == "assembly"
    assert group_for("create_loft", "modeling") == "features"
    assert group_for("any_tool", "a_test_module") == CORE


def test_group_tag() -> None:
    assert group_tag("bodies") == "group:bodies"


@pytest.mark.parametrize(
    ("value", "selected", "unknown"),
    [
        (None, None, []),
        ("all", None, []),
        ("", None, []),
        ("core,bodies", {"core", "bodies"}, []),
        (" Bodies , drawings ", {"core", "bodies", "drawings"}, []),
        ("bodies,widgets", {"core", "bodies"}, ["widgets"]),
        (["assembly"], {"core", "assembly"}, []),
        ("bodies,all", None, []),
    ],
)
def test_a_selection_always_includes_core(
    value: Any, selected: set[str] | None, unknown: list[str]
) -> None:
    assert parse_groups(value) == (selected, unknown)


def test_the_hidden_groups_are_the_rest() -> None:
    assert hidden_groups(None) == set()
    assert hidden_groups(set(GROUPS)) == set()
    assert hidden_groups({"core", "bodies"}) == set(GROUPS) - {"core", "bodies"}
