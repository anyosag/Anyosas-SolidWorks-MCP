"""Measure what the SolidWorks MCP server costs an agent's context.

Prints the size of the tool list a client receives (per tool, per family,
per tool group and in total, with an estimated token count), the size of the
server instructions, and the reply sizes of a few typical calls, all on the
mock adapter: no SolidWorks needed.

Sizes are characters of the JSON the client receives (``json.dumps`` with
default separators). Tokens are estimated at four characters each.

Usage::

    python src/utils/measure_tool_cost.py            # summary
    python src/utils/measure_tool_cost.py --top 20   # also the 20 largest tools
    python src/utils/measure_tool_cost.py --json     # machine-readable
    python src/utils/measure_tool_cost.py --groups core,bodies
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

#: Characters per token, for the estimate.
CHARS_PER_TOKEN = 4

#: Typical calls on the mock adapter, in order: a small part build, then reads.
TYPICAL_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("get_model_info", {}),
    ("create_part", {"input_data": {"name": "Measure"}}),
    ("create_sketch", {"input_data": {"plane": "Front"}}),
    (
        "add_rectangle",
        {"input_data": {"x1": -10, "y1": -10, "x2": 10, "y2": 10}},
    ),
    ("exit_sketch", {}),
    (
        "create_extrusion",
        {"input_data": {"sketch_name": "Sketch1", "depth": 10}},
    ),
    ("list_features", {"input_data": {}}),
    ("get_sketch", {"input_data": {"name": "Sketch1"}}),
    ("query_geometry", {"input_data": {"point_mm": [0, 0, 10], "radius_mm": 50}}),
    ("select_edges", {"input_data": {"feature": "Boss-Extrude1"}}),
    ("list_components", {}),
    ("verify_model", {"input_data": {"volume_mm3": 4000}}),
]


@dataclass
class ToolCost:
    """The listed size of one tool.

    Attributes:
        name: The tool's name.
        family: Its module (family tag).
        group: Its tool group.
        description: Characters of its description.
        schema: Characters of its input schema as JSON.
    """

    name: str
    family: str
    group: str
    description: int
    schema: int

    @property
    def total(self) -> int:
        """Description plus schema."""
        return self.description + self.schema


@dataclass
class CostReport:
    """Everything the script measures.

    Attributes:
        tools: One entry per listed tool.
        instructions: Characters of the server instructions.
        replies: Characters of each typical call's reply, by tool name.
    """

    tools: list[ToolCost]
    instructions: int
    replies: dict[str, int] = field(default_factory=dict)

    @property
    def descriptions(self) -> int:
        """All descriptions together."""
        return sum(t.description for t in self.tools)

    @property
    def schemas(self) -> int:
        """All input schemas together."""
        return sum(t.schema for t in self.tools)

    @property
    def total(self) -> int:
        """The whole tool list."""
        return self.descriptions + self.schemas

    def by(self, key: str) -> dict[str, int]:
        """Total characters per ``family`` or ``group``."""
        sizes: dict[str, int] = defaultdict(int)
        for tool in self.tools:
            sizes[getattr(tool, key)] += tool.total
        return dict(sorted(sizes.items(), key=lambda item: -item[1]))


def tokens(chars: int) -> int:
    """Estimated tokens for ``chars`` characters."""
    return round(chars / CHARS_PER_TOKEN)


def family_and_group(tags: set[str]) -> tuple[str, str]:
    """A tool's family and group, read from its tags.

    Args:
        tags: The tool's tags.

    Returns:
        tuple[str, str]: The family and group, ``"unknown"`` when untagged.
    """
    from solidworks_mcp.tools.tool_groups import FAMILIES

    families = sorted(tags & FAMILIES)
    groups = sorted(t.removeprefix("group:") for t in tags if t.startswith("group:"))
    return (
        families[0] if families else "unknown",
        groups[0] if groups else "unknown",
    )


async def build_server(groups: str | None = None) -> Any:
    """A set-up server on the mock adapter, listing ``groups`` (default all).

    Args:
        groups: A ``SOLIDWORKS_MCP_TOOL_GROUPS`` value; None keeps the
            environment's.

    Returns:
        SolidWorksMCPServer: The server, set up.
    """
    from solidworks_mcp.config import (
        AdapterType,
        DeploymentMode,
        SecurityLevel,
        SolidWorksMCPConfig,
    )
    from solidworks_mcp.server import SolidWorksMCPServer

    settings: dict[str, Any] = {}
    if groups is not None:
        settings["tool_groups"] = groups
    config = SolidWorksMCPConfig(
        adapter_type=AdapterType.MOCK,
        deployment_mode=DeploymentMode.LOCAL,
        security_level=SecurityLevel.MINIMAL,
        testing=True,
        **settings,
    )
    server = SolidWorksMCPServer(config)
    await server.setup()
    return server


async def measure(groups: str | None = None, *, replies: bool = True) -> CostReport:
    """Measure the tool list, the instructions and typical replies.

    Args:
        groups: The tool groups to list; None for the configured default.
        replies: Also run :data:`TYPICAL_CALLS` and measure their replies.

    Returns:
        CostReport: The sizes.
    """
    from fastmcp import Client

    from solidworks_mcp.skills import SERVER_INSTRUCTIONS

    server = await build_server(groups)
    tags = {t.name: set(t.tags or ()) for t in await server.mcp.list_tools()}
    async with Client(server.mcp) as client:
        wire = await client.list_tools()
        costs = [
            ToolCost(
                tool.name,
                *family_and_group(tags.get(tool.name, set())),
                description=len(tool.description or ""),
                schema=len(json.dumps(tool.inputSchema)),
            )
            for tool in wire
        ]
        sizes: dict[str, int] = {}
        if replies:
            await server.adapter.connect()
            names = {tool.name for tool in wire}
            for name, arguments in TYPICAL_CALLS:
                if name not in names:
                    continue
                result = await client.call_tool(name, arguments, raise_on_error=False)
                payload = result.structured_content or {
                    "content": [getattr(c, "text", "") for c in result.content]
                }
                sizes[name] = len(json.dumps(payload, default=str))
    return CostReport(costs, len(SERVER_INSTRUCTIONS), sizes)


def render(report: CostReport, top: int = 0) -> str:
    """The report as plain text."""
    lines = [
        f"tools listed: {len(report.tools)}",
        f"descriptions: {report.descriptions:>9,} chars  ~{tokens(report.descriptions):>7,} tokens",
        f"schemas:      {report.schemas:>9,} chars  ~{tokens(report.schemas):>7,} tokens",
        f"total:        {report.total:>9,} chars  ~{tokens(report.total):>7,} tokens",
        f"instructions: {report.instructions:>9,} chars  ~{tokens(report.instructions):>7,} tokens",
        "",
        "by group:",
    ]
    lines += [
        f"  {name:<14} {size:>9,} chars  ~{tokens(size):>7,} tokens"
        for name, size in report.by("group").items()
    ]
    lines += ["", "by family:"]
    lines += [
        f"  {name:<20} {size:>9,} chars" for name, size in report.by("family").items()
    ]
    if top:
        lines += ["", f"largest {top} tools (description + schema):"]
        for tool in sorted(report.tools, key=lambda t: -t.total)[:top]:
            lines.append(
                f"  {tool.name:<30} {tool.description:>6,} + {tool.schema:>6,}"
            )
    if report.replies:
        lines += ["", "typical replies (mock):"]
        lines += [
            f"  {name:<20} {size:>7,} chars" for name, size in report.replies.items()
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Print the measurement."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--top", type=int, default=0, help="also list N largest")
    parser.add_argument("--json", action="store_true", help="print JSON")
    parser.add_argument("--groups", default=None, help="tool groups to list")
    parser.add_argument(
        "--no-replies", action="store_true", help="skip the typical calls"
    )
    args = parser.parse_args(argv)
    os.environ.setdefault("SOLIDWORKS_MCP_COM_SETTLE_SECONDS", "0")
    os.environ.setdefault("SOLIDWORKS_MCP_DOC_SETTLE_SECONDS", "0")
    from loguru import logger

    logger.remove()
    report = asyncio.run(measure(args.groups, replies=not args.no_replies))
    if args.json:
        data = {
            "descriptions": report.descriptions,
            "schemas": report.schemas,
            "total": report.total,
            "tokens": tokens(report.total),
            "instructions": report.instructions,
            "groups": report.by("group"),
            "families": report.by("family"),
            "replies": report.replies,
            "tools": [asdict(t) for t in report.tools],
        }
        print(json.dumps(data, indent=2))
    else:
        print(render(report, args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
