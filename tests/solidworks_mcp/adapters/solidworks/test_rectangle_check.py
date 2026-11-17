"""``add_rectangle`` reads back what it drew (``rectangle_check``).

The Dipper: ``add_rectangle {x1: 10.6, y1: -81, x2: 12.6, y2: -17}`` reported
2 x 64 mm and drew a 2 x 2 square (y -81 to -79).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import rectangle_check as rc
from solidworks_mcp.adapters.solidworks import sketch


def _point(x_mm: float, y_mm: float) -> Any:
    return SimpleNamespace(X=x_mm / 1000.0, Y=y_mm / 1000.0)


class _Line:
    def __init__(self, start: tuple[float, float], end: tuple[float, float]) -> None:
        self.start, self.end = start, end
        self.ConstructionGeometry = False

    def GetType(self) -> int:  # noqa: N802
        return 0

    def GetStartPoint2(self) -> Any:  # noqa: N802
        return _point(*self.start)

    def GetEndPoint2(self) -> Any:  # noqa: N802
        return _point(*self.end)

    def GetCenterPoint2(self) -> Any:  # noqa: N802
        raise AttributeError("a line has no centre")


def _rectangle(x1: float, y1: float, x2: float, y2: float) -> list[_Line]:
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    return [_Line(corners[i], corners[(i + 1) % 4]) for i in range(4)]


class _App:
    def __init__(self, inference: Any = True) -> None:
        self.inference = inference
        self.calls: list[tuple[str, Any]] = []

    def GetUserPreferenceToggle(self, toggle: int) -> Any:  # noqa: N802
        self.calls.append(("get", toggle))
        return self.inference

    def SetUserPreferenceToggle(self, toggle: int, value: bool) -> bool:  # noqa: N802
        self.calls.append(("set", (toggle, value)))
        self.inference = value
        return True


class _Adapter:
    """An open sketch whose CreateCornerRectangle draws ``draws`` (corners in mm)."""

    def __init__(self, draws: Any = None, app: Any = None) -> None:
        self.swApp = app if app is not None else _App()
        self.deleted: list[Any] = []
        self.selected: list[tuple[Any, bool]] = []
        self.select_ok = True
        self.inference_when_drawn: Any = None
        self.currentModel = SimpleNamespace(EditDelete=self._delete)
        self.currentSketchManager = SimpleNamespace(
            AddToDB=False, CreateCornerRectangle=self._draw
        )
        self.draws = draws
        self._sketch_entities: dict[str, Any] = {}
        self._sketch_entity_centers: dict[str, Any] = {}

    def _draw(self, x1, y1, _z1, x2, y2, _z2) -> Any:  # noqa: ANN001
        self.inference_when_drawn = self.swApp.inference
        corners = self.draws or (x1 * 1000, y1 * 1000, x2 * 1000, y2 * 1000)
        return _rectangle(*corners)

    def _delete(self) -> None:
        self.deleted.append([handle for handle, _append in self.selected])

    def _select_sketch_entity(self, entity: Any, append: bool) -> bool:
        self.selected.append((entity, append))
        return self.select_ok

    def _handle_com_operation(self, _name: str, callback: Any) -> AdapterResult:
        try:
            return AdapterResult(status=AdapterResultStatus.SUCCESS, data=callback())
        except Exception as exc:  # noqa: BLE001
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))

    def _attempt(self, callback: Any, default: Any = None) -> Any:
        try:
            return callback()
        except Exception:  # noqa: BLE001
            return default

    def _register_sketch_entity(self, prefix: str, entity: Any) -> str:
        entity_id = f"{prefix}_{len(self._sketch_entities) + 1}"
        self._sketch_entities[entity_id] = entity
        return entity_id


def test_inference_is_off_while_drawing_and_back_after() -> None:
    adapter = _Adapter()

    with rc.inference_off(adapter):
        assert adapter.swApp.inference is False
        assert adapter.currentSketchManager.AddToDB is True

    assert adapter.swApp.inference is True
    assert adapter.swApp.calls == [
        ("get", 249),
        ("set", (249, False)),
        ("set", (249, True)),
    ]


@pytest.mark.parametrize("inference", [False, None, "on"])
def test_inference_already_off_or_unread_is_left_alone(inference: Any) -> None:
    adapter = _Adapter(app=_App(inference))

    with rc.inference_off(adapter):
        pass

    assert adapter.swApp.calls == [("get", 249)]


def test_inference_comes_back_when_the_draw_raises() -> None:
    adapter = _Adapter()

    with pytest.raises(RuntimeError), rc.inference_off(adapter):
        raise RuntimeError("COM error")

    assert adapter.swApp.inference is True


def test_the_extent_is_the_box_of_the_lines_drawn() -> None:
    lines: list[Any] = _rectangle(10.6, -81, 12.6, -79)
    point = SimpleNamespace(GetType=lambda: 1)  # a sketch point: not a line

    assert rc.drawn_extent(_Adapter(), [*lines, point]) == pytest.approx(
        (10.6, -81.0, 12.6, -79.0)
    )


def test_an_extent_that_does_not_read_is_none() -> None:
    lines = _rectangle(0, 0, 5, 5)
    lines[2].GetEndPoint2 = lambda: None  # type: ignore[method-assign]

    assert rc.drawn_extent(_Adapter(), lines) is None
    assert rc.drawn_extent(_Adapter(), _rectangle(0, 0, 5, 5)[:3]) is None
    assert rc.drawn_extent(_Adapter(), [1, 2, 3, 4]) is None


def test_a_rectangle_drawn_as_asked_matches() -> None:
    asked = (10.6, -81.0, 12.6, -17.0)

    assert rc.mismatch(asked, (10.6, -81.0, 12.6, -17.0005)) is None
    assert rc.mismatch(asked, (10.6, -81.0, 12.6, -79.0)) == (
        "(10.6, -81) to (12.6, -79) mm, 2 x 2, not (10.6, -81) to (12.6, -17) mm, "
        "2 x 64 as asked"
    )


def test_a_wrong_rectangle_is_deleted() -> None:
    adapter = _Adapter()
    lines = _rectangle(0, 0, 1, 1)

    assert rc.delete_segments(adapter, lines) is True
    assert [append for _h, append in adapter.selected] == [False, True, True, True]
    assert adapter.deleted == [lines]


def test_a_delete_that_fails_says_so() -> None:
    adapter = _Adapter()
    adapter.select_ok = False
    assert rc.delete_segments(adapter, _rectangle(0, 0, 1, 1)) is False

    def refuse() -> None:
        raise RuntimeError("EditDelete failed")

    adapter = _Adapter()
    adapter.currentModel.EditDelete = refuse
    assert rc.delete_segments(adapter, _rectangle(0, 0, 1, 1)) is False


def test_the_dippers_square_is_an_error_and_is_removed() -> None:
    adapter = _Adapter(draws=(10.6, -81, 12.6, -79))

    result = sketch._add_rectangle_impl(adapter, 10.6, -81, 12.6, -17)

    assert result.is_error
    assert result.error.startswith(
        "SolidWorks drew the rectangle from (10.6, -81) to (12.6, -79) mm, 2 x 2, "
        "not (10.6, -81) to (12.6, -17) mm, 2 x 64 as asked; it was deleted again."
    )
    assert "add_outlines" in result.error
    assert len(adapter.deleted) == 1
    assert adapter._sketch_entities == {}
    assert adapter.inference_when_drawn is False
    assert adapter.swApp.inference is True


def test_a_wrong_rectangle_that_stays_is_named() -> None:
    adapter = _Adapter(draws=(0, 0, 2, 2))
    adapter.select_ok = False

    result = sketch._add_rectangle_impl(adapter, 0, 0, 2, 9)

    assert "it is still in the sketch; delete it" in (result.error or "")


def test_a_rectangle_drawn_as_asked_is_registered() -> None:
    adapter = _Adapter()

    result = sketch._add_rectangle_impl(adapter, 12.6, -17, 10.6, -81)

    assert result.is_success, result.error
    assert adapter._sketch_entity_centers[result.data] == pytest.approx((11.6, -49.0))
    assert adapter.deleted == []
