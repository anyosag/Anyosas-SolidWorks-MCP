"""``add_sketch_entities`` draws many single entities in one COM operation.

Each entity is drawn by the ``_draw_*`` function its single tool uses, so the
COM calls, ids and registry entries match ``add_line``, ``add_circle``,
``add_rectangle``, ``add_arc`` and ``add_centerline`` drawn one by one; the
batch pays one settle gap instead of one per entity (speed plan item 7).
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import limits, sketch


class _Adapter:
    """The adapter attributes the sketch functions use, with no COM."""

    def __init__(self, manager: Any = None) -> None:
        self.currentModel: Any = SimpleNamespace(EditDelete=self._delete)
        self.currentSketchManager: Any = manager
        self.currentSketch: Any = None
        self.swApp: Any = None
        self._sketch_entities: dict[str, Any] = {}
        self._sketch_entity_centers: dict[str, tuple[float, float]] = {}
        self._next_id = 1
        self.operations: list[str] = []
        self.selected: list[tuple[Any, bool]] = []
        self.appends: list[bool] = []  # every select's append flag, in order
        self.select_ok = True
        self.deleted: list[list[Any]] = []

    def _delete(self) -> None:
        self.deleted.append([handle for handle, _append in self.selected])
        self.selected = []

    def _handle_com_operation(self, name: str, callback: Any) -> AdapterResult[Any]:
        """Like the real one: an exception becomes ``Error in <name>: ...``."""
        self.operations.append(name)
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

    def _attempt_with_error(self, callback: Any) -> tuple[Any, Any]:
        try:
            return callback(), None
        except Exception as exc:
            return None, exc

    def _register_sketch_entity(self, prefix: str, entity: Any) -> str:
        entity_id = f"{prefix}_{self._next_id}"
        self._next_id += 1
        self._sketch_entities[entity_id] = entity
        return entity_id

    def _select_sketch_entity(self, entity: Any, append: bool) -> bool:
        self.selected.append((entity, append))
        self.appends.append(append)
        return self.select_ok


class _Segment:
    """A drawn segment: what made it, and a construction flag read back."""

    def __init__(self, kind: str, args: tuple[Any, ...], sticky: bool = True) -> None:
        self.kind, self.args, self.sticky = kind, args, sticky
        self._construction = False

    @property
    def ConstructionGeometry(self) -> bool:  # noqa: N802
        return self._construction

    @ConstructionGeometry.setter
    def ConstructionGeometry(self, value: bool) -> None:  # noqa: N802
        if self.sticky:
            self._construction = value


class _Line(_Segment):
    """A rectangle side whose end points read back (``rectangle_check``)."""

    def __init__(self, start: tuple[float, float], end: tuple[float, float]) -> None:
        super().__init__("side", (start, end))
        self.start, self.end = start, end

    def GetType(self) -> int:  # noqa: N802
        return 0

    def GetStartPoint2(self) -> Any:  # noqa: N802
        return SimpleNamespace(X=self.start[0] / 1000.0, Y=self.start[1] / 1000.0)

    def GetEndPoint2(self) -> Any:  # noqa: N802
        return SimpleNamespace(X=self.end[0] / 1000.0, Y=self.end[1] / 1000.0)


class _Manager:
    """A sketch manager that records each Create call and can refuse one."""

    def __init__(
        self,
        refuse_at: int | None = None,
        sticky: bool = True,
        square: tuple[float, float, float, float] | None = None,
    ) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.refuse_at = refuse_at
        self.sticky = sticky
        self.square = square  # what CreateCornerRectangle draws instead (mm)

    def _refused(self, kind: str, args: tuple[Any, ...]) -> bool:
        self.calls.append((kind, args))
        return self.refuse_at is not None and len(self.calls) == self.refuse_at

    def _make(self, kind: str, args: tuple[Any, ...]) -> _Segment | None:
        if self._refused(kind, args):
            return None
        return _Segment(kind, args, self.sticky)

    def CreateLine(self, *args: Any) -> Any:  # noqa: N802
        return self._make("line", args)

    def CreateCircleByRadius(self, *args: Any) -> Any:  # noqa: N802
        return self._make("circle", args)

    def CreateArc(self, *args: Any) -> Any:  # noqa: N802
        return self._make("arc", args)

    def CreateCenterLine(self, *args: Any) -> Any:  # noqa: N802
        return self._make("centerline", args)

    def CreateCornerRectangle(self, *args: Any) -> Any:  # noqa: N802
        if self._refused("rectangle", args):
            return None
        x1, y1, _z1, x2, y2, _z2 = (v * 1000 for v in args)
        if self.square is not None:
            x1, y1, x2, y2 = self.square
        corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        lines = tuple(_Line(corners[i], corners[(i + 1) % 4]) for i in range(4))
        for line in lines:
            line.sticky = self.sticky
        return lines


def _all_kinds() -> list[dict[str, Any]]:
    """One item of each kind, as the single tools' adapter methods take them."""
    return [
        {"tool": "add_circle", "center_x": 10, "center_y": 5, "radius": 2},
        {"tool": "add_line", "x1": 0, "y1": 0, "x2": 20, "y2": 0},
        {"tool": "add_rectangle", "x1": 30, "y1": 0, "x2": 40, "y2": 8},
        {
            "tool": "add_arc",
            "center_x": 0,
            "center_y": 0,
            "start_x": 5,
            "start_y": 0,
            "end_x": 0,
            "end_y": 5,
            "direction": "cw",
        },
        {"tool": "add_centerline", "x1": 0, "y1": -9, "x2": 0, "y2": 9},
    ]


def _drawn(manager: _Manager) -> list[tuple[str, tuple[Any, ...]]]:
    return list(manager.calls)


# --- drawing ------------------------------------------------------------------


def test_every_kind_is_drawn_in_one_operation_in_order() -> None:
    manager = _Manager()
    adapter = _Adapter(manager)

    result = sketch._add_sketch_entities_impl(adapter, _all_kinds())

    assert result.is_success, result.error
    assert adapter.operations == ["add_sketch_entities"]
    assert [kind for kind, _ in manager.calls] == [
        "circle",
        "line",
        "rectangle",
        "arc",
        "centerline",
    ]
    data = result.data or {}
    assert [(i["index"], i["tool"], i["id"]) for i in data["items"]] == [
        (0, "add_circle", "Circle_1"),
        (1, "add_line", "Line_2"),
        (2, "add_rectangle", "Rectangle_3"),
        (3, "add_arc", "Arc_4"),
        (4, "add_centerline", "Centerline_5"),
    ]
    assert data["count"] == 5
    assert all(item["execution_time"] >= 0 for item in data["items"])
    # A cw arc is CreateArc's direction -1.
    assert manager.calls[3][1][-1] == -1


def test_a_batch_draws_and_registers_as_the_single_tools_do() -> None:
    """Same COM calls, ids and dimension points as five single calls."""
    one_by_one = _Adapter(_Manager())
    sketch._add_circle_impl(one_by_one, 10, 5, 2)
    sketch._add_line_impl(one_by_one, 0, 0, 20, 0)
    sketch._add_rectangle_impl(one_by_one, 30, 0, 40, 8)
    sketch._add_arc_impl(one_by_one, 0, 0, 5, 0, 0, 5, "cw")
    sketch._add_centerline_impl(one_by_one, 0, -9, 0, 9)
    batch = _Adapter(_Manager())

    result = sketch._add_sketch_entities_impl(batch, _all_kinds())

    assert result.is_success
    assert _drawn(batch.currentSketchManager) == _drawn(one_by_one.currentSketchManager)
    assert list(batch._sketch_entities) == list(one_by_one._sketch_entities)
    assert batch._sketch_entity_centers == one_by_one._sketch_entity_centers
    assert batch._sketch_entity_centers["Rectangle_3"] == (35.0, 4.0)
    assert len(one_by_one.operations) == 5
    assert batch.operations == ["add_sketch_entities"]
    # The ids each item gives use the prefix table the mock adapter reads.
    for item in (result.data or {})["items"]:
        assert item["id"].startswith(sketch.SKETCH_ENTITY_PREFIXES[item["tool"]])


def test_construction_items_become_construction_geometry() -> None:
    adapter = _Adapter(_Manager())
    items = [{**item, "construction": True} for item in _all_kinds()]

    result = sketch._add_sketch_entities_impl(adapter, items)

    assert result.is_success, result.error
    entities = adapter._sketch_entities
    assert entities["Circle_1"].ConstructionGeometry is True
    assert entities["Line_2"].ConstructionGeometry is True
    assert all(side.ConstructionGeometry for side in entities["Rectangle_3"])
    assert entities["Arc_4"].ConstructionGeometry is True
    # A centreline already is construction geometry: it is left as drawn.
    assert entities["Centerline_5"].ConstructionGeometry is False


# --- an item that fails ---------------------------------------------------------


def _three() -> list[dict[str, Any]]:
    """A circle, a rectangle (five segments in all), then a line."""
    return [
        {
            "tool": "add_circle",
            "center_x": 0,
            "center_y": 0,
            "radius": 3,
            "label": "circles[0]",
        },
        {
            "tool": "add_rectangle",
            "x1": 0,
            "y1": 0,
            "x2": 4,
            "y2": 2,
            "label": "rectangles[0]",
        },
        {"tool": "add_line", "x1": 0, "y1": 0, "x2": 9, "y2": 0, "label": "lines[0]"},
    ]


def test_a_refused_item_removes_what_the_call_drew() -> None:
    adapter = _Adapter(_Manager(refuse_at=3))

    result = sketch._add_sketch_entities_impl(adapter, _three())

    assert result.is_error
    assert result.error == (
        "lines[0] (line from (0, 0) to (9, 0)) was not created: Failed to create "
        "line. The 2 entities this call drew were removed again."
    )
    # The circle and the rectangle's four sides, selected and deleted together.
    assert adapter.appends == [False, True, True, True, True]
    assert len(adapter.deleted) == 1 and len(adapter.deleted[0]) == 5
    assert adapter._sketch_entities == {}
    assert adapter._sketch_entity_centers == {}
    assert result.data == {
        "items": [],
        "failed_index": 2,
        "failed": "lines[0]",
        "error": "Failed to create line",
        "removed": True,
    }


def test_without_rollback_the_items_before_the_failure_stay() -> None:
    """As separate calls would leave them: what the runner needs."""
    adapter = _Adapter(_Manager(refuse_at=3))

    result = sketch._add_sketch_entities_impl(adapter, _three(), False)

    assert result.is_error
    assert result.error == (
        "lines[0] (line from (0, 0) to (9, 0)) was not created: Failed to create "
        "line. The 2 entities drawn before it stay in the sketch."
    )
    assert adapter.deleted == []
    assert list(adapter._sketch_entities) == ["Circle_1", "Rectangle_2"]
    data = result.data or {}
    assert [item["id"] for item in data["items"]] == ["Circle_1", "Rectangle_2"]
    assert (data["failed_index"], data["removed"]) == (2, False)
    assert "failed_id" not in data


def test_a_failed_removal_names_the_ids_that_stay() -> None:
    adapter = _Adapter(_Manager(refuse_at=3))
    adapter.select_ok = False

    result = sketch._add_sketch_entities_impl(adapter, _three())

    assert result.error is not None
    assert result.error.endswith(
        "The 2 entities this call drew stay in the sketch (Circle_1, Rectangle_2); "
        "delete them (delete_sketch_entities) or undo."
    )
    data = result.data or {}
    assert [item["id"] for item in data["items"]] == ["Circle_1", "Rectangle_2"]
    assert data["removed"] is False


def test_many_ids_that_stay_are_named_by_the_first_and_last() -> None:
    items = [
        {"tool": "add_circle", "center_x": i, "center_y": 0, "radius": 1}
        for i in range(8)
    ]
    adapter = _Adapter(_Manager(refuse_at=8))
    adapter.select_ok = False

    result = sketch._add_sketch_entities_impl(adapter, items)

    assert "item 7 (circle at (7, 0), radius 1) was not created" in (result.error or "")
    assert (
        "The 7 entities this call drew stay in the sketch (Circle_1 ... Circle_7)"
        in (result.error or "")
    )


def test_a_first_item_that_fails_has_drawn_nothing() -> None:
    adapter = _Adapter(_Manager(refuse_at=1))

    result = sketch._add_sketch_entities_impl(adapter, _three())

    assert result.error == (
        "circles[0] (circle at (0, 0), radius 3) was not created: Failed to "
        "create circle."
    )
    assert adapter.selected == [] and adapter.deleted == []
    assert (result.data or {})["removed"] is False


def test_one_entity_removed_again_is_said_in_the_singular() -> None:
    adapter = _Adapter(_Manager(refuse_at=2))

    result = sketch._add_sketch_entities_impl(adapter, _three())

    assert (result.error or "").endswith(
        "The 1 entity this call drew was removed again."
    )


@pytest.mark.parametrize(
    ("item", "described"),
    [
        (
            {"tool": "add_circle", "center_x": 1.5, "center_y": -2, "radius": 4},
            "circle at (1.5, -2), radius 4",
        ),
        (
            {"tool": "add_line", "x1": 0, "y1": 0, "x2": 1, "y2": 2},
            "line from (0, 0) to (1, 2)",
        ),
        (
            {"tool": "add_rectangle", "x1": 0, "y1": 0, "x2": 1, "y2": 2},
            "rectangle from (0, 0) to (1, 2)",
        ),
        (
            {"tool": "add_centerline", "x1": 0, "y1": 0, "x2": 1, "y2": 2},
            "centerline from (0, 0) to (1, 2)",
        ),
        (
            {
                "tool": "add_arc",
                "center_x": 0,
                "center_y": 0,
                "start_x": 5,
                "start_y": 0,
                "end_x": 0,
                "end_y": 5,
            },
            "ccw arc about (0, 0) from (5, 0) to (0, 5)",
        ),
    ],
)
def test_a_refused_item_is_named_with_its_geometry(
    item: dict[str, Any], described: str
) -> None:
    adapter = _Adapter(_Manager(refuse_at=1))

    result = sketch._add_sketch_entities_impl(adapter, [item])

    assert (result.error or "").startswith(f"item 0 ({described}) was not created")


def test_a_create_call_that_raises_names_the_cause() -> None:
    manager = _Manager()
    manager.CreateCircleByRadius = (  # type: ignore[method-assign]
        lambda *_args: (_ for _ in ()).throw(RuntimeError("type mismatch"))
    )
    adapter = _Adapter(manager)

    result = sketch._add_sketch_entities_impl(adapter, _three())

    assert "was not created: type mismatch" in (result.error or "")
    assert (result.data or {})["error"] == "type mismatch"


def test_a_wrong_rectangle_fails_its_item() -> None:
    """The Dipper's 2 x 2 square for a 2 x 64 rectangle, inside a batch."""
    manager = _Manager(square=(10.6, -81, 12.6, -79))
    adapter = _Adapter(manager)
    items = [
        {"tool": "add_circle", "center_x": 0, "center_y": 0, "radius": 3},
        {"tool": "add_rectangle", "x1": 10.6, "y1": -81, "x2": 12.6, "y2": -17},
    ]

    result = sketch._add_sketch_entities_impl(adapter, items)

    message = result.error or ""
    assert message.startswith(
        "item 1 (rectangle from (10.6, -81) to (12.6, -17)) was not created: "
        "SolidWorks drew the rectangle from (10.6, -81) to (12.6, -79) mm"
    )
    assert "it was deleted again" in message
    assert message.endswith("The 1 entity this call drew was removed again.")
    # The wrong rectangle deleted by its own check, then the circle.
    assert [len(batch) for batch in adapter.deleted] == [4, 1]
    assert adapter._sketch_entities == {}


def test_geometry_that_will_not_become_construction_is_removed() -> None:
    adapter = _Adapter(_Manager(sticky=False))
    items = [{**_three()[0], "construction": True}]

    result = sketch._add_sketch_entities_impl(adapter, items)

    assert result.error == (
        "circles[0] (circle at (0, 0), radius 3) was drawn as Circle_1 but could "
        "not be made construction geometry. The 1 entity this call drew was "
        "removed again."
    )
    data = result.data or {}
    assert data["error"] == "SolidWorks did not make Circle_1 construction geometry"
    assert "failed_id" not in data
    assert adapter._sketch_entities == {}


def test_without_rollback_a_failed_construction_stays_as_normal_geometry() -> None:
    """As add_circle with construction leaves it: drawn, then not changed."""
    adapter = _Adapter(_Manager(sticky=False))
    items = [
        {"tool": "add_line", "x1": 0, "y1": 0, "x2": 1, "y2": 0},
        {**_three()[1], "construction": True},
    ]

    result = sketch._add_sketch_entities_impl(adapter, items, rollback_on_error=False)

    assert result.error == (
        "rectangles[0] (rectangle from (0, 0) to (4, 2)) was drawn as Rectangle_2 "
        "but could not be made construction geometry. Rectangle_2 stays in the "
        "sketch as normal geometry. The 1 entity drawn before it stays in the "
        "sketch."
    )
    data = result.data or {}
    assert data["failed_id"] == "Rectangle_2"
    assert [item["id"] for item in data["items"]] == ["Line_1"]
    assert "Rectangle_2" in adapter._sketch_entities


def test_a_stopped_batch_removes_what_it_drew(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _Adapter(_Manager())
    checks: list[str] = []

    def check(doing: str) -> None:
        checks.append(doing)
        if len(checks) == 3:
            raise limits.OperationStopped(f"Stopped {doing} after 901 s: limit")

    monkeypatch.setattr(limits, "check", check)

    result = sketch._add_sketch_entities_impl(adapter, _three())

    assert checks == ["drawing circles[0]", "drawing rectangles[0]", "drawing lines[0]"]
    assert result.error == (
        "Stopped drawing lines[0] after 901 s: limit. The 2 entities this call "
        "drew were removed again."
    )
    assert (result.data or {})["error"] == "Stopped drawing lines[0] after 901 s: limit"
    assert adapter._sketch_entities == {}


def test_a_stopped_batch_without_rollback_keeps_what_it_drew(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _Adapter(_Manager())

    def check(doing: str) -> None:
        if doing == "drawing rectangles[0]":
            raise limits.OperationStopped(f"Stopped {doing}: the MCP client left")

    monkeypatch.setattr(limits, "check", check)

    result = sketch._add_sketch_entities_impl(adapter, _three(), False)

    assert result.error == (
        "Stopped drawing rectangles[0]: the MCP client left. The 1 entity drawn "
        "before it stays in the sketch."
    )
    assert [item["id"] for item in (result.data or {})["items"]] == ["Circle_1"]


# --- errors from outside the items ---------------------------------------------


class _DialogAdapter(_Adapter):
    """SolidWorks showed a dialog during the call: the error carries it."""

    def _handle_com_operation(self, name: str, callback: Any) -> AdapterResult[Any]:
        self.operations.append(name)
        callback()
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error=f"SolidWorks showed a dialog during {name}",
            data={"dialog": {"title": "SOLIDWORKS"}},
        )


def test_a_dialog_after_the_items_keeps_its_details_beside_them() -> None:
    adapter = _DialogAdapter(_Manager())

    result = sketch._add_sketch_entities_impl(adapter, _three())

    assert result.error == "SolidWorks showed a dialog during add_sketch_entities"
    data = result.data or {}
    assert data["dialog"] == {"title": "SOLIDWORKS"}
    assert [item["id"] for item in data["items"]] == [
        "Circle_1",
        "Rectangle_2",
        "Line_3",
    ]
    assert data["failed_index"] is None
    assert data["removed"] is False


class _BusyAdapter(_Adapter):
    """SolidWorks was busy: the operation never ran."""

    def _handle_com_operation(self, name: str, callback: Any) -> AdapterResult[Any]:
        self.operations.append(name)
        return AdapterResult(
            status=AdapterResultStatus.ERROR, error="SolidWorks is busy"
        )


def test_an_operation_that_never_ran_has_no_item_data() -> None:
    adapter = _BusyAdapter(_Manager())

    result = sketch._add_sketch_entities_impl(adapter, _three())

    assert result.error == "SolidWorks is busy"
    assert result.data is None
    assert adapter.currentSketchManager.calls == []


# --- refused before drawing ----------------------------------------------------


_LINE = {"tool": "add_line", "x1": 0, "y1": 0, "x2": 1, "y2": 0}
_ARC = _all_kinds()[3]


@pytest.mark.parametrize(
    ("items", "message"),
    [
        ([], "Invalid request: no sketch entities to draw"),
        ({"tool": "add_line"}, "Invalid request: no sketch entities to draw"),
        ([7], "Invalid request: item 0 is not an object"),
        (
            [{"tool": "add_spline", "x1": 0}],
            "item 0: tool 'add_spline' is not one add_sketch_entities draws; give "
            "one of add_line, add_circle, add_rectangle, add_arc, add_centerline",
        ),
        ([{"x1": 0}], "item 0: tool None is not one add_sketch_entities draws"),
        (
            [_LINE, {"tool": "add_circle", "center_x": 0, "center_y": 0}],
            "item 1 (add_circle) needs radius",
        ),
        ([{**_LINE, "z1": 0}], "item 0 (add_line) has no field z1"),
        ([{**_LINE, "direction": "cw"}], "item 0 (add_line) has no field direction"),
        ([{**_LINE, "x2": True}], "item 0 (add_line): x2 must be a number, not True"),
        ([{**_LINE, "y1": "5"}], "item 0 (add_line): y1 must be a number, not '5'"),
        ([{**_LINE, "x1": math.nan}], "item 0 (add_line): x1 must be a number"),
        (
            [{**_LINE, "construction": "yes"}],
            "item 0 (add_line): construction must be true or false",
        ),
        ([{**_LINE, "label": 5}], "item 0 (add_line): label must be text"),
        (
            [{**_ARC, "direction": "up"}],
            "item 0 (add_arc): direction must be 'ccw' or 'cw', not 'up'",
        ),
        (
            [{**_ARC, "direction": ["cw"]}],
            "item 0 (add_arc): direction must be 'ccw' or 'cw', not ['cw']",
        ),
        (
            [{**_LINE, "tool": "add_rectangle"}] * 500 + [_LINE],
            "Invalid request: 2001 segments in one call (a rectangle is four); the "
            "most is 2000. Split them over several calls",
        ),
    ],
)
def test_items_it_cannot_draw_are_refused_before_drawing(
    items: Any, message: str
) -> None:
    manager = _Manager()
    adapter = _Adapter(manager)

    result = sketch._add_sketch_entities_impl(adapter, items)

    assert result.is_error
    assert message in (result.error or "")
    assert adapter.operations == []
    assert manager.calls == []


def test_no_open_sketch_is_refused() -> None:
    adapter = _Adapter(None)

    result = sketch._add_sketch_entities_impl(adapter, [_LINE])

    assert result.error == "No active sketch"
    assert adapter.operations == []


def test_the_problem_check_passes_drawable_items() -> None:
    assert sketch.sketch_entities_problem(_all_kinds()) is None
    assert sketch.sketch_entities_problem(tuple(_all_kinds())) is None


# --- the mixin forwards ---------------------------------------------------------


@pytest.mark.asyncio
async def test_the_mixin_forwards_add_sketch_entities() -> None:
    class _Mixed(sketch.SolidWorksSketchMixin, _Adapter):
        pass

    adapter = _Mixed(_Manager(refuse_at=3))

    kept = await adapter.add_sketch_entities(_three(), rollback_on_error=False)
    assert kept.is_error
    assert list(adapter._sketch_entities) == ["Circle_1", "Rectangle_2"]

    drawn = await adapter.add_sketch_entities([_LINE])
    assert drawn.is_success
    assert [item["id"] for item in (drawn.data or {})["items"]] == ["Line_3"]
