"""An assembly's box from its components' solid bodies, placed in the assembly.

``GetBox(0)`` read 10 mm tall where the solids are 7.2 mm (a part's
``create_axis`` line counted; Aggrewell enclosure v2). The bodies come in
their part's coordinates and are placed by ``Transform2`` (``p . R + t``).
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import assembly_box, limits
from solidworks_mcp.adapters.solidworks.assembly_box import METHOD, solid_box

_IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


class _Block:
    """A box body in its part's coordinates (m), answering GetExtremePoint."""

    def __init__(self, low: tuple[float, ...], high: tuple[float, ...]) -> None:
        self.corners = [
            tuple(high[a] if (i >> a) & 1 else low[a] for a in range(3))
            for i in range(8)
        ]
        self.found = True

    def GetExtremePoint(self, dx, dy, dz, ox, oy, oz):  # noqa: N802 - COM name
        best = max(self.corners, key=lambda c: c[0] * dx + c[1] * dy + c[2] * dz)
        ox.value, oy.value, oz.value = best
        return self.found


class _Ball:
    """A ball body: its extreme point is the centre plus the radius along the direction."""

    def __init__(self, centre: tuple[float, ...], radius: float) -> None:
        self.centre, self.radius = centre, radius

    def GetExtremePoint(self, dx, dy, dz, ox, oy, oz):  # noqa: N802 - COM name
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        ox.value, oy.value, oz.value = (
            c + self.radius * d / length
            for c, d in zip(self.centre, (dx, dy, dz), strict=True)
        )
        return True


class _Component:
    """A component: its bodies (part coordinates), placement, state and children."""

    def __init__(
        self,
        bodies: tuple[Any, ...] = (),
        *,
        children: tuple[_Component, ...] | None = None,
        rows: tuple[tuple[float, ...], ...] = _IDENTITY,
        origin: tuple[float, ...] = (0.0, 0.0, 0.0),
        state: Any = 3,
        visible: Any = 1,
    ) -> None:
        self.bodies, self.children = bodies, children
        self.state, self.Visible = state, visible
        self.Transform2: Any = SimpleNamespace(
            ArrayData=(*(v for row in rows for v in row), *origin, 1.0, 0.0, 0.0, 0.0)
        )
        self.bodies_error: Exception | None = None

    def GetSuppression2(self):  # noqa: N802 - COM name
        return self.state

    def GetChildren(self):  # noqa: N802 - COM name
        return self.children

    def GetBodies2(self, _kind):  # noqa: N802 - COM name
        if self.bodies_error is not None:
            raise self.bodies_error
        return self.bodies or None


class _Adapter:
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


@pytest.fixture(autouse=True)
def _wrapping_is_the_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "solidworks_mcp.adapters.solidworks.io._dynamic",
        SimpleNamespace(Dispatch=lambda obj: obj),
    )


def _box(*components: Any) -> dict[str, Any] | None:
    return solid_box(_Adapter(), SimpleNamespace(GetComponents=lambda _top: components))


# ---- what is boxed --------------------------------------------------------------


def test_the_bodies_are_placed_by_their_components_transform() -> None:
    # Turned 90 degrees about z: the part's x runs along the assembly's +y.
    turned = _Component(
        (_Block((0.0, 0.0, 0.0), (0.01, 0.02, 0.03)),),
        rows=((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
        origin=(0.1, 0.0, 0.0),
    )

    box = _box(turned)

    assert box == {
        "min": [80.0, 0.0, 0.0],
        "max": [100.0, 10.0, 30.0],
        "size": [20.0, 10.0, 30.0],
        "method": METHOD,
    }


def test_a_turned_round_body_keeps_a_tight_box() -> None:
    # A box around the ball, turned 45 degrees and boxed again, would be
    # 2 * 5 * sqrt(2) = 14.1 mm wide; its extreme points give 10 mm.
    s = math.sqrt(0.5)
    ball = _Component(
        (_Ball((0.0, 0.0, 0.0), 0.005),),
        rows=((s, s, 0.0), (-s, s, 0.0), (0.0, 0.0, 1.0)),
        origin=(0.02, 0.0, 0.0),
    )

    box = _box(ball)

    assert box is not None
    assert box["size"] == pytest.approx([10.0, 10.0, 10.0])
    assert box["min"] == pytest.approx([15.0, -5.0, -5.0])


def test_several_components_and_bodies_make_one_box() -> None:
    base = _Component(
        (
            _Block((0, 0, 0), (0.05, 0.0072, 0.04)),
            _Block((0, 0, 0), (0.01, 0.0072, 0.01)),
        )
    )
    lid = _Component(
        (_Block((0, 0, 0), (0.05, 0.002, 0.04)),), origin=(0.0, 0.0072, 0.0)
    )

    box = _box(base, lid)

    assert box is not None
    assert box["size"] == pytest.approx([50.0, 9.2, 40.0])


def test_a_sub_assembly_is_read_down_to_its_parts() -> None:
    inner = _Component((_Block((0, 0, 0), (0.01, 0.01, 0.01)),), origin=(0.1, 0.0, 0.0))
    sub = _Component(children=(inner,))

    box = _box(sub, _Component((_Block((0, 0, 0), (0.01, 0.01, 0.01)),)))

    assert box is not None
    assert box["max"] == pytest.approx([110.0, 10.0, 10.0])


@pytest.mark.parametrize(
    "left_out",
    [
        _Component((_Block((0, 0, 0), (1.0, 1.0, 1.0)),), state=0),
        _Component((_Block((0, 0, 0), (1.0, 1.0, 1.0)),), visible=0),
        _Component(
            children=(_Component((_Block((0, 0, 0), (1.0, 1.0, 1.0)),)),), visible=0
        ),
    ],
    ids=["suppressed", "hidden", "in a hidden sub-assembly"],
)
def test_suppressed_and_hidden_components_are_left_out(left_out: _Component) -> None:
    shown = _Component((_Block((0, 0, 0), (0.01, 0.01, 0.01)),))

    box = _box(left_out, shown)

    assert box is not None
    assert box["size"] == pytest.approx([10.0, 10.0, 10.0])


def test_a_part_with_no_solid_body_adds_nothing() -> None:
    sketch_only = _Component()
    shown = _Component((_Block((0, 0, 0), (0.01, 0.01, 0.01)),))

    box = _box(sketch_only, shown)

    assert box is not None
    assert box["size"] == pytest.approx([10.0, 10.0, 10.0])


# ---- when it can't be told -------------------------------------------------------


def _unreadable_bodies() -> _Component:
    component = _Component((_Block((0, 0, 0), (0.01, 0.01, 0.01)),))
    component.bodies_error = RuntimeError("Member not found")
    return component


def _unplaced() -> _Component:
    component = _Component((_Block((0, 0, 0), (0.01, 0.01, 0.01)),))
    component.Transform2 = None
    return component


def _pointless() -> _Component:
    block = _Block((0, 0, 0), (0.01, 0.01, 0.01))
    block.found = False
    return _Component((block,))


@pytest.mark.parametrize(
    "component",
    [
        _Component(state=1),  # lightweight: its bodies aren't loaded
        _Component(state=4),
        _unreadable_bodies(),
        _unplaced(),
        _pointless(),
        None,  # a component that can't be wrapped
    ],
    ids=["lightweight", "fully lightweight", "bodies", "placement", "point", "wrap"],
)
def test_anything_unreadable_gives_no_box(monkeypatch, component: Any) -> None:
    if component is None:
        monkeypatch.setattr(
            "solidworks_mcp.adapters.solidworks.io._dynamic",
            SimpleNamespace(Dispatch=lambda obj: None),
        )
        component = _Component((_Block((0, 0, 0), (0.01, 0.01, 0.01)),))

    assert _box(component, _Component((_Block((0, 0, 0), (1, 1, 1)),))) is None


@pytest.mark.parametrize("components", [None, ()])
def test_no_components_or_none_shown_give_no_box(components: Any) -> None:
    assembly = SimpleNamespace(GetComponents=lambda _top: components)

    assert solid_box(_Adapter(), assembly) is None
    assert _box(_Component(visible=0)) is None


def test_a_tree_past_the_component_limit_gives_no_box(monkeypatch) -> None:
    monkeypatch.setattr(assembly_box, "MAX_COMPONENTS", 2)
    blocks = [_Component((_Block((0, 0, 0), (0.01, 0.01, 0.01)),)) for _ in range(3)]

    assert _box(*blocks) is None


def test_the_walk_stops_when_the_operation_is_stopped(monkeypatch) -> None:
    def stop(doing: str) -> None:
        raise limits.OperationStopped(f"Stopped {doing}")

    monkeypatch.setattr(limits, "check", stop)

    with pytest.raises(limits.OperationStopped, match="solid bodies for its box"):
        _box(_Component((_Block((0, 0, 0), (0.01, 0.01, 0.01)),)))
