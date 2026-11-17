"""Moving an offset reference plane (``ref_plane_edit``).

The Dipper build cut 0.2 mm off a block instead of moving the plane its
sketch stood on. Fakes model ``Plane1``, 10 mm above Top Plane: an
``IRefPlaneFeatureData`` whose indexed ``Constraint`` and
``ReversedReferenceDirection`` answer only through ``IDispatch.Invoke`` (as
under pywin32 late binding), and whose ``ModifyDefinition`` moves the plane's
``Transform``.
"""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import feature_editing as fe
from solidworks_mcp.adapters.solidworks import ref_plane_edit as rp

_NULL = object()  # the VT_DISPATCH null component
_GET, _PUT = 2, 4


class _Ole:
    """The ``IDispatch`` of :class:`_PlaneData`, for its indexed properties."""

    def __init__(self, data: _PlaneData) -> None:
        self._data = data
        self.calls: list[tuple[str, int, tuple[Any, ...]]] = []

    def GetIDsOfNames(self, _lcid: int, name: str) -> int:  # noqa: N802
        if name not in self._data.indexed:
            raise AttributeError(f"Unknown name {name}")
        return list(self._data.indexed).index(name) + 100

    def Invoke(
        self, dispid: int, _lcid: int, flags: int, _want: bool, *args: Any
    ) -> Any:  # noqa: N802
        name = list(self._data.indexed)[dispid - 100]
        self.calls.append((name, flags, args))
        if flags == _GET:
            assert args == (0,)
            return self._data.indexed[name]
        index, value = args
        assert index == 0
        self._data.indexed[name] = value
        return None


class _PlaneData:
    """``IRefPlaneFeatureData`` of an offset plane."""

    def __init__(
        self,
        distance: float | None = 0.01,
        constraint: int | None = rp.CONSTRAINT_DISTANCE,
        reversed_: bool | None = False,
        access: bool = True,
    ) -> None:
        self._distance = distance
        self.indexed: dict[str, Any] = {}
        if constraint is not None:
            self.indexed["Constraint"] = constraint
        if reversed_ is not None:
            self.indexed["ReversedReferenceDirection"] = reversed_
        self._oleobj_ = _Ole(self)
        self._access = access
        self.released = 0

    @property
    def Distance(self) -> float:  # noqa: N802
        if self._distance is None:
            raise AttributeError("Distance")
        return self._distance

    @Distance.setter
    def Distance(self, value: float) -> None:  # noqa: N802
        self._distance = value

    def AccessSelections(self, _doc: Any, _component: Any) -> bool:  # noqa: N802
        return self._access

    def ReleaseSelectionAccess(self) -> None:  # noqa: N802
        self.released += 1


class _Transform:
    def __init__(self, origin_m: list[float]) -> None:
        self.ArrayData = [1, 0, 0, 0, 0, -1, 0, 1, 0, *origin_m, 1, 0, 0, 0]


class _Plane:
    """A ``RefPlane`` feature over Top Plane; ``ModifyDefinition`` moves it."""

    def __init__(self, data: Any, modify: bool = True) -> None:
        self.Name = "Plane1"
        self._data, self._modify = data, modify
        self.origin = [0.0, 0.01, 0.0]
        self.definition_after: Any = "same"

    def GetTypeName2(self) -> str:  # noqa: N802
        return "RefPlane"

    def GetTypeName(self) -> None:  # noqa: N802
        return None

    def GetDefinition(self) -> Any:  # noqa: N802
        return self._data

    def GetSpecificFeature2(self) -> Any:  # noqa: N802
        return type("RefPlane", (), {"Transform": _Transform(self.origin)})()

    def ModifyDefinition(self, data: Any, _doc: Any, _component: Any) -> bool:  # noqa: N802
        if not self._modify:
            return False
        side = -1 if data.indexed.get("Constraint", 0) & rp.CONSTRAINT_FLIP else 1
        if "Constraint" not in data.indexed and data.indexed.get(
            "ReversedReferenceDirection"
        ):
            side = -1
        self.origin = [0.0, side * data.Distance, 0.0]
        if self.definition_after != "same":
            self._data = self.definition_after
        return True


class _Model:
    def __init__(self, plane: _Plane | None) -> None:
        self.plane = plane
        self.calls: list[str] = []

    def FeatureByName(self, name: str) -> Any:  # noqa: N802
        return self.plane if self.plane and name == self.plane.Name else None

    def EditRebuild3(self) -> bool:  # noqa: N802
        self.calls.append("rebuild")
        return True


class _Adapter:
    def __init__(self, model: _Model) -> None:
        self.currentModel = model

    def _attempt(self, operation: Any, default: Any = None) -> Any:
        try:
            return operation()
        except Exception:  # noqa: BLE001
            return default

    def _get_attr_or_call(self, obj: Any, name: str) -> Any:
        value = getattr(obj, name)
        return value() if callable(value) else value


def _edit(plane: _Plane, changes: dict[str, Any]) -> tuple[dict[str, Any], _Model]:
    model = _Model(plane)
    return fe.edit_feature(_Adapter(model), "Plane1", changes, _NULL), model


# ---- the edit ----------------------------------------------------------------


def test_the_offset_moves_the_plane() -> None:
    data = _PlaneData()

    reply, model = _edit(_Plane(data), {"offset": 9.8})

    assert reply == {
        "feature": "Plane1",
        "type": "RefPlane",
        "before": {"offset": 10.0, "flip": False, "origin_mm": [0.0, 10.0, 0.0]},
        "after": {"offset": 9.8, "flip": False, "origin_mm": [0.0, 9.8, 0.0]},
    }
    assert data.indexed["Constraint"] == rp.CONSTRAINT_DISTANCE  # side unchanged
    assert model.calls == ["rebuild"]


def test_flip_sets_the_option_flip_bit_of_the_first_reference() -> None:
    data = _PlaneData(constraint=rp.CONSTRAINT_DISTANCE | 1)

    reply, _model = _edit(_Plane(data), {"flip": True})

    assert data.indexed["Constraint"] == rp.CONSTRAINT_DISTANCE | 1 | rp.CONSTRAINT_FLIP
    assert reply["after"] == {"offset": 10.0, "flip": True, "origin_mm": [0, -10, 0]}
    assert ("Constraint", _PUT, (0, 265)) in data._oleobj_.calls


def test_flip_back_clears_the_bit() -> None:
    data = _PlaneData(constraint=rp.CONSTRAINT_DISTANCE | rp.CONSTRAINT_FLIP)

    reply, _model = _edit(_Plane(data), {"flip": False, "offset": 4})

    assert reply["before"]["flip"] is True
    assert data.indexed["Constraint"] == rp.CONSTRAINT_DISTANCE
    assert reply["after"]["origin_mm"] == [0.0, 4.0, 0.0]


def test_without_a_constraint_the_side_is_the_reversed_direction() -> None:
    data = _PlaneData(constraint=None, reversed_=False)

    reply, _model = _edit(_Plane(data), {"flip": True})

    assert data.indexed["ReversedReferenceDirection"] is True
    assert reply["after"]["flip"] is True
    assert reply["after"]["origin_mm"] == [0.0, -10.0, 0.0]


def test_a_side_that_reads_nowhere_cannot_be_flipped() -> None:
    data = _PlaneData(constraint=None, reversed_=None)

    with pytest.raises(RuntimeError, match="side can't be changed"):
        _edit(_Plane(data), {"flip": True})
    assert data.released == 1


def test_after_the_rebuild_a_lost_definition_reports_what_was_asked() -> None:
    plane = _Plane(_PlaneData())
    plane.definition_after = None

    reply, _model = _edit(plane, {"offset": 3})

    assert reply["after"] == {"offset": 3, "origin_mm": [0.0, 3.0, 0.0]}


@pytest.mark.parametrize(
    ("data", "modify", "error", "fragment"),
    [
        (None, True, RuntimeError, "gave no definition for 'Plane1'"),
        (_PlaneData(access=False), True, RuntimeError, "would not open"),
        (_PlaneData(), False, RuntimeError, "refused the change"),
        (_PlaneData(constraint=4), True, ValueError, "is not an offset plane"),
        (_PlaneData(constraint=16), True, ValueError, "set_dimension"),
        (
            _PlaneData(distance=None, constraint=None),
            True,
            ValueError,
            "gave no distance for 'Plane1'",
        ),
    ],
)
def test_edits_that_fail_say_why(
    data: Any, modify: bool, error: type[Exception], fragment: str
) -> None:
    with pytest.raises(error, match=None) as caught:
        _edit(_Plane(data, modify=modify), {"offset": 2})
    assert fragment in str(caught.value)


def test_a_refused_change_releases_the_definition() -> None:
    data = _PlaneData()

    with pytest.raises(RuntimeError):
        _edit(_Plane(data, modify=False), {"offset": 2})
    assert data.released == 1


def test_a_plane_takes_only_offset_and_flip() -> None:
    with pytest.raises(ValueError, match="it takes flip, offset, not depth"):
        _edit(_Plane(_PlaneData()), {"depth": 2})


# ---- the reads -----------------------------------------------------------------


def test_a_definition_that_reads_only_its_distance() -> None:
    plane = rp.read_definition(
        _Adapter(_Model(None)), _PlaneData(constraint=None, reversed_=None)
    )

    assert plane == rp.PlaneDefinition(offset_mm=10.0, constraint=None, reversed=None)
    assert plane.flip is None
    assert rp.plane_problem("Plane1", plane) is None


def test_a_plane_without_a_transform_has_no_origin() -> None:
    feature = type("F", (), {"GetSpecificFeature2": lambda self: None})()

    assert rp.plane_origin_mm(_Adapter(_Model(None)), feature) is None


def test_indexed_properties_go_through_invoke() -> None:
    data = _PlaneData()

    assert rp.get_indexed(data, "Constraint", 0) == rp.CONSTRAINT_DISTANCE
    rp.put_indexed(data, "ReversedReferenceDirection", 0, True)
    assert data.indexed["ReversedReferenceDirection"] is True
    with pytest.raises(AttributeError, match="Unknown name"):
        rp.get_indexed(data, "Nope", 0)
