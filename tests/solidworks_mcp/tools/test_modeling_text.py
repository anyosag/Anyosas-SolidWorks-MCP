"""``emboss_text``: a label's sketch, its text and its boss or cut in one call.

From the Dipper build, which could add no labels at all. An emboss that adds
no volume grew into the part, so it is made again the other way; an engrave
leaves the side to ``create_cut_extrude``'s choice.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from solidworks_mcp.adapters.base import (
    AdapterResult,
    AdapterResultStatus,
    SolidWorksFeature,
)
from solidworks_mcp.tools.modeling import (
    EmbossTextInput,
    _added_nothing,
    register_modeling_tools,
    sketch_point,
)


def _ok(data: Any = None, **kwargs: Any) -> AdapterResult[Any]:
    return AdapterResult(status=AdapterResultStatus.SUCCESS, data=data, **kwargs)


def _volume(mm3: float | None) -> AdapterResult[Any]:
    return _ok({"volume_mm3": mm3, "bounding_box_mm": None, "solid_body_count": 1})


def _feature(name: str, **parameters: Any) -> AdapterResult[Any]:
    return _ok(SolidWorksFeature(name=name, type="Extrusion", parameters=parameters))


async def _tool(mcp_server, adapter) -> Any:
    await register_modeling_tools(mcp_server, adapter, {})
    return {t.name: t.fn for t in await mcp_server.list_tools()}["emboss_text"]


def _scripted(adapter: Any, axes: Any = None) -> None:
    """Sketch steps that succeed, the sketch named Sketch3."""
    adapter.create_sketch = AsyncMock(
        return_value=_ok("Sketch3", metadata={"plane": "Plane1", "axes": axes})
    )
    adapter.add_sketch_text = AsyncMock(
        return_value=_ok({"id": "Text_1", "text": "MAX", "shares_sketch_with": []})
    )
    adapter.exit_sketch = AsyncMock(return_value=_ok())


async def test_an_emboss_on_a_plane_runs_through_the_mock(
    mcp_server, mock_adapter
) -> None:
    await mock_adapter.create_part()
    tool = await _tool(mcp_server, mock_adapter)

    reply = await tool(
        {"text": "MAX", "plane": "Top", "depth": 0.6, "height": 4, "x": 2, "y": 3}
    )

    assert reply["status"] == "success", reply
    assert reply["message"].startswith("Embossed 'MAX' 0.6 mm on Top Plane: ")
    assert reply["sketch"]["plane"] == "Top Plane"
    assert reply["sketch"]["plane_created"] is None
    assert reply["sketch"]["axes"]["summary"].startswith("sketch x = model +X")
    assert reply["text"]["position_mm"] == [2.0, 3.0]
    assert reply["feature"]["mode"] == "emboss"
    assert reply["feature"]["direction_flipped"] is False
    assert reply["feature"]["name"].startswith("Boss-Extrude")


async def test_an_emboss_that_added_nothing_is_made_the_other_way(
    mcp_server, mock_adapter
) -> None:
    _scripted(mock_adapter)
    mock_adapter.create_extrusion = AsyncMock(
        side_effect=[_feature("Boss-Extrude4"), _feature("Boss-Extrude5")]
    )
    mock_adapter.delete_feature = AsyncMock(return_value=_ok({}))
    mock_adapter.get_model_geometry_summary = AsyncMock(
        side_effect=[_volume(1000.0), _volume(1000.0), _volume(1012.0)]
    )
    tool = await _tool(mcp_server, mock_adapter)

    reply = await tool({"text": "MAX", "plane": "Plane1", "depth": 0.5})

    assert reply["status"] == "success", reply
    mock_adapter.delete_feature.assert_awaited_once_with("Boss-Extrude4")
    first, second = (c.args[0] for c in mock_adapter.create_extrusion.await_args_list)
    assert "reverse_direction" not in first.model_fields_set
    assert second.reverse_direction is True
    assert second.sketch_name == "Sketch3"
    assert reply["feature"] == {
        "name": "Boss-Extrude5",
        "mode": "emboss",
        "depth": 0.5,
        "direction_flipped": True,
    }
    assert reply["measured_change"]["volume_delta_mm3"] == 12.0


async def test_an_emboss_that_cannot_be_deleted_stays_and_warns(
    mcp_server, mock_adapter
) -> None:
    _scripted(mock_adapter)
    mock_adapter.create_extrusion = AsyncMock(return_value=_feature("Boss-Extrude4"))
    mock_adapter.delete_feature = AsyncMock(
        return_value=AdapterResult(status=AdapterResultStatus.ERROR, error="no")
    )
    mock_adapter.get_model_geometry_summary = AsyncMock(
        side_effect=[_volume(1000.0), _volume(1000.0)]
    )
    tool = await _tool(mcp_server, mock_adapter)

    reply = await tool({"text": "MAX", "plane": "Plane1", "depth": 0.5})

    assert reply["feature"]["direction_flipped"] is False
    assert "changed no volume" in reply["warning"]
    assert mock_adapter.create_extrusion.await_count == 1


async def test_a_side_chosen_for_an_emboss_is_kept(mcp_server, mock_adapter) -> None:
    _scripted(mock_adapter)
    mock_adapter.create_extrusion = AsyncMock(return_value=_feature("Boss-Extrude4"))
    mock_adapter.delete_feature = AsyncMock()
    mock_adapter.get_model_geometry_summary = AsyncMock(
        side_effect=[_volume(1000.0), _volume(1000.0)]
    )
    tool = await _tool(mcp_server, mock_adapter)

    await tool(
        {"text": "MAX", "plane": "Plane1", "depth": 0.5, "reverse_direction": False}
    )

    params = mock_adapter.create_extrusion.await_args.args[0]
    assert "reverse_direction" in params.model_fields_set
    mock_adapter.delete_feature.assert_not_awaited()


async def test_an_engrave_is_a_cut_that_picks_its_side(
    mcp_server, mock_adapter
) -> None:
    _scripted(mock_adapter)
    mock_adapter.create_cut_extrude = AsyncMock(
        return_value=_feature("Cut-Extrude2", direction_flipped=True)
    )
    mock_adapter.get_model_geometry_summary = AsyncMock(
        side_effect=[_volume(1000.0), _volume(990.0)]
    )
    tool = await _tool(mcp_server, mock_adapter)

    reply = await tool(
        {"text": "MIN", "plane": "Plane1", "depth": 0.4, "mode": "engrave"}
    )

    params = mock_adapter.create_cut_extrude.await_args.args[0]
    assert params.merge_result is False
    assert params.depth == 0.4
    assert "reverse_direction" not in params.model_fields_set
    assert reply["message"].startswith("Engraved 'MIN' 0.4 mm")
    assert reply["feature"]["direction_flipped"] is True
    assert reply["measured_change"]["volume_delta_mm3"] == -10.0


async def test_on_a_face_a_plane_is_made_and_x_y_count_from_the_point(
    mcp_server, mock_adapter
) -> None:
    axes = {
        "x": [1, 0, 0],
        "y": [0, 0, -1],
        "normal": [0, 1, 0],
        "origin_mm": [0, 10, 0],
    }
    _scripted(mock_adapter, axes)
    mock_adapter.create_reference_plane = AsyncMock(
        return_value=_ok({"name": "Plane1"})
    )
    mock_adapter.create_extrusion = AsyncMock(return_value=_feature("Boss-Extrude2"))
    tool = await _tool(mcp_server, mock_adapter)

    reply = await tool(
        {"text": "MAX", "face_point": [20, 10, -5], "x": 1, "y": -2, "depth": 1}
    )

    mock_adapter.create_reference_plane.assert_awaited_once_with(
        "", 0.0, 0.0, False, None, [20.0, 10.0, -5.0]
    )
    mock_adapter.create_sketch.assert_awaited_once_with("Plane1")
    args = mock_adapter.add_sketch_text.await_args.args
    assert args[1:3] == (21.0, 3.0)  # (20, 5) on the sketch, plus (1, -2)
    assert reply["sketch"]["plane_created"] == "Plane1"
    assert reply["sketch"]["axes"] == axes


async def test_on_a_face_without_axes_x_y_count_from_the_sketch_origin(
    mcp_server, mock_adapter
) -> None:
    _scripted(mock_adapter, axes=None)
    mock_adapter.create_reference_plane = AsyncMock(
        return_value=_ok({"name": "Plane2"})
    )
    mock_adapter.create_extrusion = AsyncMock(return_value=_feature("Boss-Extrude2"))
    tool = await _tool(mcp_server, mock_adapter)

    reply = await tool(
        {"text": "MAX", "face_point": [20, 10, -5], "x": 1, "y": 2, "depth": 1}
    )

    assert mock_adapter.add_sketch_text.await_args.args[1:3] == (1.0, 2.0)
    assert "axes couldn't be read" in reply["warning"]


async def test_a_text_that_fails_closes_its_sketch(mcp_server, mock_adapter) -> None:
    _scripted(mock_adapter)
    mock_adapter.add_sketch_text = AsyncMock(
        return_value=AdapterResult(status=AdapterResultStatus.ERROR, error="boom")
    )
    mock_adapter.create_extrusion = AsyncMock()
    tool = await _tool(mcp_server, mock_adapter)

    reply = await tool({"text": "MAX", "plane": "Top", "depth": 1})

    assert reply["status"] == "error"
    assert "Failed to add the text: boom" in reply["message"]
    mock_adapter.exit_sketch.assert_awaited_once()
    mock_adapter.create_extrusion.assert_not_awaited()


@pytest.mark.parametrize(
    "fields",
    [
        {"text": "A", "depth": 1},
        {"text": "A", "depth": 1, "plane": "Top", "face_point": [0, 0, 0]},
        {"text": "A", "depth": 1, "plane": " "},
        {"text": "A", "depth": 0, "plane": "Top"},
        {"text": " ", "depth": 1, "plane": "Top"},
    ],
)
def test_a_label_with_nowhere_or_nothing_to_write_is_refused(
    fields: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        EmbossTextInput(**fields)


# ---- the helpers ----------------------------------------------------------------


def test_a_model_point_in_sketch_coordinates() -> None:
    axes = {"x": [0, 0, -1], "y": [0, 1, 0], "origin_mm": [5, 0, 0]}

    assert sketch_point(axes, [5, 3, -4]) == (4.0, 3.0)


@pytest.mark.parametrize(
    "axes", [None, {"x": [1, 0], "y": [0, 1, 0], "origin_mm": [0, 0, 0]}, {"x": "a"}]
)
def test_no_sketch_point_without_axes(axes: Any) -> None:
    assert sketch_point(axes, [1, 2, 3]) is None


def test_no_sketch_point_from_axes_that_are_not_numbers() -> None:
    axes = {"x": [1, 0, "z"], "y": [0, 1, 0], "origin_mm": [0, 0, 0]}

    assert sketch_point(axes, [1, 2, 3]) is None


@pytest.mark.parametrize(
    ("before", "after", "nothing"),
    [
        (_volume(1000.0), _volume(1000.0), True),
        (_volume(1000.0), _volume(1000.5), False),
        (_volume(None), _volume(1000.0), False),
        (None, _volume(1000.0), False),
        (_volume(1000.0), AdapterResult(status=AdapterResultStatus.ERROR), False),
    ],
)
def test_a_boss_added_nothing_only_when_both_volumes_say_so(
    before: Any, after: Any, nothing: bool
) -> None:
    assert _added_nothing(before, after) is nothing
