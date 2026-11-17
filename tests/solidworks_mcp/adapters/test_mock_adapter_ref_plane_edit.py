"""The mock's ``edit_feature`` on a reference plane."""

from __future__ import annotations

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


async def _part() -> MockSolidWorksAdapter:
    mock = MockSolidWorksAdapter({})
    await mock.connect()
    await mock.create_part()
    return mock


async def test_a_plane_without_a_recorded_definition_is_not_offset() -> None:
    mock = await _part()
    mock._reference_planes.append("Plane7")  # made without create_reference_plane

    result = await mock.edit_feature("Plane7", {"offset": 2})

    assert result.error == (
        "'Plane7' is not an offset plane (its first reference is not a distance); "
        "edit_feature changes an offset plane's offset and flip. Change an angled "
        "plane's angle with set_dimension."
    )


async def test_a_negative_offset_is_kept_as_its_distance() -> None:
    mock = await _part()
    await mock.create_reference_plane("Front Plane", offset=-4, flip=False)

    result = await mock.edit_feature("Plane1", {"flip": True})

    assert result.is_success, result.error
    assert result.data["before"] == {"offset": 4, "flip": False}
    assert result.data["after"] == {"offset": 4, "flip": True}


async def test_a_plane_change_it_does_not_take_is_refused() -> None:
    mock = await _part()
    await mock.create_reference_plane("Front Plane", offset=4)

    result = await mock.edit_feature("Plane1", {"radius": 1})

    assert result.error == "'Plane1' is a RefPlane; it takes flip, offset, not radius."
