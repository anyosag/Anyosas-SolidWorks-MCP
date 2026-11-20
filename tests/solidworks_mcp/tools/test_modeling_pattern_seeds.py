"""The pattern and mirror tools say what they left out: a listed pattern's own seeds.

A pattern listed beside the features it repeats made SolidWorks refuse the
whole call (Aggrewell lid). The adapters leave those features out and list
them in ``left_out``; the tools name them in a ``warning`` and their message.
"""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.base import ExtrusionParameters
from solidworks_mcp.tools.modeling import _left_out_warning, register_modeling_tools


@pytest.fixture
async def tools(mcp_server, mock_adapter) -> dict[str, Any]:
    await register_modeling_tools(mcp_server, mock_adapter, {})
    return {t.name: t.fn for t in await mcp_server.list_tools()}


@pytest.fixture
async def vent(mock_adapter) -> dict[str, str]:
    """A vent (a boss and its chamfer) and a ring of it, made in the mock."""
    await mock_adapter.create_part()
    hole = (await mock_adapter.create_extrusion(ExtrusionParameters(depth=2.0))).data
    chamfer = (await mock_adapter.add_chamfer(0.5, ["Edge1"])).data
    axis = (await mock_adapter.create_axis("y")).data["name"]
    ring = await mock_adapter.pattern_circular([hole.name, chamfer.name], axis, 6)
    return {"hole": hole.name, "chamfer": chamfer.name, "ring": ring.data["name"]}


def _warned(vent: dict[str, str]) -> str:
    return (
        f"Left out {vent['hole']}, {vent['chamfer']} ({vent['ring']} repeats them "
        "already): a pattern or mirror carries its own seeds, and SolidWorks "
        "refuses them listed beside it."
    )


async def test_pattern_linear_warns_of_the_seeds_it_left_out(tools, vent) -> None:
    reply = await tools["pattern_linear"](
        {
            "features": [vent["hole"], vent["chamfer"], vent["ring"]],
            "direction": "x",
            "count": 2,
            "spacing": 35.7,
        }
    )

    assert reply["status"] == "success", reply
    assert reply["message"].startswith(f"Patterned {vent['ring']}: 2 instances")
    assert reply["warning"].startswith(_warned(vent))
    assert len(reply["pattern"]["left_out"]) == 2


async def test_mirror_feature_warns_of_the_seeds_it_left_out(tools, vent) -> None:
    reply = await tools["mirror_feature"](
        {
            "features": [vent["hole"], vent["chamfer"], vent["ring"]],
            "mirror_plane": "Right Plane",
            "mirror_bodies": False,
        }
    )

    assert reply["message"] == f"Mirrored {vent['ring']} about Right Plane"
    assert reply["warning"].startswith(_warned(vent))


async def test_pattern_circular_warns_of_the_seeds_it_left_out(
    tools, vent, mock_adapter
) -> None:
    axis = (await mock_adapter.create_axis("z")).data["name"]

    reply = await tools["pattern_circular"](
        {
            "features": [vent["hole"], vent["chamfer"], vent["ring"]],
            "axis": axis,
            "count": 3,
        }
    )

    assert reply["message"].startswith(f"Patterned {vent['ring']} around {axis}")
    assert reply["warning"].startswith(_warned(vent))


async def test_nothing_left_out_means_no_warning(tools, vent) -> None:
    reply = await tools["mirror_feature"](
        {
            "features": [vent["ring"]],
            "mirror_plane": "Right Plane",
            "mirror_bodies": False,
        }
    )

    assert "warning" not in reply
    assert "left_out" not in reply["mirror"]


def test_one_feature_left_out_by_each_of_two_patterns() -> None:
    data = {
        "left_out": [
            {"feature": "Hole1", "patterned_by": "Ring1"},
            {"feature": "Hole2", "patterned_by": "Ring2"},
        ]
    }

    assert _left_out_warning(data) == (
        "Left out Hole1 (Ring1 repeats it already); Hole2 (Ring2 repeats it "
        "already): a pattern or mirror carries its own seeds, and SolidWorks "
        "refuses them listed beside it."
    )
    assert _left_out_warning({}) == ""
