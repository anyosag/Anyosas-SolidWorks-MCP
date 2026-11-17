"""The mock's ``set_component_visibility`` and ``export_image`` views and hidden components."""

from __future__ import annotations

from pathlib import Path

import pytest

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.adapters.solidworks import view_matrix as vm


@pytest.fixture
async def assembly() -> MockSolidWorksAdapter:
    mock = MockSolidWorksAdapter({})
    await mock.connect()
    await mock.create_assembly()
    await mock.insert_component("base.sldprt")
    await mock.insert_component("cover.sldprt")
    return mock


async def test_components_are_hidden_and_shown(
    assembly: MockSolidWorksAdapter,
) -> None:
    hidden = await assembly.set_component_visibility(["component-2"], False)
    again = await assembly.set_component_visibility(["component-2"], False)
    shown = await assembly.set_component_visibility(["component-2"], True)

    assert hidden.data["components"] == [
        {"name": "component-2", "before": True, "after": False, "method": "Visible"}
    ]
    assert again.data["components"][0]["method"] is None
    assert shown.data["components"][0]["before"] is False


async def test_refusals(assembly: MockSolidWorksAdapter) -> None:
    empty = await assembly.set_component_visibility([], False)
    assert "Give the components" in empty.error
    missing = await assembly.set_component_visibility(["lid-1"], False)
    assert missing.error.startswith("Component(s) not found: lid-1")
    await assembly.create_part()
    part = await assembly.set_component_visibility(["component-1"], False)
    assert "needs an assembly" in part.error


async def test_a_picture_with_components_hidden_and_a_custom_view(
    assembly: MockSolidWorksAdapter, tmp_path: Path
) -> None:
    await assembly.set_component_visibility(["component-1"], False)

    result = await assembly.export_image(
        {
            "file_path": str(tmp_path / "p.png"),
            "view_direction": [1, -1, 1],
            "up_axis": "z",
            "hide_components": ["component-1", "component-2"],
        }
    )

    assert result.is_success, result.error
    assert result.data["view"] == "custom"
    assert result.data["zoomed_to_fit"] is True
    assert result.data["view_axes"] == vm.axes(vm.look_from([1, -1, 1], [0, 0, 1]))
    assert result.data["hidden_components"] == [
        {"name": "component-1", "before": False, "restored": True},
        {"name": "component-2", "before": True, "restored": True},
    ]


@pytest.mark.parametrize(
    ("orientation", "has_axes"), [("front", True), ("dimetric", False)]
)
async def test_a_z_up_standard_view(
    assembly: MockSolidWorksAdapter, tmp_path: Path, orientation: str, has_axes: bool
) -> None:
    result = await assembly.export_image(
        {
            "file_path": str(tmp_path / "p.png"),
            "view_orientation": orientation,
            "up_axis": "z",
        }
    )

    assert result.is_success, result.error
    assert (result.data["view_axes"] is not None) is has_axes
    if has_axes:
        assert result.data["view_axes"]["up"] == pytest.approx([0, 0, 1])


async def test_pictures_that_cant_be_taken(
    assembly: MockSolidWorksAdapter, tmp_path: Path
) -> None:
    path = str(tmp_path / "p.png")
    bad_view = await assembly.export_image(
        {"file_path": path, "view_orientation": "current", "up_axis": "z"}
    )
    missing = await assembly.export_image({"file_path": path, "hide_components": ["x"]})

    assert "'current' isn't one" in bad_view.error
    assert missing.error.startswith("Component(s) not found: x")
