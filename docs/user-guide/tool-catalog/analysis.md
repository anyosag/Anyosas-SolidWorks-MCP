# Analysis Tools

Extract engineering properties from models: mass, volume, centre of mass, moments of inertia, interference between solids, geometry curvature and thickness, and material properties. Essential for design validation.

> **Prerequisite:** An active model (part or assembly) with solid geometry.

**Total tools in this category: 11**

---

### `calculate_mass_properties`

Measure the active part or assembly's mass properties: volume, surface area, mass, centre of mass and moments of inertia.

**Prerequisite:** Active model with solid geometry

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `density_g_cm3` | `float?` | — | `None` | Also give `mass_at_density`: the measured volume times this density, g/cm³ (a resin at 1.15), for a part with no material. SolidWorks' own `mass` is kept beside it; `set_density` makes SolidWorks use it |

**Sample call:**

```json
{}
```

---

### `get_mass_properties`

Measure the active part or assembly's mass properties: volume, surface area, mass, centre of mass and moments of inertia.

**Prerequisite:** Active model with solid geometry

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `density_g_cm3` | `float?` | — | `None` | Also give `mass_at_density`: the measured volume times this density, g/cm³ (a resin at 1.15), for a part with no material. SolidWorks' own `mass` is kept beside it; `set_density` makes SolidWorks use it |

**Sample call:**

```json
{}
```

---

### `check_interference`

Find where the components of the active assembly overlap (Tools > Interference Detection).

**Prerequisite:** Active assembly

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `assembly_path` | `str?` | — | `None` | Assembly path alias |
| `include_hidden` | `bool` | — | `False` | Check hidden components too; by default they are left out, as in SolidWorks' own Interference Detection |
| `components` | `List[str]` | ✅ | `` | List of component names to check for interference |

**Sample call:**

```json
{
  "components": []
}
```

---

### `analyze_geometry`

Run a geometry analysis on the active model, such as curvature, draft or wall thickness.

**Prerequisite:** Active model with geometry

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `analysis_type` | `str` | ✅ | `` | Type of analysis (curvature, draft, thickness, etc.) |
| `parameters` | `Dict[str, Any]?` | — | `None` | Analysis-specific parameters |

**Sample call:**

```json
{
  "analysis_type": "curvature"
}
```

---

### `check_wall_thickness`

Measure the active part's thinnest walls before 3D printing it.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `min_wall_mm` | `float` | — | `0.8` | The thinnest wall the printer can make, mm (0.8 is two 0.4 mm nozzle lines); thinner spots are listed |
| `max_rays` | `int` | — | `5000` | The most points to measure; more finds thin spots on big parts, slower (default 5000) |
| `exclude_boxes_mm` | `List[Annotated[List[float], Field(min_length=6, max_length=6)]]?` | — | `None` | Regions meant to be thin, such as a snap-fit flexure: each box is [x1, y1, z1, x2, y2, z2], two opposite corners in the part's coordinates, mm. Points inside a box (edges included) don't count against ok, min_thickness_mm or thin_spots; data.excluded reports them |

**Sample call:**

```json
{}
```

---

### `check_printability`

Check the active part for 3D printing (resin or FDM): overhangs, islands, bridges, suction cups; optionally the best orientation and thin features.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `build_direction` | `str | List[float]?` | — | `None` | The direction the part grows away from the build plate: 'x', 'y', 'z', '-x', '-y', '-z' or [x, y, z]. A part printed upright on its base, with Y up in the model, is 'y'. Leave it out with `orientations` to check in the best one found |
| `orientations` | `int?` | — | `None` | Try this many build directions (the six axes, the biggest flat faces on the plate, then directions in between; 26 is a good start) and report the one needing the least support as `orientations.best`, with `rotate_to_z`, the turn that makes it +Z for a slicer |
| `min_feature_mm` | `float?` | — | `None` | Also list the regions thinner than this, mm (the printer's smallest feature, e.g. 0.4 on resin), from rays cast inward on the mesh: `thin_features` |
| `support_angle_deg` | `float` | — | `45.0` | Surfaces facing the plate at less than this many degrees from horizontal need supports (default 45) |
| `min_bridge_mm` | `float` | — | `5.0` | Only flat overhangs held at both ends longer than this are listed, mm |

**Sample call:**

```json
{}
```

---

### `rotation_clearance`

Turn a body or component about an axis through a point, over an angle range, and find the smallest gap and where it first hits.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `moving` | `str` | ✅ | `` | What turns: in a part, a body as `list_bodies` names it; in an assembly, a component as `list_components` names it (e.g. 'Lid-1') |
| `other` | `str` | ✅ | `` | What it must clear: another body or component (e.g. 'Base-1') |
| `axis` | `str | List[float]` | ✅ | `` | The direction of the turn's axis in the part's or assembly's coordinates: 'x', 'y', 'z', '-x', '-y', '-z' or [x, y, z]. Positive angles turn by the right-hand rule about it |
| `pivot_mm` | `List[float]` | ✅ | `` | A point on the axis, [x, y, z] mm: a point on the hinge pin's axis; the origin by default |
| `range_deg` | `List[float]` | ✅ | `` | [from, to] degrees, from at most 0 to at least 0: 0 is where it is now. A lid that only opens: [0, 180]. Default [-180, 180] |
| `step_deg` | `float` | — | `5.0` | The coarse step, degrees (default 5). Contacts and the closest pass are then narrowed to tolerance_deg. Smaller finds thinner walls: the reply's travel_per_step_mm is how far a point moves per step |
| `tolerance_deg` | `float` | — | `0.1` | How finely contact angles and the closest pass are found, degrees |

**Sample call:**

```json
{
  "moving": "example",
  "other": "example",
  "axis": "example",
  "pivot_mm": [],
  "range_deg": []
}
```

---

### `volume_by_plane`

Measure the active part's volume, and mass, above and below a plane, without cutting it.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `plane` | `str?` | — | `None` | A reference plane's name, e.g. 'Top Plane' or 'Fill line' (Top, Front and Right work too). `above` is the side its normal points to: +Y for Top Plane |
| `point_mm` | `List[float]?` | — | `None` | Instead of plane: a point on it, [x, y, z] mm |
| `normal` | `str | List[float]?` | — | `None` | With point_mm: the plane's normal, 'x', 'y', 'z', '-x', '-y', '-z' or [x, y, z]; `above` is the side it points to |
| `bodies` | `List[str]?` | — | `None` | Only these bodies, as `list_bodies` names them; all by default |
| `enclosed` | `bool` | — | `False` | Also measure the space the bodies enclose on each side, open only at the plane: what a cup holds filled to that line (`enclosed_mm3`, `enclosed_ml`). Slower |
| `density_g_cm3` | `float?` | — | `None` | The density for the masses, g/cm³ (a resin at 1.15); the part's own (its material or `set_density`) when omitted |

**Sample call:**

```json
{}
```

---

### `query_geometry`

List the active part's faces and edges within a radius of a point, nearest first, with their geometry.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `point_mm` | `List[float]` | ✅ | `` | The point, [x, y, z] in the part's mm (on a face, or in a hole) |
| `radius_mm` | `float` | — | `2.0` | List what lies within this many mm of the point (default 2) |
| `kinds` | `Literal['faces', 'edges', 'both']` | — | `both` | 'faces', 'edges' or 'both' (the default) |
| `limit` | `int` | — | `10` | The most faces, and the most edges, listed, nearest first |
| `read_all` | `bool` | — | `False` | Read every face box near the point so nothing within radius_mm is missed (complete: true). Costs about two calls a face the first time; kept until the next rebuild, so later queries are fast |

**Sample call:**

```json
{
  "point_mm": []
}
```

---

### `get_material_properties`

Report the physical properties of the active part's material: density, modulus, Poisson's ratio, strength.

**Prerequisite:** Active part with assigned material

**Sample call:**

```json
{}
```

---

### `verify_model`

Check the active part or assembly against what you meant to build, one pass/fail check per expected value.

**Prerequisite:** SolidWorks running

**Sample call:**

```json
{}
```

---
