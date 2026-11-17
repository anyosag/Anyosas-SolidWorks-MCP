"""``set_density``: the document density, read back two ways, on a fake part.

The fake part is a 40 x 10 x 40 block: 16000 mm3, so at 1.15 g/cm3 it weighs
18.4 g.
"""

from __future__ import annotations

import pytest

from solidworks_mcp.adapters.solidworks import density
from solidworks_mcp.adapters.solidworks.clearance import InvalidRequest

from .fake_bodies import Harness
from .fake_volume import VolumeWorld


@pytest.fixture(autouse=True)
def _plain_com(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        density,
        "_sw_type_info",
        type("T", (), {"flagged": staticmethod(lambda o, *_: o)}),
    )


def _part(material: tuple[str, str] = ("", "")) -> VolumeWorld:
    world = VolumeWorld({"Boss-Extrude1": [0.0, 0.0, 0.0, 40.0, 10.0, 40.0]})
    world.material = material
    world.material_density = 7900.0
    return world


def test_a_resin_density_on_a_part_without_material() -> None:
    world = _part()

    found = density.set_density(Harness(world), 1.15)

    assert world.preferences == [(7, 0, 1150.0)]
    assert found["applied"] is True
    assert found["read_back_kg_m3"] == 1150.0
    assert found["mass_properties_density_g_cm3"] == pytest.approx(1.15)
    assert found["volume_mm3"] == pytest.approx(16000.0)
    assert found["mass_g"] == pytest.approx(18.4)
    assert found["material_removed"] is None
    assert found["method"] == "document_density"
    assert "saved with the part" in found["persists"]
    assert world.rebuilds == 1


def test_a_part_with_a_material_is_refused() -> None:
    world = _part(("AISI 1020", "SOLIDWORKS Materials"))

    with pytest.raises(InvalidRequest, match="AISI 1020 \\(SOLIDWORKS Materials\\)"):
        density.set_density(Harness(world), 1.15)

    assert world.preferences == []
    assert "remove_material" in density.material_message("ABS", "")


def test_the_material_comes_off_first_when_asked() -> None:
    world = _part(("AISI 1020", "SOLIDWORKS Materials"))

    found = density.set_density(Harness(world), 1.15, remove_material=True)

    assert world.material_calls == [("", "", "")]
    assert found["material_removed"] == {
        "material": "AISI 1020",
        "database": "SOLIDWORKS Materials",
    }
    assert found["applied"] is True


def test_a_material_that_will_not_come_off() -> None:
    world = _part(("AISI 1020", ""))
    world.material_stuck = True

    with pytest.raises(RuntimeError, match="kept the material AISI 1020"):
        density.set_density(Harness(world), 1.15, remove_material=True)
    assert world.preferences == []


def test_a_refused_value_is_an_error() -> None:
    world = _part()
    world.preference_refused = True

    with pytest.raises(RuntimeError, match="refused the density"):
        density.set_density(Harness(world), 1.15)


def test_a_density_the_mass_properties_ignore_is_not_applied() -> None:
    world = _part()
    world.preference_ignored = True

    found = density.set_density(Harness(world), 1.15)

    assert found["applied"] is False
    assert found["mass_properties_density_g_cm3"] == pytest.approx(1.0)
    assert found["read_back_kg_m3"] == 1000.0


def test_without_mass_properties_nothing_is_known() -> None:
    world = _part()
    world.mass_fails = True

    found = density.set_density(Harness(world), 1.15)

    assert found["applied"] is False
    assert found["volume_mm3"] is None and found["mass_g"] is None
