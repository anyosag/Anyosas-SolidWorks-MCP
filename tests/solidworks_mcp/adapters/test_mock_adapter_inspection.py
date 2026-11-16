"""The mock reads features and sketches in the live reply's shape."""

from __future__ import annotations

import pytest

from solidworks_mcp.adapters.base import ExtrusionParameters, RevolveParameters
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


@pytest.fixture
async def part() -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter(
        {"mock_model_delay": 0, "mock_feature_delay": 0, "mock_sketch_delay": 0}
    )
    await adapter.connect()
    await adapter.create_part()
    return adapter


async def test_an_extrude_gives_what_the_mock_made(part) -> None:
    await part.create_sketch("Top")
    await part.exit_sketch()
    made = await part.create_extrusion(ExtrusionParameters(depth=12.0, draft_angle=2.0))

    result = await part.get_feature_definition(made.data.name)

    assert result.is_success
    assert result.data["type"] == "Extrusion"
    assert result.data["known_type"] is True
    assert result.data["parameters"]["direction1"] == {
        "depth_mm": 12.0,
        "end_condition": "Blind",
        "draft": True,
        "draft_angle_deg": 2.0,
    }
    assert result.data["suppressed"] is False


async def test_a_revolve_fillet_and_chamfer_give_their_sizes(part) -> None:
    await part.create_sketch("Front")
    await part.exit_sketch()
    revolve = await part.create_revolve(RevolveParameters(angle=90.0))
    fillet = await part.add_fillet(2.0, ["Edge1", "Edge2"])
    chamfer = await part.add_chamfer(1.0, ["Edge3"])

    revolved = await part.get_feature_definition(revolve.data.name)
    rounded = await part.get_feature_definition(fillet.data.name)
    cut = await part.get_feature_definition(chamfer.data.name)

    assert revolved.data["parameters"]["angle_deg"] == 90.0
    assert rounded.data["parameters"] == {"radius_mm": 2.0, "edge_count": 2}
    assert cut.data["parameters"] == {"distance_mm": 1.0, "edge_count": 1}


async def test_a_sketch_gives_its_plane(part) -> None:
    await part.create_sketch("Right")
    await part.exit_sketch()

    definition = await part.get_feature_definition("Sketch1")
    sketch = await part.get_sketch("Sketch1")

    assert definition.data["type"] == "ProfileFeature"
    assert definition.data["parameters"]["plane"] == "Right Plane"
    assert sketch.data["plane"] == "Right Plane"
    assert sketch.data["axes"]["normal"] == [1.0, 0.0, 0.0]
    assert sketch.data["segments"] == []


async def test_a_sketch_on_a_made_plane_has_no_known_axes(part) -> None:
    plane = await part.create_reference_plane("Top Plane", offset=5.0)
    await part.create_sketch(plane.data["name"])
    await part.exit_sketch()

    sketch = await part.get_sketch("Sketch1")

    assert sketch.data["plane"] == plane.data["name"]
    assert sketch.data["axes"] is None


async def test_other_features_give_their_type_and_dimensions(part) -> None:
    part._dimensions["D1@Sweep1"] = 3.0
    from solidworks_mcp.adapters.base import SolidWorksFeature

    part._features["s"] = SolidWorksFeature(name="Sweep1", type="Sweep")

    sweep = await part.get_feature_definition("Sweep1")
    plane = await part.get_feature_definition("Front Plane")

    assert sweep.data["known_type"] is False
    assert sweep.data["note"] == "A Sweep gives its dimensions only."
    assert sweep.data["dimensions"] == [
        {"name": "D1@Sweep1", "value": 3.0, "units": "mm"}
    ]
    assert plane.data["type"] == "RefPlane"


async def test_names_that_are_not_there_or_not_sketches_are_refused(part) -> None:
    missing = await part.get_feature_definition("Nope")
    not_sketch = await part.get_sketch("Front Plane")
    no_sketch = await part.get_sketch("Nope")

    assert missing.error == "Feature 'Nope' not found; list_features names them."
    assert not_sketch.error.startswith("'Front Plane' is a RefPlane, not a sketch")
    assert no_sketch.error == "Feature 'Nope' not found; list_features names them."


async def test_no_model_is_refused() -> None:
    adapter = MockSolidWorksAdapter({})

    assert (await adapter.get_feature_definition("X")).error == "No active model"
    assert (await adapter.get_sketch("X")).error == "No active model"
