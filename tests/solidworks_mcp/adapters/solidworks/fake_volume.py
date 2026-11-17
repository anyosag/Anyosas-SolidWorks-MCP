"""A fake part for ``volume_by_plane`` and ``set_density``: boxes with holes.

Builds on ``fake_bodies``: every body is a box (mm) less its holes. Added here:

* ``IModeler.CreateBodyFromBox3`` (flagged first) for a cube along a model
  axis. Its centre argument is the *base's* centre when ``base_centre`` is on
  (else the box's), so the code must find where the cube really is;
* ``Operations2`` cut: the target less the tool's box, plus each of the tool's
  holes the target reaches as a piece of its own (the pocket a cup leaves);
* ``IBody2.GetExtremePoint`` through byref doubles;
* named reference planes (``FeatureByName``, ``GetTypeName2``,
  ``GetSpecificFeature2().Transform.ArrayData``);
* ``CreateMassProperty`` (``Density``, ``Mass``, ``Volume``) and the
  document density preference, and the part's material.
"""

from __future__ import annotations

from typing import Any

from solidworks_mcp.adapters.solidworks import bodies as bd

from . import fake_bodies as fb

M = 1000.0


class Modeler(fb.Com):
    def __init__(self, world: VolumeWorld) -> None:
        super().__init__()
        self.world = world

    def CreateBodyFromBox3(self, data: Any) -> Any:  # noqa: N802
        self.need("CreateBodyFromBox3")
        w = self.world
        if w.box_fails:
            return None
        values = [float(v) for v in fb.unwrap(data)]
        centre = [v * M for v in values[0:3]]
        axis = values[3:6]
        side = values[6] * M
        along = next(k for k in range(3) if abs(axis[k]) > 0.5)
        lo = [c - side / 2 for c in centre]
        hi = [c + side / 2 for c in centre]
        if w.base_centre:
            sign = 1.0 if axis[along] > 0 else -1.0
            lo[along] = centre[along] + (0.0 if sign > 0 else -side)
            hi[along] = lo[along] + side
        w.cubes += 1
        cube = fb.Body(w, "", lo + hi, temp=True)
        if w.cube_mass_fails:
            cube.GetMassProperties = lambda density: ()  # type: ignore[method-assign]
        if w.cube_move_fails:
            cube.ApplyTransform = lambda xform: False  # type: ignore[method-assign]
        return cube


class VolumeApp(fb.App):
    def __init__(self, world: VolumeWorld) -> None:
        super().__init__()
        self.modeler = Modeler(world)
        self.world = world

    def GetModeler(self) -> Any:  # noqa: N802
        self.need("GetModeler")
        return None if self.world.modeler_fails else self.modeler


class MassProperty:
    """``IMassProperty``. As on SOLIDWORKS 2026 SP4.1 through late binding,
    ``Mass`` read after ``Volume`` on the same object gives the volume."""

    def __init__(self, world: VolumeWorld) -> None:
        volume = sum(b.volume for b in world.bodies) / 1e9
        density = world.mass_density()
        self._volume = volume
        self._mass = volume * (density or 0.0)
        self._volume_read = False
        self.Density = 0.0 if world.density_hidden else (density or 0.0)

    @property
    def Volume(self) -> float:  # noqa: N802
        self._volume_read = True
        return self._volume

    @property
    def Mass(self) -> float:  # noqa: N802
        return self._volume if self._volume_read else self._mass


class VolumeExtension(fb.Extension):
    def CreateMassProperty(self) -> Any:  # noqa: N802
        self.need("CreateMassProperty")
        w = self.world
        return None if w.mass_fails else MassProperty(w)

    def SetUserPreferenceDouble(self, pref: int, option: int, value: float) -> bool:  # noqa: N802
        self.need("SetUserPreferenceDouble")
        self.world.preferences.append((pref, option, value))
        if self.world.preference_refused:
            return False
        if not self.world.preference_ignored:
            self.world.document_density = value
        return True

    def GetUserPreferenceDouble(self, pref: int, option: int) -> float:  # noqa: N802
        self.need("GetUserPreferenceDouble")
        return self.world.document_density


class RefPlane:
    def __init__(self, origin_m: list[float], normal: list[float]) -> None:
        x = [1.0, 0.0, 0.0] if abs(normal[0]) < 0.9 else [0.0, 1.0, 0.0]
        self.Transform = fb.Transform([*x, 0.0, 0.0, 0.0, *normal, *origin_m, 1.0])


class PlaneFeature(fb.Com):
    def __init__(self, kind: str, plane: RefPlane | None) -> None:
        super().__init__()
        self.kind, self.plane = kind, plane

    def GetTypeName2(self) -> str:  # noqa: N802
        self.need("GetTypeName2")
        return self.kind

    def GetSpecificFeature2(self) -> Any:  # noqa: N802
        self.need("GetSpecificFeature2")
        return self.plane


class VolumeModel(fb.Model):
    def __init__(self, world: VolumeWorld) -> None:
        super().__init__(world)
        self.Extension = VolumeExtension(world)

    def GetMaterialPropertyName2(self, configuration: str, database: Any) -> Any:  # noqa: N802
        name, library = self.world.material
        database.value = library
        return name

    def SetMaterialPropertyName2(
        self, configuration: str, database: str, name: str
    ) -> None:  # noqa: N802
        self.world.material_calls.append((configuration, database, name))
        if not self.world.material_stuck:
            self.world.material = (name, database)


class Piece(fb.Body):
    """What a cut leaves: a temporary body that knows its extreme points."""

    def GetExtremePoint(self, *args: Any) -> bool:  # noqa: N802
        self.need("GetExtremePoint")
        if self.world.extreme_fails:
            return False
        direction, out = args[:3], args[3:]
        for k in range(3):
            out[k].value = (self.box[k + 3] if direction[k] > 0 else self.box[k]) / M
        return True


class VolumeWorld(fb.World):
    """A part of boxes, with planes, a modeler, mass properties and a material."""

    def __init__(self, bodies: dict[str, list[float]] | None = None) -> None:
        super().__init__(bodies)
        self.model = VolumeModel(self)
        self.app = VolumeApp(self)
        self.base_centre = True
        self.cubes = 0
        self.box_fails = False
        self.modeler_fails = False
        self.cube_mass_fails = False
        self.cube_move_fails = False
        self.extreme_fails = False
        self.cut_raises = False
        self.mass_fails = False
        self.density_hidden = False
        #: The material assigned (name, library); empty when none.
        self.material: tuple[str, str] = ("", "")
        self.material_density: float | None = None
        self.material_stuck = False
        self.material_calls: list[tuple[str, str, str]] = []
        self.document_density = 1000.0
        self.preferences: list[tuple[int, int, float]] = []
        self.preference_refused = False
        self.preference_ignored = False

    def mass_density(self) -> float | None:
        """The density mass properties use (kg/m3)."""
        if self.material[0]:
            return self.material_density
        return self.document_density

    def add_plane(self, name: str, origin_mm: list[float], normal: list[float]) -> None:  # type: ignore[override]
        self.named[name] = PlaneFeature(
            "RefPlane", RefPlane([v / M for v in origin_mm], normal)
        )

    def add_feature(self, name: str, kind: str, plane: RefPlane | None = None) -> None:
        self.named[name] = PlaneFeature(kind, plane)

    def boolean(self, a: fb.Body, b: fb.Body, op: int, code: Any) -> Any:
        if op != bd.BODY_CUT:
            return super().boolean(a, b, op, code)
        if self.cut_raises:
            raise RuntimeError("the cut failed")
        shared = bd.box_overlap(a.box, b.box)
        if shared is None:
            code.value = bd.NO_INTERSECT
            return None
        code.value = bd.NO_ERROR
        pieces = [Piece(self, "", a.box, a.holes + [shared], temp=True)]
        for hole in b.holes:
            pocket = bd.box_overlap(hole, a.box)
            if pocket is not None:
                pieces.append(Piece(self, "", pocket, temp=True))
        return tuple(pieces)
