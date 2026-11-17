"""``pattern_linear``: directions and seeds selected by mark, then FeatureLinearPattern5.

The API help's Remarks give the marks: Direction 1 at 1, Direction 2 at 2,
features at 4, solid bodies at 256. A model axis ("x") is the default plane
whose normal it is; an edge point is picked after one rebuild, made before
anything is selected (a rebuild clears the selection, pitfall #23).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import features, patterns


class _Feature:
    def __init__(self, part: _Part, name: str) -> None:
        self.part, self.Name = part, name

    def Select2(self, append: bool, mark: int) -> bool:  # noqa: N802
        self.part.selected.append(("feature", self.Name, append, mark))
        return True


class _Part:
    """A part with named features, planes and axes, recording what is selected.

    ``by_id`` lists the ``(name, type)`` pairs ``SelectByID2`` finds.
    """

    def __init__(
        self,
        features_by_name: tuple[str, ...] = (),
        by_id: tuple[tuple[str, str], ...] = (),
        built: bool = True,
        raises: Exception | None = None,
    ) -> None:
        self.features = set(features_by_name)
        self.by_id = set(by_id)
        self.selected: list[tuple] = []
        self.calls: list[tuple] = []
        self.cleared = 0
        self._built, self._raises = built, raises
        self.Extension = SimpleNamespace(SelectByID2=self._select_by_id)
        self.FeatureManager = SimpleNamespace(FeatureLinearPattern5=self._pattern)

    def FeatureByName(self, name: str) -> _Feature | None:  # noqa: N802
        return _Feature(self, name) if name in self.features else None

    def ClearSelection2(self, _all: bool) -> bool:  # noqa: N802
        self.cleared += 1
        self.selected.clear()
        return True

    def _select_by_id(self, name: str, kind: str, *args: Any) -> bool:
        if (name, kind) not in self.by_id:
            return False
        self.selected.append((kind, name, args[3], args[4]))
        return True

    def _pattern(self, *args: Any) -> Any:
        self.calls.append(args)
        if self._raises is not None:
            raise self._raises
        return SimpleNamespace(Name="LPattern1") if self._built else None


class _Adapter:
    def __init__(self, part: _Part | None) -> None:
        self.currentModel = part

    def _handle_com_operation(self, name: str, callback: Any) -> AdapterResult[Any]:
        try:
            return AdapterResult(status=AdapterResultStatus.SUCCESS, data=callback())
        except Exception as exc:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=f"Error in {name}: {exc}"
            )

    def _attempt(self, callback: Any, default: Any = None) -> Any:
        try:
            return callback()
        except Exception:
            return default

    def _attempt_with_error(self, callback: Any) -> tuple[Any, Exception | None]:
        try:
            return callback(), None
        except Exception as exc:
            return None, exc

    def _get_attr_or_call(self, obj: Any, name: str) -> Any:
        return getattr(obj, name)


class _Picker:
    """Stands in for ``features._BodyEntityPicker``; records rebuilds."""

    made: list[_Picker] = []

    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter
        self.rebuilds = 0
        self.selected_before_rebuild: list[tuple] | None = None
        _Picker.made.append(self)

    def rebuild(self) -> None:
        self.rebuilds += 1
        self.selected_before_rebuild = list(self.adapter.currentModel.selected)


@pytest.fixture
def edge_picks(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Edge points picked through ``features._select_edge_by_coord``."""
    picks: list[tuple] = []
    _Picker.made = []

    def pick(adapter, x, y, z, append, mark, kind, picker) -> bool:
        picks.append((round(x, 9), round(y, 9), round(z, 9), append, mark, kind))
        adapter.currentModel.selected.append(("EDGE", (x, y, z), append, mark))
        return True

    monkeypatch.setattr(features, "_select_edge_by_coord", pick)
    monkeypatch.setattr(features, "_BodyEntityPicker", _Picker)
    return picks


def _run(part: _Part | None, *args: Any, **kwargs: Any) -> AdapterResult[Any]:
    return patterns.pattern_linear(_Adapter(part), *args, **kwargs)


# ---- reading a direction --------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "kind", "flip", "plane"),
    [
        ("x", "principal", False, "right"),
        ("+Y", "principal", False, "top"),
        ("-z", "principal", True, "front"),
        (" - x ", "principal", True, "right"),
    ],
)
def test_a_model_axis_is_the_normal_of_a_default_plane(
    spec: str, kind: str, flip: bool, plane: str
) -> None:
    direction = patterns.parse_direction(spec)

    assert (direction.kind, direction.flip, direction.plane_key) == (kind, flip, plane)


@pytest.mark.parametrize("spec", ["12,0,-5", "edge:12,0,-5", "EDGE: 12, 0, -5"])
def test_a_point_names_the_edge_through_it(spec: str) -> None:
    direction = patterns.parse_direction(spec)

    assert direction.kind == "edge"
    assert direction.point_mm == (12.0, 0.0, -5.0)


@pytest.mark.parametrize("spec", ["Axis1", "Top Plane", "a,b,c"])
def test_anything_else_is_a_name(spec: str) -> None:
    direction = patterns.parse_direction(spec)

    assert (direction.kind, direction.name) == ("named", spec)


@pytest.mark.parametrize(
    ("spec", "fragment"),
    [("", "direction is required"), ("edge:1,2", "an edge is given as 'x,y,z'")],
)
def test_a_direction_that_cannot_be_read_is_refused(spec: str, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        patterns.parse_direction(spec)


@pytest.mark.parametrize(
    ("arguments", "fragment"),
    [
        (([], 2, 5.0, None, 1, 0.0), "at least one feature"),
        ((["Cut1"], 1, 5.0, None, 1, 0.0), "count of at least 2"),
        ((["Cut1"], 3, 0.0, None, 1, 0.0), "positive spacing"),
        ((["Cut1"], 3, 5.0, "y", 1, 5.0), "count2 of at least 2"),
        ((["Cut1"], 3, 5.0, "y", 2, 0.0), "positive spacing2"),
    ],
)
def test_a_pattern_that_cannot_be_made_is_refused_before_selecting(
    arguments: tuple, fragment: str
) -> None:
    assert fragment in str(patterns.linear_pattern_refusal(*arguments))
    features_, count, spacing, direction2, count2, spacing2 = arguments
    part = _Part()

    result = _run(
        part,
        features_,
        "x",
        count,
        spacing,
        direction2,
        count2,
        spacing2,
    )

    assert (
        result.error
        == f"Invalid request: {patterns.linear_pattern_refusal(*arguments)}"
    )
    assert part.cleared == 0


def test_an_unreadable_direction_is_an_invalid_request() -> None:
    result = _run(_Part(), ["Cut1"], "edge:1", 2, 5.0)

    assert (result.error or "").startswith("Invalid request: Direction 'edge:1'")


def test_without_a_part_nothing_is_patterned() -> None:
    assert _run(None, ["Cut1"], "x", 2, 5.0).error == "No active model"


# ---- a row and a grid -----------------------------------------------------------


def test_a_row_along_x_follows_the_right_planes_normal() -> None:
    part = _Part(features_by_name=("Right Plane",), by_id=(("Slot", "BODYFEATURE"),))

    result = _run(part, ["Slot"], "x", 8, 12.0)

    assert result.is_success, result.error
    assert part.selected == [
        ("feature", "Right Plane", False, patterns.MARK_DIRECTION_1),
        ("BODYFEATURE", "Slot", True, patterns.MARK_FEATURES),
    ]
    args = part.calls[0]
    assert len(args) == 22
    assert args[:8] == (8, pytest.approx(0.012), 1, 0.0, False, False, "", "")
    assert result.data == {
        "name": "LPattern1",
        "patterned": ["Slot"],
        "bodies": False,
        "direction": {
            "spec": "x",
            "follows": "normal of Right Plane",
            "count": 8,
            "spacing_mm": 12.0,
            "flipped": False,
        },
        "direction2": None,
        "instances": 8,
    }


def test_a_grid_takes_both_directions_and_their_senses() -> None:
    part = _Part(
        features_by_name=("Top Plane", "Lead-in"),
        by_id=(("Axis1", "AXIS"),),
    )

    result = _run(
        part,
        ["Lead-in"],
        "Axis1",
        4,
        10.0,
        "-y",
        4,
        7.5,
        reverse_direction=True,
        reverse_direction2=True,
    )

    assert result.is_success, result.error
    assert part.selected == [
        ("AXIS", "Axis1", False, 1),
        ("feature", "Top Plane", True, 2),
        ("feature", "Lead-in", True, 4),
    ]
    args = part.calls[0]
    # Axis1 reversed flips; "-y" reversed goes back to +y.
    assert args[:6] == (4, 0.01, 4, 0.0075, True, False)
    assert result.data["direction2"]["follows"] == "normal of Top Plane"
    assert result.data["instances"] == 16


def test_a_named_plane_or_edge_is_found_by_type_too() -> None:
    part = _Part(by_id=(("Plane3", "PLANE"), ("Cut2", "BODYFEATURE")))

    result = _run(part, ["Cut2"], "Plane3", 2, 5.0)

    assert result.is_success, result.error
    assert part.selected[0] == ("PLANE", "Plane3", False, 1)


def test_bodies_are_selected_as_solid_bodies_at_mark_256() -> None:
    part = _Part(
        features_by_name=("Front Plane",), by_id=(("Boss-Extrude2", "SOLIDBODY"),)
    )

    result = _run(part, ["Boss-Extrude2"], "z", 3, 20.0, bodies=True)

    assert result.is_success, result.error
    assert part.selected[-1] == ("SOLIDBODY", "Boss-Extrude2", True, 256)
    assert result.data["bodies"] is True


def test_an_edge_direction_rebuilds_before_anything_is_selected(
    edge_picks: list[tuple],
) -> None:
    part = _Part(features_by_name=("Right Plane", "Slot"))

    result = _run(part, ["Slot"], "x", 2, 5.0, "edge:10,0,-20", 3, 4.0)

    assert result.is_success, result.error
    picker = _Picker.made[0]
    assert picker.rebuilds == 1
    assert picker.selected_before_rebuild == []
    assert edge_picks == [(0.01, 0.0, -0.02, True, 2, "EDGE")]
    assert result.data["direction2"]["follows"] == "edge at [10.0, 0.0, -20.0] mm"


def test_no_rebuild_without_an_edge_direction(edge_picks: list[tuple]) -> None:
    part = _Part(features_by_name=("Right Plane", "Slot"))

    _run(part, ["Slot"], "x", 2, 5.0)

    assert _Picker.made[0].rebuilds == 0
    assert edge_picks == []


# ---- what can go wrong ----------------------------------------------------------


def test_an_edge_point_that_selects_nothing_is_reported(
    monkeypatch: pytest.MonkeyPatch, edge_picks: list[tuple]
) -> None:
    monkeypatch.setattr(features, "_select_edge_by_coord", lambda *_a: False)
    part = _Part(features_by_name=("Slot",))

    result = _run(part, ["Slot"], "1,2,3", 2, 5.0)

    assert result.error == (
        "Failed to select the edge at [1.0, 2.0, 3.0] mm for direction '1,2,3'."
    )


def test_a_default_plane_that_cannot_be_selected_is_reported() -> None:
    result = _run(_Part(features_by_name=("Slot",)), ["Slot"], "y", 2, 5.0)

    assert (result.error or "").startswith("Failed to select the top plane")


def test_a_name_that_is_nothing_in_the_part_says_what_a_direction_is() -> None:
    result = _run(_Part(features_by_name=("Slot",)), ["Slot"], "Axis9", 2, 5.0)

    assert "Failed to select 'Axis9' as a pattern direction" in (result.error or "")
    assert "'x', 'y' or 'z'" in (result.error or "")


@pytest.mark.parametrize(
    ("bodies", "fragment"),
    [
        (False, "feature to pattern: Gone (list_features names them)"),
        (True, "body to pattern: Gone (list_bodies names them)"),
    ],
)
def test_a_seed_that_is_not_there_is_named(bodies: bool, fragment: str) -> None:
    part = _Part(features_by_name=("Right Plane",))

    result = _run(part, ["Gone"], "x", 2, 5.0, bodies=bodies)

    assert fragment in (result.error or "")
    assert part.calls == []


def test_a_feature_found_only_by_its_tree_name_is_still_a_seed() -> None:
    part = _Part(features_by_name=("Right Plane", "Rib1"))

    result = _run(part, ["Rib1"], "x", 2, 5.0)

    assert result.is_success, result.error
    assert part.selected[-1] == ("feature", "Rib1", True, 4)


@pytest.mark.parametrize(
    ("part", "fragment"),
    [
        (_Part(features_by_name=("Right Plane", "Slot"), built=False), "nothing"),
        (
            _Part(features_by_name=("Right Plane", "Slot"), raises=RuntimeError("COM")),
            "nothing (seeds=['Slot'], direction='x'): COM",
        ),
    ],
)
def test_a_call_that_builds_nothing_says_why(part: _Part, fragment: str) -> None:
    result = _run(part, ["Slot"], "x", 2, 5.0)

    assert not result.is_success
    assert (result.error or "").startswith("FeatureLinearPattern5 returned nothing")
    assert fragment in (result.error or "")


def test_a_second_direction_is_named_when_nothing_is_built() -> None:
    part = _Part(features_by_name=("Right Plane", "Top Plane", "Slot"), built=False)

    result = _run(part, ["Slot"], "x", 2, 5.0, "y", 2, 5.0)

    assert "direction2='y'" in (result.error or "")


def test_an_unreadable_pattern_name_is_none() -> None:
    part = _Part(features_by_name=("Right Plane", "Slot"))
    part.FeatureManager.FeatureLinearPattern5 = lambda *_a: object()

    result = _run(part, ["Slot"], "x", 2, 5.0)

    assert result.is_success
    assert result.data["name"] is None


# ---- the mixin --------------------------------------------------------------------


async def test_the_adapter_method_passes_everything_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple] = []
    monkeypatch.setattr(
        patterns, "pattern_linear", lambda *args: seen.append(args) or "result"
    )
    adapter = PyWin32Adapter.__new__(PyWin32Adapter)

    result = await adapter.pattern_linear(
        ["Slot"], "x", 8, 12.0, "y", 2, 6.0, True, False, True
    )

    assert result == "result"
    assert seen == [(adapter, ["Slot"], "x", 8, 12.0, "y", 2, 6.0, True, False, True)]
