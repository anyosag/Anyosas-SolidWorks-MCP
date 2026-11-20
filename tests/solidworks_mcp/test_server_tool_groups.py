"""``SOLIDWORKS_MCP_TOOL_GROUPS``: the server lists only the groups it is given."""

from __future__ import annotations

import pytest
from fastmcp import Client

from solidworks_mcp.config import (
    AdapterType,
    DeploymentMode,
    SecurityLevel,
    SolidWorksMCPConfig,
)
from solidworks_mcp.server import SolidWorksMCPServer


async def _names(groups: str) -> set[str]:
    config = SolidWorksMCPConfig(
        adapter_type=AdapterType.MOCK,
        deployment_mode=DeploymentMode.LOCAL,
        security_level=SecurityLevel.MINIMAL,
        testing=True,
        tool_groups=groups,
    )
    server = SolidWorksMCPServer(config)
    await server.setup()
    async with Client(server.mcp) as client:
        return {t.name for t in await client.list_tools()}


@pytest.mark.asyncio
async def test_all_groups_by_default() -> None:
    names = await _names("all")

    assert {"create_part", "combine_bodies", "create_loft", "add_mate"} <= names


@pytest.mark.asyncio
async def test_only_the_chosen_groups_and_core() -> None:
    names = await _names("bodies, widgets")

    assert {"create_part", "enable_tool_groups", "combine_bodies"} <= names
    assert "create_loft" not in names
    assert "add_mate" not in names


def test_the_setting_is_read_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("SOLIDWORKS_MCP_TOOL_GROUPS", "core,drawings")

    assert SolidWorksMCPConfig.from_env().tool_groups == "core,drawings"
