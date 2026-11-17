"""A fake SolidWorks for the rotation sweep: every body is a ball.

A ball turned about an axis is a ball whose centre turned, so distances and
overlaps have exact answers: two balls of radius ``r`` and ``R`` with centres
``d`` apart are ``d - r - R`` apart, and overlap by the lens between them.

* Part bodies: ``Copy`` makes a temporary ball, ``ApplyTransform`` moves its
  centre by ``p . R + t`` (the transform's ``ArrayData``, metres), and
  ``Operations2`` (intersect, temporary bodies only, consuming both) gives the
  lens as a piece with that volume.
* Components hold balls in their part's coordinates and a ``Transform2``;
  their faces' assembly twins (``GetCorrespondingEntity``) sit wherever the
  component is placed at the moment ``ClosestDistance`` measures them.

The zero-argument methods refuse to run unflagged, as late binding does
(pitfall #5), and so does ``IMathUtility.CreateTransform`` (pitfall #41).
"""

from __future__ import annotations

import math
from typing import Any

from solidworks_mcp.adapters.solidworks import bodies as bd

from .fake_bodies import Com, MathUtility, SelectData, Transform

M = 1000.0


def lens_mm3(d: float, r1: float, r2: float) -> float:
    """The volume two balls share, their centres ``d`` apart (mm)."""
    if d >= r1 + r2:
        return 0.0
    if d <= abs(r1 - r2):
        return 4.0 / 3.0 * math.pi * min(r1, r2) ** 3
    return (
        math.pi
        * (r1 + r2 - d) ** 2
        * (d * d + 2 * d * (r1 + r2) - 3 * (r1 - r2) ** 2)
        / (12 * d)
    )


def place(data: Any, point: list[float]) -> list[float]:
    """``point`` (mm) placed by a transform's 16 values (translation in metres)."""
    values = [float(v) for v in data]
    rows = [values[0:3], values[3:6], values[6:9]]
    return [
        sum(point[k] * rows[k][j] for k in range(3)) + values[9 + j] * M
        for j in range(3)
    ]


class Piece(Com):
    """What an intersect gives: a temporary body with a volume."""

    def __init__(self, volume_mm3: float) -> None:
        super().__init__()
        self.volume = volume_mm3

    def GetMassProperties(self, density: float) -> tuple[float, ...]:  # noqa: N802
        return (0.0, 0.0, 0.0, self.volume / 1e9, 0.0, 0.0)


class BallFace(Com):
    """A ball's one face; its box is the ball's, in the body's coordinates."""

    def __init__(self, ball: Ball) -> None:
        super().__init__()
        self.ball = ball

    def GetBox(self) -> tuple[float, ...]:  # noqa: N802
        self.need("GetBox")
        r, c = self.ball.radius, self.ball.centre
        return tuple([(v - r) / M for v in c] + [(v + r) / M for v in c])

    def centre(self) -> list[float]:
        return list(self.ball.centre)


class Ball(Com):
    """A solid ball (mm)."""

    def __init__(
        self, world: World, name: str, centre: list[float], radius: float, temp=False
    ) -> None:
        super().__init__()
        self.world, self.Name = world, name
        self.centre, self.radius = [float(v) for v in centre], float(radius)
        self.temp, self.consumed = temp, False

    def GetBodyBox(self) -> tuple[float, ...]:  # noqa: N802
        self.need("GetBodyBox")
        r = self.radius
        return tuple(
            [(c - r) / M for c in self.centre] + [(c + r) / M for c in self.centre]
        )

    def GetMassProperties(self, density: float) -> tuple[float, ...]:  # noqa: N802
        volume = 4.0 / 3.0 * math.pi * self.radius**3
        return (*(c / M for c in self.centre), volume / 1e9, 0.0, 0.0)

    def Copy(self) -> Ball:  # noqa: N802
        self.need("Copy")
        self.world.copies += 1
        return Ball(self.world, self.Name, self.centre, self.radius, temp=True)

    def ApplyTransform(self, xform: Any) -> bool:  # noqa: N802
        assert self.temp, "ApplyTransform on a part's own body"
        self.world.transforms.append(list(xform.ArrayData))
        if self.world.transform_fails:
            return False
        self.centre = place(xform.ArrayData, self.centre)
        return True

    def Operations2(self, operation: int, tool: Any, code: Any) -> Any:  # noqa: N802
        assert self.temp and tool.temp, "Operations2 on a part's own body"
        assert not self.consumed and not tool.consumed, "a consumed body reused"
        assert operation == bd.BODY_INTERSECT
        self.consumed = tool.consumed = True
        self.world.booleans += 1
        shared = lens_mm3(math.dist(self.centre, tool.centre), self.radius, tool.radius)
        code.value = bd.NO_ERROR if shared > 0 else bd.NO_INTERSECT
        return (Piece(shared),) if shared > 0 else None

    def GetFaces(self) -> tuple[BallFace, ...]:  # noqa: N802
        self.need("GetFaces")
        return (BallFace(self),)


class Twin(Com):
    """A component face in the assembly: placed by the component, now."""

    def __init__(self, component: Component, face: BallFace) -> None:
        super().__init__()
        self.component, self.face = component, face

    def centre(self) -> list[float]:
        return place(self.component.data, self.face.ball.centre)

    @property
    def ball(self) -> Ball:
        return self.face.ball


class Component(Com):
    """A component of balls, in its part's coordinates, placed by ``Transform2``."""

    def __init__(
        self,
        world: World,
        name: str,
        balls: list[tuple[list[float], float]],
        origin_mm: list[float] = (0.0, 0.0, 0.0),  # type: ignore[assignment]
        fixed: bool = False,
        mates: int = 0,
    ) -> None:
        super().__init__()
        self.world, self.Name2 = world, name
        self.balls = [
            Ball(world, f"{name}-{k}", c, r) for k, (c, r) in enumerate(balls)
        ]
        self.data = (
            [1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0]
            + [v / M for v in origin_mm]
            + [1.0, 0.0, 0.0, 0.0]
        )
        self.fixed, self.mates = fixed, mates
        self.placements: list[list[float]] = []

    @property
    def Transform2(self) -> Transform:  # noqa: N802
        return Transform(list(self.data))

    @Transform2.setter
    def Transform2(self, xform: Transform) -> None:  # noqa: N802
        values = list(xform.ArrayData)
        self.placements.append(values)
        frozen = self.world.frozen_after
        if frozen is None or len(self.placements) <= frozen:
            self.data = values

    def GetSuppression2(self) -> int:  # noqa: N802
        return 2

    def GetBodies2(self, kind: int) -> tuple[Ball, ...]:  # noqa: N802
        assert kind == 0
        return tuple(self.balls)

    def GetCorrespondingEntity(self, face: BallFace) -> Twin:  # noqa: N802
        return Twin(self, face)

    def IsFixed(self) -> bool:  # noqa: N802
        return self.fixed

    def Select4(self, append: bool, data: Any, callout: bool) -> bool:  # noqa: N802
        self.world.selected = self
        return True

    def GetMates(self) -> tuple[Any, ...] | None:  # noqa: N802
        return tuple(object() for _ in range(self.mates)) or None


class SelectionManager(Com):
    def CreateSelectData(self) -> SelectData:  # noqa: N802
        return SelectData()


class Model(Com):
    """The part or assembly document."""

    def __init__(self, world: World) -> None:
        super().__init__()
        self.world = world
        self.SelectionManager = SelectionManager()

    def GetType(self) -> int:  # noqa: N802
        return self.world.doc_type

    def GetBodies2(self, kind: int, visible_only: bool) -> Any:  # noqa: N802
        return tuple(self.world.bodies) or None

    def GetComponents(self, top_level: bool) -> list[Component]:  # noqa: N802
        return list(self.world.components)

    def ClearSelection2(self, everything: bool) -> None:  # noqa: N802
        self.world.selected = None

    def EditRebuild3(self) -> bool:  # noqa: N802
        self.world.rebuilds += 1
        return True

    def FixComponent(self) -> None:  # noqa: N802
        self.world.selected.fixed = True
        self.world.fixes.append(("fix", self.world.selected.Name2))

    def UnfixComponent(self) -> None:  # noqa: N802
        self.world.selected.fixed = False
        self.world.fixes.append(("float", self.world.selected.Name2))

    def ClosestDistance(self, a: Any, b: Any, p1: Any, p2: Any) -> float:  # noqa: N802
        self.world.distances += 1
        if self.world.distance_fails:
            return -1.0
        ca, cb = a.centre(), b.centre()
        d = math.dist(ca, cb)
        gap = max(0.0, d - a.ball.radius - b.ball.radius)
        towards = [(cb[k] - ca[k]) / d for k in range(3)] if d else [1.0, 0, 0]
        pa = [ca[k] + towards[k] * a.ball.radius for k in range(3)]
        pb = [cb[k] - towards[k] * b.ball.radius for k in range(3)]
        p1.value = tuple(v / M for v in pa)
        p2.value = tuple(v / M for v in pb)
        return gap / M


class App(Com):
    def __init__(self) -> None:
        super().__init__()
        self.utility = MathUtility()

    def GetMathUtility(self) -> MathUtility:  # noqa: N802
        return self.utility


class World:
    """The fake document: its balls or components, and what was done to them."""

    def __init__(self, doc_type: int = 1) -> None:
        self.doc_type = doc_type
        self.bodies: list[Ball] = []
        self.components: list[Component] = []
        self.copies = 0
        self.booleans = 0
        self.distances = 0
        self.rebuilds = 0
        self.transforms: list[list[float]] = []
        self.fixes: list[tuple[str, str]] = []
        self.selected: Any = None
        self.transform_fails = False
        self.distance_fails = False
        #: Mates that hold a component: after this many placements it stops
        #: moving (0: it never moves); None: it always moves.
        self.frozen_after: int | None = None
        self.model = Model(self)
        self.app = App()

    def ball(self, name: str, centre: list[float], radius: float) -> Ball:
        made = Ball(self, name, centre, radius)
        self.bodies.append(made)
        return made

    def component(self, name: str, *args: Any, **kwargs: Any) -> Component:
        made = Component(self, name, *args, **kwargs)
        self.components.append(made)
        return made


class Adapter:
    """The PyWin32Adapter members the sweep relies on."""

    def __init__(self, world: World) -> None:
        self.currentModel = world.model
        self.swApp = world.app

    def _attempt(self, operation: Any, default: Any = None) -> Any:
        try:
            return operation()
        except Exception:  # noqa: BLE001 - mirrors PyWin32Adapter
            return default

    def _attempt_with_error(self, operation: Any) -> tuple[Any, Exception | None]:
        try:
            return operation(), None
        except Exception as exc:  # noqa: BLE001
            return None, exc

    def _get_attr_or_call(self, obj: Any, name: str) -> Any:
        value = getattr(obj, name)
        return value() if callable(value) else value
