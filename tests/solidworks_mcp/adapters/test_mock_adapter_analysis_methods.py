"""The mock's turn sweep, plane volume and density: the real arithmetic, on boxes."""

from __future__ import annotations

import pytest

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter

_ARM = [40.0, -5.0, -5.0, 50.0, 5.0, 5.0]
_WALL = [-100.0, 20.0, -10.0, 100.0, 30.0, 10.0]


async def _part(**bodies: list[float]) -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    if bodies:
        adapter.part_bodies_mm = dict(bodies)
    await adapter.create_part()
    return adapter


async def _assembly() -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_assembly()
    await adapter.insert_component("C:/parts/lid.sldprt", 40, -5, -5)
    await adapter.insert_component("C:/parts/base.sldprt")
    adapter.component_boxes_mm["component-2"] = _WALL
    return adapter


# ---- rotation_clearance ----------------------------------------------------------------


async def test_a_body_swings_into_a_wall() -> None:
    adapter = await _part(Arm=_ARM, Wall=_WALL)

    result = await adapter.rotation_clearance("Arm", "Wall", [0, 0, 1], None, [0, 90])

    assert result.is_success, result.error
    data = result.data
    assert data["kind"] == "bodies" and data["moving"] == "Arm"
    assert 14.0 < data["positive"]["first_contact_deg"] < 18.0
    assert "body_frame" not in data


async def test_a_component_swings_from_where_it_is_placed() -> None:
    adapter = await _assembly()

    result = await adapter.rotation_clearance(
        "component-1", "component-2", [0, 0, 1], [0, 0, 0], [0, 90]
    )

    assert result.is_success, result.error
    assert result.data["kind"] == "components"
    assert result.data["start_clearance_mm"] == pytest.approx(15.0)
    assert result.data["restored"] is True


@pytest.mark.parametrize(
    ("moving", "other", "span", "fragment"),
    [
        ("Arm", "Arm", None, "two different ones"),
        ("Arm", "Wall", [10, 90], "at most 0"),
        ("Arm", "Ghost", None, "Body(ies) not found: Ghost"),
    ],
)
async def test_a_part_sweep_that_cannot_run(
    moving: str, other: str, span: list[float] | None, fragment: str
) -> None:
    adapter = await _part(Arm=_ARM, Wall=_WALL)

    result = await adapter.rotation_clearance(moving, other, [0, 0, 1], None, span)

    assert fragment in (result.error or "")


async def test_an_assembly_sweep_that_cannot_run() -> None:
    adapter = await _assembly()
    missing = await adapter.rotation_clearance("component-1", "ghost-1", [0, 0, 1])
    adapter.component_boxes_mm["component-2"] = None
    empty = await adapter.rotation_clearance("component-1", "component-2", [0, 0, 1])

    assert "Component(s) not found: ghost-1" in (missing.error or "")
    assert "other 'component-2' holds no solid body" in (empty.error or "")


async def test_a_sweep_needs_a_part_or_an_assembly() -> None:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    nothing = await adapter.rotation_clearance("a", "b", [0, 0, 1])
    await adapter.create_drawing()
    drawing = await adapter.rotation_clearance("a", "b", [0, 0, 1])

    assert nothing.error == "No active model"
    assert "(active document is drawing)" in (drawing.error or "")


# ---- volume_by_plane ------------------------------------------------------------------


async def test_the_default_block_split_by_the_top_plane_offset() -> None:
    adapter = await _part()

    result = await adapter.volume_by_plane(None, [0, 4, 0], [0, 1, 0], None, True, 1.15)

    data = result.data
    assert data["above"]["volume_mm3"] == pytest.approx(9600.0)
    assert data["below"]["volume_mm3"] == pytest.approx(6400.0)
    assert data["below"]["mass_g"] == pytest.approx(7.36)
    assert data["below"]["enclosed_mm3"] == 0.0
    assert data["density_source"] == "input"
    assert data["plane"]["name"] is None


async def test_a_default_plane_by_name_and_the_part_density() -> None:
    adapter = await _part()
    await adapter.set_density(1.15)

    result = await adapter.volume_by_plane("Right")

    data = result.data
    assert data["plane"]["name"] == "Right Plane"
    assert data["below"]["volume_mm3"] == 0.0
    assert data["density_g_cm3"] == 1.15 and data["density_source"] == "solidworks"


async def test_a_material_gives_the_density() -> None:
    adapter = await _part()
    await adapter.set_material("ABS")

    result = await adapter.volume_by_plane("Top Plane")

    assert result.data["density_g_cm3"] == pytest.approx(1.02)


async def test_without_a_density_there_is_no_mass() -> None:
    adapter = await _part()

    result = await adapter.volume_by_plane("Front Plane", bodies=["Boss-Extrude1"])

    assert result.data["above"]["mass_g"] is None
    assert result.data["density_source"] is None


async def test_a_plane_or_body_the_mock_does_not_know() -> None:
    adapter = await _part()

    plane = await adapter.volume_by_plane("Fill line")
    body = await adapter.volume_by_plane("Top Plane", bodies=["Ghost"])

    assert "Plane not found: Fill line" in (plane.error or "")
    assert "Body(ies) not found: Ghost" in (body.error or "")


async def test_the_volume_needs_a_part() -> None:
    adapter = await _assembly()

    result = await adapter.volume_by_plane("Top Plane")

    assert "needs an active part document" in (result.error or "")


# ---- set_density -----------------------------------------------------------------------


async def test_a_density_on_a_part() -> None:
    adapter = await _part()

    result = await adapter.set_density(1.15)

    data = result.data
    assert data["applied"] is True and data["read_back_kg_m3"] == 1150.0
    assert data["mass_g"] == pytest.approx(16000 * 1.15 / 1000)
    assert data["material_removed"] is None


async def test_a_material_is_refused_unless_it_comes_off() -> None:
    adapter = await _part()
    await adapter.set_material("ABS")

    refused = await adapter.set_density(1.15)
    removed = await adapter.set_density(1.15, remove_material=True)
    material = await adapter.get_material()

    assert "the part has the material ABS" in (refused.error or "")
    assert removed.data["material_removed"]["material"] == "ABS"
    assert material.data["material"] is None


async def test_a_density_needs_a_part() -> None:
    adapter = await _assembly()

    result = await adapter.set_density(1.15)

    assert "set_density needs an active part document" in (result.error or "")


# ---- set_component_transform with a turn ------------------------------------------------


async def test_a_component_turned_about_a_pivot() -> None:
    adapter = await _assembly()

    result = await adapter.set_component_transform(
        "component-1", [100, 0, 0], None, None, [0, 0, 1], [50, 0, 0], 90
    )

    assert result.data["origin_mm"] == pytest.approx([50.0, 50.0, 0.0])
    assert result.data["rotation"][0] == pytest.approx([0.0, 1.0, 0.0])
