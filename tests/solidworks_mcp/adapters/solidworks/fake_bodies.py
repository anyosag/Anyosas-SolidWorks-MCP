"""A fake SolidWorks part for the multibody tests: every body is a box.

Each body is a box (mm) with holes: boxes a subtract or an indent took out of
it, so its volume drops while its box stays, as a real body's does. The fakes
refuse a zero-argument method that wasn't flagged first (pitfall #5), and
``IMathUtility.CreateTransform`` too (pitfall #41), so a missing flag fails a
test instead of silently returning a value.

``Operations2`` only takes temporary bodies (``Copy()`` results) and consumes
both, as the API help says; a call on a part's own body fails the test.
"""

from __future__ import annotations

import math
from typing import Any

from solidworks_mcp.adapters.solidworks import bodies as bd
from solidworks_mcp.adapters.solidworks.com_utils import as_list

M = 1000.0


def unwrap(value: Any) -> Any:
    """A VARIANT's value (or the plain value off Windows)."""
    return getattr(value, "value", value)


def faces_of(box: list[float]) -> list[list[float]]:
    """The six faces of a box as flat boxes."""
    faces = []
    for axis in range(3):
        for level in (box[axis], box[axis + 3]):
            lo, hi = list(box[:3]), list(box[3:])
            lo[axis] = hi[axis] = level
            faces.append(lo + hi)
    return faces


class Com:
    """A fake dispatch that records the names flagged as methods."""

    def __init__(self) -> None:
        self.flagged: set[str] = set()

    def _FlagAsMethod(self, *names: str) -> None:  # noqa: N802
        self.flagged.update(names)

    def need(self, name: str) -> None:
        """Fail like late binding does when a zero-argument method isn't flagged."""
        if name not in self.flagged:
            raise TypeError(f"'{name}' was called without being flagged")


class SelectData(Com):
    def __init__(self) -> None:
        super().__init__()
        self.Mark = 0


class Feature(Com):
    """A feature: its name, and what deleting it undoes."""

    def __init__(self, world: World, name: str, undo: Any = None) -> None:
        super().__init__()
        self.world, self.Name, self.undo = world, name, undo

    def Select2(self, append: bool, mark: int) -> bool:  # noqa: N802
        return self.world.select(self, mark, append)


class Face(Com):
    def __init__(self, world: World, body: Body, box_mm: list[float]) -> None:
        super().__init__()
        self.world, self.body, self.box = world, body, box_mm

    def GetBox(self) -> tuple[float, ...]:  # noqa: N802
        self.need("GetBox")
        pad = 0.15
        return tuple(
            [(v - pad) / M for v in self.box[:3]]
            + [(v + pad) / M for v in self.box[3:]]
        )

    def GetSurface(self) -> Any:  # noqa: N802
        self.need("GetSurface")
        surface = Com()
        surface.IsPlane = lambda: True  # type: ignore[attr-defined]
        surface.IsCylinder = lambda: False  # type: ignore[attr-defined]
        return surface

    def Select4(self, append: bool, data: Any) -> bool:  # noqa: N802
        return self.world.select(self, data.Mark if data else 0, append)


class Body(Com):
    """A solid body: a box (mm) less its holes."""

    def __init__(
        self,
        world: World,
        name: str,
        box: list[float],
        holes: list[list[float]] | None = None,
        temp: bool = False,
    ) -> None:
        super().__init__()
        self.world, self.Name = world, name
        self.box = [float(v) for v in box]
        self.holes = [list(h) for h in holes or []]
        self.temp = temp
        self.consumed = False
        self.Visible = True

    @property
    def volume(self) -> float:
        return bd.box_volume(self.box) - sum(
            bd.overlap_volume(h, self.box) for h in self.holes
        )

    def GetBodyBox(self) -> tuple[float, ...]:  # noqa: N802
        self.need("GetBodyBox")
        return tuple(v / M for v in self.box)

    def GetFaceCount(self) -> int:  # noqa: N802
        self.need("GetFaceCount")
        return 6 + 6 * len(self.holes)

    def GetMassProperties(self, density: float) -> tuple[float, ...]:  # noqa: N802
        center = bd.box_center(self.box)
        volume = self.volume / 1e9
        return (
            *(c / M for c in center),
            volume,
            bd.box_area(self.box) / 1e6,
            volume * density,
            *([0.0] * 6),
        )

    def Select2(self, append: bool, data: Any) -> bool:  # noqa: N802
        if self.world.body_select_fails:
            return False
        return self.world.select(self, data.Mark if data else 0, append)

    def Copy(self) -> Body:  # noqa: N802
        self.need("Copy")
        self.world.copies += 1
        return Body(self.world, self.Name, self.box, self.holes, temp=True)

    def ApplyTransform(self, xform: Any) -> bool:  # noqa: N802
        shift = [v * M for v in xform.ArrayData[9:12]]
        self.box = bd.translate_box(self.box, shift)
        self.holes = [bd.translate_box(h, shift) for h in self.holes]
        return True

    def Operations2(self, operation: int, tool: Body, code: Any) -> Any:  # noqa: N802
        assert self.temp and tool.temp, "Operations2 on a part's own body"
        assert not self.consumed and not tool.consumed, "a consumed body reused"
        self.consumed = tool.consumed = True
        self.world.booleans.append(operation)
        return self.world.boolean(self, tool, operation, code)

    def GetFaces(self) -> tuple[Face, ...]:  # noqa: N802
        self.need("GetFaces")
        return tuple(Face(self.world, self, f) for f in faces_of(self.box))


class Transform(Com):
    def __init__(self, data: list[float]) -> None:
        super().__init__()
        self.ArrayData = data


class MathUtility(Com):
    def CreateTransform(self, data: Any) -> Transform:  # noqa: N802
        self.need("CreateTransform")
        return Transform([float(v) for v in unwrap(data)])


class App(Com):
    def __init__(self) -> None:
        super().__init__()
        self.utility = MathUtility()

    def GetMathUtility(self) -> MathUtility:  # noqa: N802
        return self.utility


class SelectionManager(Com):
    def CreateSelectData(self) -> SelectData:  # noqa: N802
        self.need("CreateSelectData")
        return SelectData()


class Extension(Com):
    def __init__(self, world: World) -> None:
        super().__init__()
        self.world = world

    def SelectByID2(self, name: str, kind: str, *rest: Any) -> bool:  # noqa: N802
        self.world.by_id.append((name, kind))
        body = next((b for b in self.world.bodies if b.Name == name), None)
        if body is None or kind != "SOLIDBODY":
            return False
        _x, _y, _z, append, mark, _callout, _option = rest
        return self.world.select(body, mark, append)

    def DeleteSelection2(self, options: int) -> bool:  # noqa: N802
        for entity, _mark in list(self.world.selection):
            self.world.deleted.append(entity.Name)
            if isinstance(entity, Feature) and entity.undo:
                entity.undo()
        self.world.selection.clear()
        return True


class FeatureManager(Com):
    def __init__(self, world: World) -> None:
        super().__init__()
        self.world = world

    def _marked(self, mark: int) -> list[Any]:
        return [e for e, m in self.world.selection if m == mark]

    def InsertCombineFeature(self, op: int, main: Any, tools: Any) -> Any:  # noqa: N802
        w = self.world
        main, tools = unwrap(main), list(unwrap(tools) or [])
        w.combine_calls.append(
            (op, main is not None, len(tools), sorted(m for _e, m in w.selection))
        )
        if w.combine_fails:
            return None
        if tools:
            if not w.combine_direct_works:
                return None
            if op == bd.BODY_CUT:
                target, others = main, tools
            else:
                target, others = tools[0], tools[1:]
        else:
            if (op, tuple(sorted({m for _e, m in w.selection}))) not in w.combine_marks:
                return None
            if op == bd.BODY_CUT:
                target, others = self._marked(1)[0], self._marked(2)
            elif self._marked(1):
                target, others = self._marked(1)[0], self._marked(2)
            else:
                target, others = self._marked(2)[0], self._marked(2)[1:]
        return w.combine(op, target, others)

    def InsertMoveCopyBody2(self, *args: Any) -> Any:  # noqa: N802
        return self.world.move(self._marked(1), *args)

    def InsertDeleteBody2(self, keep: bool) -> Any:  # noqa: N802
        return self.world.delete_keep(self._marked(0), keep)

    def PreSplitBody2(self) -> Any:  # noqa: N802
        self.need("PreSplitBody2")
        return self.world.pre_split()

    def PostSplitBody2(self, pieces: Any, consume: bool, *rest: Any) -> Any:  # noqa: N802
        origins, paths, template = (unwrap(v) for v in rest)
        assert consume is False and template == ""
        assert list(origins) == [None] * len(origins)
        assert list(paths) == [""] * len(paths)
        return self.world.post_split(list(unwrap(pieces)))

    def InsertIndent(self, *args: Any) -> Any:  # noqa: N802
        return self.world.indent(self._marked(1), self._marked(4), args)

    def InsertFeatureTreeFolder2(self, kind: int) -> Any:  # noqa: N802
        assert kind == bd.FOLDER_CONTAINING
        w = self.world
        w.folders.append([e.Name for e, _m in w.selection])
        return Feature(w, f"Folder{len(w.folders)}") if not w.folder_fails else None


class Model(Com):
    """The part document."""

    def __init__(self, world: World) -> None:
        super().__init__()
        self.world = world
        self.SelectionManager = SelectionManager()
        self.Extension = Extension(world)
        self.FeatureManager = FeatureManager(world)

    def GetType(self) -> int:  # noqa: N802
        return self.world.doc_type

    def GetPathName(self) -> str:  # noqa: N802
        return self.world.path

    def GetBodies2(self, kind: int, visible_only: bool) -> Any:  # noqa: N802
        assert kind == bd.SOLID_BODY
        found = [b for b in self.world.bodies if b.Visible or not visible_only]
        return tuple(found) or None

    def ClearSelection2(self, everything: bool) -> None:  # noqa: N802
        self.need("ClearSelection2")
        self.world.selection.clear()

    def EditRebuild3(self) -> bool:  # noqa: N802
        self.need("EditRebuild3")
        self.world.rebuilds += 1
        return True

    def FeatureByName(self, name: str) -> Any:  # noqa: N802
        return self.world.named.get(name)

    def InsertPart3(self, path: str, options: int, configuration: str) -> Any:  # noqa: N802
        return self.world.insert_part(path, options, configuration)

    def CreateFeatureFromBody3(self, body: Body, ref: bool, options: int) -> Any:  # noqa: N802
        return self.world.feature_from_body(body, options)

    def ClosestDistance(self, a: Face, b: Face, p1: Any, p2: Any) -> float:  # noqa: N802
        self.world.distances += 1
        point_a, point_b = bd.nearest_points(a.box, b.box)
        p1.value = tuple(v / M for v in point_a)
        p2.value = tuple(v / M for v in point_b)
        return math.dist(point_a, point_b) / M


class World:
    """The fake part: its bodies, selection, features and switches."""

    def __init__(self, bodies: dict[str, list[float]] | None = None) -> None:
        self.bodies: list[Body] = [
            Body(self, name, box) for name, box in (bodies or {}).items()
        ]
        self.selection: list[tuple[Any, int]] = []
        self.doc_type = 1
        self.path = "C:/parts/lid.SLDPRT"
        self.counts: dict[str, int] = {}
        self.copies = 0
        self.booleans: list[int] = []
        self.rebuilds = 0
        self.distances = 0
        self.deleted: list[str] = []
        self.folders: list[list[str]] = []
        self.by_id: list[tuple[str, str]] = []
        self.combine_calls: list[tuple[int, bool, int, list[int]]] = []
        # Which preselected forms make a Combine: (operation, set of marks).
        self.combine_marks = {
            (bd.BODY_CUT, (1, 2)),
            (bd.BODY_ADD, (2,)),
            (bd.BODY_INTERSECT, (2,)),
        }
        self.combine_direct_works = False
        self.combine_fails = False
        self.body_select_fails = False
        self.add_fails = False
        self.folder_fails = False
        self.from_body_needs_no_check = False
        self.from_body_fails = False
        self.move_fails = False
        self.delete_fails = False
        self.split_fails = False
        #: Indent: None makes no feature; "cut" cuts the grown tools; "nothing"
        #: makes a feature that removes nothing.
        self.indent_result: str | None = None
        self.indent_forms: list[list[str]] = []
        self.insertable: dict[str, dict[str, list[float]]] = {}
        self.named: dict[str, Any] = {}
        self.model = Model(self)
        self.app = App()

    # -- helpers --------------------------------------------------------------

    def feature(self, stem: str, undo: Any = None) -> Feature:
        self.counts[stem] = self.counts.get(stem, 0) + 1
        return Feature(self, f"{stem}{self.counts[stem]}", undo)

    def body(self, name: str) -> Body:
        return next(b for b in self.bodies if b.Name == name)

    def select(self, entity: Any, mark: int, append: bool) -> bool:
        if not append:
            self.selection.clear()
        self.selection.append((entity, mark))
        return True

    def add_plane(self, name: str, axis: int, at_mm: float) -> None:
        plane = Feature(self, name)
        plane.split = (axis, at_mm)  # type: ignore[attr-defined]
        self.named[name] = plane

    # -- temporary bodies --------------------------------------------------------

    def boolean(self, a: Body, b: Body, op: int, code: Any) -> Any:
        code.value = bd.NO_ERROR
        shared = bd.box_overlap(a.box, b.box)
        if op == bd.BODY_ADD:
            if self.add_fails:
                code.value = 1058
                return None
            if shared is None:
                return (
                    Body(self, a.Name, a.box, a.holes, True),
                    Body(self, b.Name, b.box, b.holes, True),
                )
            return (Body(self, a.Name, bd.union_box(a.box, b.box), temp=True),)
        assert op == bd.BODY_INTERSECT
        if a.box == b.box and a.holes == b.holes:
            code.value = bd.SAME_TOOL_AND_TARGET
            return None
        if shared is None:
            code.value = bd.NO_INTERSECT
            return None
        lost = sum(bd.overlap_volume(h, shared) for h in a.holes + b.holes)
        if bd.box_volume(shared) - lost <= 1e-9:
            code.value = bd.NO_INTERSECT
            return None
        piece = Body(self, a.Name, shared, a.holes + b.holes, temp=True)
        return (piece,)

    # -- features -------------------------------------------------------------------

    def combine(self, op: int, target: Body, others: list[Body]) -> Feature:
        before = (list(target.box), [list(h) for h in target.holes])
        removed = list(others)
        for other in others:
            if op == bd.BODY_CUT:
                target.holes.append(list(other.box))
            elif op == bd.BODY_ADD:
                target.box = bd.union_box(target.box, other.box)
            else:
                target.box = bd.box_overlap(target.box, other.box) or target.box
            self.bodies.remove(other)

        def undo() -> None:
            target.box, target.holes = before
            self.bodies.extend(removed)

        return self.feature("Combine", undo)

    def move(self, bodies: list[Body], *args: Any) -> Any:
        if self.move_fails:
            return None
        tx, ty, tz, _d, px, py, pz, ax, ay, az, copy, copies = args
        feature = self.feature("Body-Move/Copy")
        shift = [tx * M, ty * M, tz * M]
        angles = [math.degrees(v) for v in (ax, ay, az)]
        turned = next((("xyz"[k], a) for k, a in enumerate(angles) if a), None)

        def placed(box: list[float], k: int) -> list[float]:
            out = bd.translate_box(box, [k * s for s in shift])
            if turned:
                out = bd.rotate_box(
                    out, turned[0], k * turned[1], [px * M, py * M, pz * M]
                )
            return [round(v, 9) for v in out]

        made = 0
        for body in bodies:
            if not copy:
                body.box = placed(body.box, 1)
                continue
            for k in range(1, copies + 1):
                made += 1
                self.bodies.append(
                    Body(self, f"{feature.Name}[{made}]", placed(body.box, k))
                )
        return feature

    def delete_keep(self, bodies: list[Body], keep: bool) -> Any:
        if self.delete_fails:
            return None
        gone = [b for b in self.bodies if (b in bodies) != keep]
        for body in gone:
            self.bodies.remove(body)
        return self.feature("Body-Delete/Keep")

    def pre_split(self) -> Any:
        planes = [e for e, _m in self.selection if hasattr(e, "split")]
        if not planes or self.split_fails:
            return None
        axis, at = planes[0].split
        pieces = []
        for body in self.bodies:
            halves = bd.split_box(body.box, axis, at)
            for half in halves or ():
                piece = Body(self, "", half, temp=True)
                piece.source = body  # type: ignore[attr-defined]
                pieces.append(piece)
        return tuple(pieces) or None

    def post_split(self, pieces: list[Body]) -> Feature:
        feature = self.feature("Split")
        for k, piece in enumerate(pieces, start=1):
            source = piece.source  # type: ignore[attr-defined]
            if source in self.bodies:
                self.bodies.remove(source)
            self.bodies.append(Body(self, f"{feature.Name}[{k}]", piece.box))
        return feature

    def indent(self, targets: list[Body], tools: list[Any], args: tuple) -> Any:
        thickness, clearance, exclude, clr_dir, cut, cut_dir = args
        assert (thickness, exclude, clr_dir, cut, cut_dir) == (
            bd.INDENT_THICKNESS_M,
            False,
            True,
            True,
            False,
        )
        self.indent_forms.append([type(t).__name__ for t in tools])
        if self.indent_result is None:
            return None
        target = targets[0]
        before = [list(h) for h in target.holes]
        if self.indent_result == "cut":
            for tool in tools:
                body = tool.body if isinstance(tool, Face) else tool
                target.holes.append(bd.grow_box(body.box, clearance * M))

        def undo() -> None:
            target.holes = before

        return self.feature("Indent", undo)

    def insert_part(self, path: str, options: int, configuration: str) -> Any:
        self.inserted = (path, options, configuration)  # type: ignore[attr-defined]
        given = self.insertable.get(path)
        if given is None:
            return None
        for name, box in given.items():
            self.bodies.append(Body(self, name, box))
        return self.feature("holder-")

    def feature_from_body(self, body: Body, options: int) -> Any:
        if self.from_body_fails or (self.from_body_needs_no_check and options != 0):
            return None
        made = Body(self, "", body.box, body.holes)
        feature = self.feature("Imported")
        made.Name = feature.Name
        self.bodies.append(made)

        def undo() -> None:
            if made in self.bodies:
                self.bodies.remove(made)

        feature.undo = undo
        return feature


class Harness(bd.SolidWorksBodiesMixin):
    """The mixin plus the PyWin32Adapter members it relies on."""

    def __init__(self, world: World | None) -> None:
        self.world = world
        self.currentModel = world.model if world else None
        self.swApp = world.app if world else None

    def _handle_com_operation(self, name: str, operation: Any, *args: Any) -> Any:
        from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus

        try:
            data = operation(*args)
        except Exception as exc:  # noqa: BLE001 - mirrors PyWin32Adapter
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=f"Error in {name}: {exc}"
            )
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=data)

    def _attempt(self, operation: Any, default: Any = None) -> Any:
        try:
            return operation()
        except Exception:  # noqa: BLE001
            return default

    def _attempt_with_error(self, operation: Any) -> tuple[Any, Exception | None]:
        try:
            return operation(), None
        except Exception as exc:  # noqa: BLE001
            return None, exc

    def _get_attr_or_call(self, obj: Any, name: str) -> Any:
        value = getattr(obj, name)
        return value() if callable(value) else value


def names(world: World) -> list[str]:
    return [b.Name for b in world.bodies]


__all__ = ["Harness", "World", "as_list", "names", "unwrap"]
