"""Turning the model view (``view_orientation``) on a fake ``IModelView``."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import view_matrix as vm
from solidworks_mcp.adapters.solidworks import view_orientation as vo


class _Transform:
    def __init__(self, data: Any) -> None:
        self.ArrayData = tuple(getattr(data, "value", data))


class _View:
    """An ``IModelView`` whose ``Orientation3`` takes a transform, or keeps its own."""

    def __init__(self, rows: list[list[float]], sticks: bool = True) -> None:
        self._transform = _Transform(
            vm.with_rows([0.0] * 9 + [0.1, 0.2, 0.3, 2.0, 0, 0, 0], rows)
        )
        self.sticks = sticks
        self.written: list[tuple[float, ...]] = []

    @property
    def Orientation3(self) -> Any:  # noqa: N802 - COM name
        return self._transform

    @Orientation3.setter
    def Orientation3(self, transform: Any) -> None:  # noqa: N802 - COM name
        self.written.append(transform.ArrayData)
        if self.sticks:
            self._transform = transform


class _Adapter:
    def __init__(self, utility: Any = None) -> None:
        self.swApp = SimpleNamespace(GetMathUtility=lambda: utility)

    def _attempt(self, fn: Any, default: Any = None) -> Any:
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return default


_UTILITY = SimpleNamespace(CreateTransform=_Transform)


def _doc(view: Any) -> Any:
    return SimpleNamespace(ActiveView=view)


def test_a_view_is_turned_and_read_back() -> None:
    view = _View(vm.standard_rows("front"))
    wanted = vm.look_from([1, -1, 1], [0, 0, 1])

    turned = vo.turn(_Adapter(_UTILITY), _doc(view), wanted)

    assert turned == {"axes": vm.axes(wanted), "checked": True}
    (written,) = view.written
    assert list(written[:9]) == pytest.approx([x for row in wanted for x in row])
    # The rest of the view's transform is kept as it was.
    assert list(written[9:13]) == [0.1, 0.2, 0.3, 2.0]


def test_a_view_solidworks_keeps_takes_no_picture() -> None:
    view = _View(vm.standard_rows("front"), sticks=False)

    with pytest.raises(RuntimeError, match="kept a different view orientation"):
        vo.turn(_Adapter(_UTILITY), _doc(view), vm.standard_rows("right"))


def test_an_orientation_that_reads_back_as_nothing_is_unchecked() -> None:
    class _WriteOnly(_View):
        @property
        def Orientation3(self) -> Any:  # noqa: N802
            return None

        @Orientation3.setter
        def Orientation3(self, transform: Any) -> None:  # noqa: N802
            self.written.append(transform.ArrayData)

    view = _WriteOnly(vm.standard_rows("front"))
    wanted = vm.standard_rows("top")

    turned = vo.turn(_Adapter(_UTILITY), _doc(view), wanted)

    assert turned == {"axes": vm.axes(wanted), "checked": False}
    assert view.written[0][9:] == (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)


@pytest.mark.parametrize(
    ("adapter", "doc"),
    [
        (_Adapter(None), _doc(_View([[1, 0, 0], [0, 1, 0], [0, 0, 1]]))),
        (_Adapter(_UTILITY), _doc(None)),
    ],
    ids=["no math utility", "no view"],
)
def test_without_a_view_or_utility_nothing_turns(adapter: Any, doc: Any) -> None:
    with pytest.raises(RuntimeError, match="could not be turned"):
        vo.turn(adapter, doc, vm.standard_rows("front"))


def test_a_standard_view_is_turned_z_up() -> None:
    view = _View(vm.standard_rows("isometric"))

    turned = vo.turn_z_up(_Adapter(_UTILITY), _doc(view))

    assert turned["axes"] == vm.axes(vm.look_from([1, -1, 1], [0, 0, 1]))
    read = vo.read_rows(_Adapter(_UTILITY), _doc(view))
    expected = vm.z_up(vm.standard_rows("isometric"))
    assert [x for row in read for x in row] == pytest.approx(
        [x for row in expected for x in row]
    )


def test_an_unreadable_view_isnt_turned_z_up() -> None:
    with pytest.raises(RuntimeError, match="can't be read"):
        vo.turn_z_up(_Adapter(_UTILITY), _doc(None))
