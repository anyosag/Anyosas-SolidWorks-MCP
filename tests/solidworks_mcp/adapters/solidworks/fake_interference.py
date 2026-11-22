"""A fake SolidWorks assembly for the interference engine: every body is boxes.

A body is one or more lumps, each a box (mm): a U-shaped bracket is two lumps.
Components hold bodies in their part's coordinates and a ``Transform2``
(rotation rows, translation in metres, scale), as SolidWorks gives them.

* ``Copy`` makes a temporary body; ``ApplyTransform`` places its lumps by the
  transform (``p . R + t``; exact for the quarter turns the tests use).
* ``Operations2`` (intersect, temporary bodies only, consuming both) gives one
  piece per pair of lumps that share a volume, as the kernel gives one body
  per region, with ``swBodyOperationNoIntersect`` and no piece when nothing
  is shared, and ``swBodyOperationSameToolAndTarget`` and no piece for two
  identical bodies when the world says so.
* The zero-argument methods refuse to run unflagged, as late binding does
  (pitfall #5), and so does ``IMathUtility.CreateTransform`` (pitfall #41).

The world counts what was done (copies, placements, booleans) and has a
switch for each failure the engine must hand to SolidWorks' own detection.
"""

from __future__ import annotations

from typing import Any

from solidworks_mcp.adapters.solidworks import bodies as bd
from solidworks_mcp.adapters.solidworks.clearance import transform_box

from .fake_bodies import Com, MathUtility, Transform

M = 1000.0
IDENTITY_ROWS = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
#: A quarter turn about z: the part's x runs along the assembly's +y.
QUARTER_TURN_Z = ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0))

Box = list[float]


def cube(low: tuple[float, float, float], size: float) -> Box:
    """A cube (mm) from its low corner."""
    return [*map(float, low), *(float(v) + size for v in low)]


class Body(Com):
    """A solid body: one or more disjoint lumps (boxes, mm)."""

    def __init__(self, world: World, lumps: list[Box], temp: bool = False) -> None:
        super().__init__()
        self.world = world
        self.lumps = [list(map(float, lump)) for lump in lumps]
        self.temp, self.consumed = temp, False
        self.Visible = True
        self.box_fails = False

    @property
    def volume(self) -> float:
        return sum(bd.box_volume(lump) for lump in self.lumps)

    def GetBodyBox(self) -> tuple[float, ...] | None:  # noqa: N802 - COM name
        self.need("GetBodyBox")
        if self.box_fails:
            return None
        around = self.lumps[0]
        for lump in self.lumps[1:]:
            around = bd.union_box(around, lump)
        return tuple(v / M for v in around)

    def GetMassProperties(self, density: float) -> tuple[float, ...]:  # noqa: N802
        if self.world.volume_fails:
            return ()
        return (0.0, 0.0, 0.0, self.volume / 1e9, 0.0, 0.0)

    def Copy(self) -> Body | None:  # noqa: N802 - COM name
        self.need("Copy")
        if self.world.copies_left is not None:
            if self.world.copies_left == 0:
                return None
            self.world.copies_left -= 1
        self.world.copies += 1
        return Body(self.world, self.lumps, temp=True)

    def ApplyTransform(self, xform: Any) -> bool:  # noqa: N802 - COM name
        assert self.temp, "ApplyTransform on a part's own body"
        values = [float(v) for v in xform.ArrayData]
        self.world.placements.append(values)
        if self.world.move_fails:
            return False
        rows = [values[0:3], values[3:6], values[6:9]]
        origin = [v * M for v in values[9:12]]
        self.lumps = [transform_box(lump, rows, origin) for lump in self.lumps]
        return True

    def Operations2(self, operation: int, tool: Body, code: Any) -> Any:  # noqa: N802
        assert self.temp and tool.temp, "Operations2 on a part's own body"
        assert not self.consumed and not tool.consumed, "a consumed body reused"
        assert operation == bd.BODY_INTERSECT
        self.consumed = tool.consumed = True
        world = self.world
        world.booleans += 1
        if world.boolean_error is not None:
            raise world.boolean_error
        if world.code is not None:
            code.value = world.code
            return None
        if world.same_body and self.lumps == tool.lumps:
            code.value = bd.SAME_TOOL_AND_TARGET
            return None
        pieces = []
        for lump in self.lumps:
            for other in tool.lumps:
                shared = bd.box_overlap(lump, other)
                if shared is not None:
                    pieces.append(Body(world, [shared], temp=True))
        code.value = bd.NO_ERROR if pieces else bd.NO_INTERSECT
        return tuple(pieces) or None


class Component(Com):
    """A component: bodies in its part's coordinates, a placement, state, children."""

    def __init__(
        self,
        world: World,
        name: str,
        bodies: list[Body] | None = None,
        *,
        origin_mm: tuple[float, float, float] = (0.0, 0.0, 0.0),
        rows: tuple[tuple[float, ...], ...] = IDENTITY_ROWS,
        state: Any = 2,
        visible: Any = 1,
        children: list[Component] | None = None,
    ) -> None:
        super().__init__()
        self.world, self.Name2 = world, name
        self.bodies = list(bodies or [])
        self.state, self.Visible = state, visible
        self.children = children
        self.data: list[float] | None = [
            *(v for row in rows for v in row),
            *(v / M for v in origin_mm),
            1.0,
            0.0,
            0.0,
            0.0,
        ]
        self.bodies_error: Exception | None = None
        self.children_error: Exception | None = None
        self.bodies_read = 0

    @property
    def Transform2(self) -> Transform | None:  # noqa: N802 - COM name
        return None if self.data is None else Transform(list(self.data))

    def GetSuppression2(self) -> Any:  # noqa: N802 - COM name
        return self.state

    def GetChildren(self) -> Any:  # noqa: N802 - COM name
        if self.children_error is not None:
            raise self.children_error
        return tuple(self.children) if self.children else None

    def GetBodies2(self, kind: int) -> Any:  # noqa: N802 - COM name
        assert kind == bd.SOLID_BODY
        self.bodies_read += 1
        if self.bodies_error is not None:
            raise self.bodies_error
        return tuple(self.bodies) or None


class Assembly(Com):
    """The assembly document: its top-level components, and SolidWorks' detection."""

    def __init__(self, world: World) -> None:
        super().__init__()
        self.world = world
        self.components_error: Exception | None = None

    def GetType(self) -> int:  # noqa: N802 - COM name
        return 2

    def GetComponents(self, top_level: bool) -> Any:  # noqa: N802 - COM name
        assert top_level is True
        if self.components_error is not None:
            raise self.components_error
        return self.world.top

    @property
    def InterferenceDetectionManager(self) -> Any:  # noqa: N802 - COM name
        self.world.manager_asked += 1
        return self.world.manager


class App(Com):
    def __init__(self) -> None:
        super().__init__()
        self.utility: MathUtility | None = MathUtility()

    def GetMathUtility(self) -> MathUtility | None:  # noqa: N802 - COM name
        return self.utility


class World:
    """The fake assembly, what was done to its bodies, and its failure switches."""

    def __init__(self) -> None:
        self.top: Any = []
        self.copies = 0
        self.booleans = 0
        self.placements: list[list[float]] = []
        #: Copies that can still be made; None for no limit.
        self.copies_left: int | None = None
        self.move_fails = False
        self.volume_fails = False
        #: What Operations2 raises, or the code it gives with no piece.
        self.boolean_error: Exception | None = None
        self.code: Any = None
        #: Two identical bodies give swBodyOperationSameToolAndTarget.
        self.same_body = False
        #: ``IInterferenceDetectionMgr``, and how often it was asked for.
        self.manager: Any = None
        self.manager_asked = 0
        self.assembly = Assembly(self)
        self.app = App()

    def body(self, *lumps: Box) -> Body:
        return Body(self, list(lumps))

    def component(self, name: str, *bodies: Body, **kwargs: Any) -> Component:
        """A component, not yet in the assembly (a child, or for :meth:`add`)."""
        return Component(self, name, list(bodies), **kwargs)

    def add(self, name: str, *bodies: Body, **kwargs: Any) -> Component:
        """A top-level component."""
        made = self.component(name, *bodies, **kwargs)
        self.top.append(made)
        return made


class Adapter:
    """The PyWin32Adapter members the engine uses."""

    def __init__(self, world: World) -> None:
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
