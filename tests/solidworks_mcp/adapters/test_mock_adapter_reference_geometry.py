"""The mock's sketch frames, plane axes, and axes and planes through any point."""

from __future__ import annotations

import pytest

from solidworks_mcp.adapters.base import ExtrusionParameters
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


@pytest.fixture
async def part() -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter(
        {"mock_model_delay": 0, "mock_sketch_delay": 0, "mock_feature_delay": 0}
    )
    await adapter.connect()
    await adapter.create_part()
    return adapter


async def test_the_open_sketch_has_its_planes_frame(part) -> None:
    assert "No sketch is open" in (await part.get_open_sketch_axes()).error
    await part.create_sketch("Top")

    frame = (await part.get_open_sketch_axes()).data

    assert frame["y"] == [0.0, 0.0, -1.0]
    assert frame["summary"].startswith("sketch x = model +X")


async def test_a_sketch_on_a_plane_of_unknown_geometry_has_no_frame(part) -> None:
    plane = await part.create_reference_plane("", face_point=[0.0, 0.0, 0.0])
    await part.create_sketch(plane.data["name"])

    result = await part.get_open_sketch_axes()

    assert result.error.startswith("The mock keeps no geometry for 'Plane1'")


async def test_plane_axes_of_default_offset_and_unknown_planes(part) -> None:
    offset = await part.create_reference_plane("Right Plane", offset=7.0, flip=True)
    angled = await part.create_reference_plane("Top Plane", angle=30.0, axis="Axis1")

    right = (await part.plane_axes("Right")).data
    moved = (await part.plane_axes(offset.data["name"])).data
    unknown = await part.plane_axes(angled.data["name"])

    assert right["plane"] == "Right Plane" and right["method"] == "plane_transform"
    assert moved["axes"]["origin_mm"] == [-7.0, 0.0, 0.0]
    assert unknown.error == "The mock keeps no geometry for 'Plane2'."


async def test_plane_axes_refuses_what_is_not_a_plane(part) -> None:
    await part.create_sketch("Front")
    await part.exit_sketch()

    assert "not found in the part" in (await part.plane_axes("Plane7")).error
    assert (await part.plane_axes("Sketch1")).error.startswith(
        "'Sketch1' is a ProfileFeature, not a plane"
    )
    part._current_model = None
    assert (await part.plane_axes("Top")).error == "No active model"


async def test_an_axis_through_two_points(part) -> None:
    result = await part.create_axis_through([20, 0, 10], [20, 5, 10])

    assert result.data["name"] == "Axis1"
    assert result.data["direction"] == [0.0, 1.0, 0.0]
    assert result.data["read_back"]["on_line"] is True
    assert result.data["features_after"] - result.data["features_before"] == 2
    assert "same point" in (await part.create_axis_through([1, 1, 1], [1, 1, 1])).error


async def test_a_plane_through_a_point(part) -> None:
    made = await part.create_plane_through([20, 0, 10], [0.5, 0, 0.866])
    name = made.data["name"]
    await part.create_sketch(name)

    frame = (await part.get_open_sketch_axes()).data

    assert made.data["normal_matches"] is True
    assert frame["origin_mm"] == [20.0, 0.0, 10.0]
    assert (await part.plane_axes(name)).data["axes"] == made.data["axes"]
    assert "no length" in (await part.create_plane_through([0, 0, 0], [0, 0, 0])).error


async def test_axes_and_planes_refuse_an_open_sketch(part) -> None:
    await part.create_sketch("Front")

    for result in (
        await part.create_axis_through([0, 0, 0], [0, 0, 1]),
        await part.create_plane_through([0, 0, 0], [0, 0, 1]),
    ):
        assert "exit_sketch first" in result.error


async def test_an_extrusion_echoes_its_regions(part) -> None:
    await part.create_sketch("Front")
    await part.add_circle(0, 0, 5)
    await part.exit_sketch()

    built = await part.create_extrusion(ExtrusionParameters(depth=2, regions="all"))

    assert built.data.parameters["regions"] == "all"


def test_a_nameless_plane_has_no_frame(part) -> None:
    assert part._mock_frame_of(None) is None
    assert part._mock_axes_of("") is None
