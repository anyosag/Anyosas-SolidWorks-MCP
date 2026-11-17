"""``add_sketch_text``: InsertSketchText, its ITextFormat, and a guide line for an angle.

The fakes refuse a zero-argument method that wasn't flagged first (pitfall
#5), so a missing flag fails a test instead of silently returning a value.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import sketch_text
from solidworks_mcp.adapters.solidworks.sketch import SolidWorksSketchMixin


class _Com:
    def __init__(self) -> None:
        self.flagged: set[str] = set()

    def _FlagAsMethod(self, *names: str) -> None:  # noqa: N802
        self.flagged.update(names)

    def need(self, name: str) -> None:
        if name not in self.flagged:
            raise TypeError(f"'{name}' was called without being flagged")


class _Format(_Com):
    def __init__(self) -> None:
        super().__init__()
        self.CharHeight = 0.005
        self.TypeFaceName = "Century Gothic"
        self.Bold = False
        self.Italic = False
        self.Escapement = 0.0
        self.CharHeightInPts = 0

    def IsHeightSpecifiedInPts(self) -> bool:  # noqa: N802
        self.need("IsHeightSpecifiedInPts")
        return False


class _Text(_Com):
    """An ``ISketchText``: what SetTextFormat was handed becomes its format."""

    def __init__(self, x: float, y: float, text: str, take_format: bool) -> None:
        super().__init__()
        self.Text = text
        self._xy = (x, y)
        self._format = _Format()
        self._take = take_format
        self.set_calls: list[tuple] = []
        self.use_doc = True

    def GetCoordinates(self) -> tuple[float, float, float]:  # noqa: N802
        self.need("GetCoordinates")
        return (self._xy[0], self._xy[1], 0.0)

    def GetTextFormat(self) -> _Format:  # noqa: N802
        self.need("GetTextFormat")
        copy = _Format()
        copy.__dict__.update(
            {k: v for k, v in self._format.__dict__.items() if k != "flagged"}
        )
        return copy

    def GetUseDocTextFormat(self) -> bool:  # noqa: N802
        self.need("GetUseDocTextFormat")
        return self.use_doc

    def SetTextFormat(self, use_doc: bool, text_format: _Format) -> bool:  # noqa: N802
        self.set_calls.append((use_doc, text_format))
        if self._take:
            self._format, self.use_doc = text_format, use_doc
        return self._take


class _Line(_Com):
    def __init__(self, *coords: float) -> None:
        super().__init__()
        self.coords = coords
        self.ConstructionGeometry = False
        self.selected: list[tuple] = []
        self.selectable = True

    def Select4(self, append: bool, data: Any) -> bool:  # noqa: N802
        self.selected.append((append, data.Mark))
        return self.selectable


class _Model(_Com):
    def __init__(self, inserts: bool = True, take_format: bool = True) -> None:
        super().__init__()
        self.inserted: list[tuple] = []
        self.clears = 0
        self._inserts, self._take = inserts, take_format
        self.SelectionManager = SimpleNamespace(
            CreateSelectData=lambda: SimpleNamespace(Mark=0)
        )

    def ClearSelection2(self, _all: bool) -> bool:  # noqa: N802
        self.clears += 1
        return True

    def InsertSketchText(self, *args: Any) -> _Text | None:  # noqa: N802
        self.inserted.append(args)
        if not self._inserts:
            return None
        return _Text(args[0], args[1], args[3], self._take)


class _Adapter:
    def __init__(self, model: _Model, line: _Line | None = None) -> None:
        self.currentModel = model
        self.lines: list[tuple] = []
        self._line = line

        def create_line(*coords: float) -> _Line | None:
            self.lines.append(coords)
            if self._line is None:
                return None
            self._line.coords = coords
            return self._line

        self.currentSketchManager = SimpleNamespace(CreateLine=create_line)
        self._sketch_entities: dict[str, Any] = {}
        self._counter = 0

    def _attempt(self, callback: Any, default: Any = None) -> Any:
        try:
            return callback()
        except Exception:
            return default

    def _register_sketch_entity(self, prefix: str, entity: Any) -> str:
        self._counter += 1
        entity_id = f"{prefix}_{self._counter}"
        self._sketch_entities[entity_id] = entity
        return entity_id


def _write(adapter: _Adapter, **fields: Any) -> dict[str, Any]:
    return sketch_text.add_sketch_text(adapter, fields.pop("text", "MAX"), **fields)


def test_text_goes_in_at_its_point_in_sketch_metres() -> None:
    model = _Model()
    adapter = _Adapter(model)

    data = _write(adapter, x=12.0, y=-3.5)

    assert model.inserted == [(0.012, -0.0035, 0.0, "MAX", 0, 0, 0, 100, 100)]
    assert data["id"] == "Text_1"
    assert data["text"] == "MAX"
    assert data["position_mm"] == [12.0, -3.5]
    assert data["font"] == "Century Gothic"
    assert data["height_mm"] == 5.0
    assert data["uses_document_font"] is True
    assert data["format_applied"] is None
    assert data["along_line"] is None
    assert data["angle_deg"] == 0.0
    assert data["shares_sketch_with"] == []
    assert model.clears == 2  # before (nothing for the text to follow) and after


def test_the_height_font_and_style_are_set_through_its_format() -> None:
    model = _Model()
    adapter = _Adapter(model)

    data = _write(adapter, height=4.0, font="Arial", bold=True, italic=True)

    text = adapter._sketch_entities["Text_1"]
    use_doc, handed = text.set_calls[0]
    assert use_doc is False
    assert handed.CharHeight == pytest.approx(0.004)
    assert (handed.TypeFaceName, handed.Bold, handed.Italic) == ("Arial", True, True)
    assert data["format_applied"] is True
    assert (data["height_mm"], data["font"], data["bold"]) == (4.0, "Arial", True)
    assert data["uses_document_font"] is False


def test_a_format_solidworks_refuses_is_reported() -> None:
    data = _write(_Adapter(_Model(take_format=False)), height=4.0)

    assert data["format_applied"] is False
    assert data["height_mm"] == 5.0


def test_a_text_with_no_format_to_read_reports_it_not_applied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_Text, "GetTextFormat", lambda _self: None)

    data = _write(_Adapter(_Model()), font="Arial")

    assert data["format_applied"] is False


def test_an_angle_follows_a_construction_line_selected_at_mark_1() -> None:
    line = _Line()
    model = _Model()
    adapter = _Adapter(model, line)

    data = _write(adapter, text="OPEN", x=10.0, y=5.0, height=4.0, angle=90.0)

    length = sketch_text.guide_length_mm("OPEN", 4.0)
    x1, y1, z1, x2, y2, z2 = adapter.lines[0]
    assert (x1, y1, z1, z2) == (0.01, 0.005, 0.0, 0.0)
    assert x2 == pytest.approx(0.01)
    assert y2 == pytest.approx(0.005 + length / 1000.0)
    assert line.ConstructionGeometry is True
    assert line.selected == [(False, 1)]
    assert "Select4" in line.flagged
    assert data["along_line"] == "TextLine_1"
    assert data["id"] == "Text_2"
    assert data["angle_deg"] == 90.0


def test_a_guide_line_that_cannot_be_drawn_fails() -> None:
    with pytest.raises(Exception, match="Could not draw the construction line"):
        _write(_Adapter(_Model(), None), angle=30.0)


def test_a_guide_line_that_cannot_be_selected_fails() -> None:
    line = _Line()
    line.selectable = False

    with pytest.raises(Exception, match="Could not select the construction line"):
        _write(_Adapter(_Model(), line), angle=30.0)


def test_a_guide_line_with_no_selection_data_fails() -> None:
    model = _Model()
    model.SelectionManager = SimpleNamespace(CreateSelectData=lambda: None)

    with pytest.raises(Exception, match="Could not select the construction line"):
        _write(_Adapter(model, _Line()), angle=30.0)


def test_no_text_back_from_solidworks_is_an_error() -> None:
    with pytest.raises(Exception, match="SolidWorks inserted no text for 'MAX'"):
        _write(_Adapter(_Model(inserts=False)))


def test_other_geometry_in_the_sketch_is_named() -> None:
    adapter = _Adapter(_Model())
    adapter._sketch_entities.update(
        {"Rectangle_1": object(), "Text_2": object(), "TextLine_3": object()}
    )

    data = _write(adapter)

    assert data["shares_sketch_with"] == ["Rectangle_1"]


def test_a_guide_line_is_longer_than_its_text() -> None:
    assert sketch_text.guide_length_mm("AB", 4.0) == pytest.approx(12.0)
    assert sketch_text.guide_length_mm("", None) == pytest.approx(7.5)
    assert math.isclose(sketch_text.guide_length_mm("ABC", None), 22.5)


# ---- the mixin ----------------------------------------------------------------


class _Mixin(SolidWorksSketchMixin):
    def __init__(self, sketch_manager: Any) -> None:
        self.currentModel = _Model()
        self.currentSketchManager = sketch_manager
        self._sketch_entities: dict[str, Any] = {}
        self.ran: list[str] = []

    def _attempt(self, callback: Any, default: Any = None) -> Any:
        try:
            return callback()
        except Exception:
            return default

    def _register_sketch_entity(self, prefix: str, _entity: Any) -> str:
        return f"{prefix}_1"

    def _handle_com_operation(self, name: str, callback: Any) -> AdapterResult[Any]:
        self.ran.append(name)
        try:
            return AdapterResult(status=AdapterResultStatus.SUCCESS, data=callback())
        except Exception as exc:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=f"Error in {name}: {exc}"
            )


async def test_the_adapter_needs_an_open_sketch() -> None:
    result = await _Mixin(None).add_sketch_text("MAX")

    assert result.error == "No active sketch"


async def test_the_adapter_writes_inside_one_com_operation() -> None:
    adapter = _Mixin(SimpleNamespace())

    result = await adapter.add_sketch_text("MIN", 1.0, 2.0, 3.0, "Arial")

    assert result.is_success, result.error
    assert adapter.ran == ["add_sketch_text"]
    assert result.data["text"] == "MIN"


async def test_the_adapter_error_reads_as_a_sentence() -> None:
    adapter = _Mixin(SimpleNamespace())
    adapter.currentModel = _Model(inserts=False)

    result = await adapter.add_sketch_text("MIN")

    assert (result.error or "").startswith("SolidWorks inserted no text")
