# Sketching Tools

Build 2-D sketch geometry (lines, circles, arcs, splines, polygons) and apply geometric constraints and driven dimensions. Sketches are the prerequisite for every extrusion, revolve, sweep, and loft feature.

> **Prerequisite:** An active part document with an active sketch edit session.

**Total tools in this category: 25**

---

### `create_sketch`

Open a new sketch on a plane of the active part, ready for lines, circles and other sketch entities.

**Prerequisite:** Active part document, no active sketch

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `plane` | `str` | ✅ | `` | Plane to sketch on: 'Front' (or 'XY'), 'Top' ('XZ'), 'Right' ('YZ'), or the exact name of a plane in the part, e.g. 'Plane1' |
| `sketch_name` | `str?` | — | `None` | Name to give the sketch in the feature tree, applied when exit_sketch closes it; omit to keep SolidWorks' own (Sketch1, Sketch2, ...) |

**Sample call:**

```json
{
  "plane": "Front"
}
```

---

### `add_line`

Draw a straight line between two points in the open sketch, in mm.

**Prerequisite:** Active sketch edit mode

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `x1` | `float?` | — | `None` | Start point X coordinate in mm |
| `y1` | `float?` | — | `None` | Start point Y coordinate in mm |
| `x2` | `float?` | — | `None` | End point X coordinate in mm |
| `y2` | `float?` | — | `None` | End point Y coordinate in mm |
| `start_x` | `float?` | — | `None` | Start point X alias |
| `start_y` | `float?` | — | `None` | Start point Y alias |
| `end_x` | `float?` | — | `None` | End point X alias |
| `end_y` | `float?` | — | `None` | End point Y alias |
| `z1` | `float?` | — | `None` | Start point Z in model mm; only with coordinates 'model' |
| `z2` | `float?` | — | `None` | End point Z in model mm; only with coordinates 'model' |
| `coordinates` | `Coordinates` | — | `sketch` | _COORDINATES |
| `construction` | `bool` | — | `False` | Construction geometry flag |

**Sample call:**

```json
{}
```

---

### `add_circle`

Draw a circle in the open sketch from its centre and radius, in mm.

**Prerequisite:** Active sketch edit mode

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `center_x` | `float` | ✅ | `` | Circle center X coordinate in mm |
| `center_y` | `float` | ✅ | `` | Circle center Y coordinate in mm |
| `center_z` | `float?` | — | `None` | Center Z in model mm; only with coordinates 'model' |
| `radius` | `float` | ✅ | `` | Circle radius in mm |
| `coordinates` | `Coordinates` | — | `sketch` | _COORDINATES |
| `construction` | `bool` | — | `False` | Construction geometry flag |

**Sample call:**

```json
{
  "center_x": 0.0,
  "center_y": 0.0,
  "radius": 15.0
}
```

---

### `add_rectangle`

Draw a closed, axis-aligned rectangle in the open sketch from two opposite corners, in mm.

**Prerequisite:** Active sketch edit mode

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `x1` | `float?` | — | `None` | First corner X coordinate in mm |
| `y1` | `float?` | — | `None` | First corner Y coordinate in mm |
| `x2` | `float?` | — | `None` | Opposite corner X coordinate in mm |
| `y2` | `float?` | — | `None` | Opposite corner Y coordinate in mm |
| `corner1_x` | `float?` | — | `None` | Corner 1 X alias |
| `corner1_y` | `float?` | — | `None` | Corner 1 Y alias |
| `corner2_x` | `float?` | — | `None` | Corner 2 X alias |
| `corner2_y` | `float?` | — | `None` | Corner 2 Y alias |
| `z1` | `float?` | — | `None` | First corner Z in model mm; only with coordinates 'model' |
| `z2` | `float?` | — | `None` | Opposite corner Z in model mm; only with coordinates 'model' |
| `coordinates` | `Coordinates` | — | `sketch` | _COORDINATES + '; the sides still run along sketch x and y' |
| `construction` | `bool` | — | `False` | Construction geometry flag |

**Sample call:**

```json
{}
```

---

### `exit_sketch`

Close the open sketch so a feature can use it.

**Prerequisite:** Active sketch edit mode

**Sample call:**

```json
{}
```

---

### `check_sketch_fully_defined`

Check whether a sketch is fully defined, under-defined or over-defined.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `sketch_name` | `str?` | — | `None` | Optional sketch name. If omitted, checks the active or last sketch. |

**Sample call:**

```json
{}
```

---

### `add_arc`

Draw a circular arc in the open sketch from its centre, start point and end point, in mm.

**Prerequisite:** Active sketch edit mode

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `center_x` | `float` | ✅ | `` | Arc center X coordinate in mm |
| `center_y` | `float` | ✅ | `` | Arc center Y coordinate in mm |
| `start_x` | `float` | ✅ | `` | Arc start point X coordinate in mm |
| `start_y` | `float` | ✅ | `` | Arc start point Y coordinate in mm |
| `end_x` | `float` | ✅ | `` | Arc end point X coordinate in mm |
| `end_y` | `float` | ✅ | `` | Arc end point Y coordinate in mm |
| `center_z` | `float?` | — | `None` | Center Z in model mm; only with coordinates 'model' |
| `start_z` | `float?` | — | `None` | Start Z in model mm; only with coordinates 'model' |
| `end_z` | `float?` | — | `None` | End Z in model mm; only with coordinates 'model' |
| `coordinates` | `Coordinates` | — | `sketch` | _COORDINATES |
| `direction` | `Literal['ccw', 'cw']` | — | `ccw` | Which way the arc runs from start to end, looking at the sketch (x right, y up; in model coordinates, looking against its normal): 'ccw' counter-clockwise (the default) or 'cw' |

**Sample call:**

```json
{
  "center_x": 0.0,
  "center_y": 0.0,
  "start_x": -30.0,
  "start_y": 0.0,
  "end_x": 30.0,
  "end_y": 0.0
}
```

---

### `add_outlines`

Draw whole outlines of lines and arcs in the open sketch in one call, in mm.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `outlines` | `List[Outline]` | ✅ | `` | One or more outlines, each a closed loop by default. Give each either 'points' (a polygon) or 'start' plus 'segments' (lines and arcs) |
| `coordinates` | `Coordinates` | — | `sketch` | _COORDINATES + "; every point is then [x, y, z], an arc's centre too, and an arc's direction is as seen looking against the normal" |

**Sample call:**

```json
{
  "outlines": []
}
```

---

### `add_sketch_text`

Write text in the open sketch, for a label to raise or cut into the part.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `text` | `str` | ✅ | `` | The text to write, e.g. 'MAX' or 'DIPPER v2' |
| `x` | `float` | — | `0.0` | Where the text starts (its left end), sketch x in mm |
| `y` | `float` | — | `0.0` | Where the text starts (its baseline), sketch y in mm |
| `height` | `float?` | — | `None` | Character height in mm; omit for the document's text height |
| `font` | `str?` | — | `None` | Font name, e.g. 'Arial'; omit for the document's. A plain sans-serif font extrudes most reliably |
| `angle` | `float` | — | `0.0` | Degrees counter-clockwise from sketch x: 90 reads upwards. The text follows a construction line drawn at that angle |
| `bold` | `bool` | — | `False` | Bold characters |
| `italic` | `bool` | — | `False` | Italic characters |

**Sample call:**

```json
{
  "text": "example"
}
```

---

### `edit_sketch`

Open an existing sketch for editing, listing its segments with ids.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `name` | `str` | ✅ | `` | The sketch to open, e.g. 'Sketch3' |

**Sample call:**

```json
{
  "name": "MCP_Demo_Part"
}
```

---

### `get_sketch`

Read a sketch's segments, plane and text without opening it for editing.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `name` | `str` | ✅ | `` | The sketch to read, e.g. 'Sketch3' |
| `detail` | `Literal['full', 'summary']` | — | `full` | 'full' (the default): every segment with its points. 'summary': counts by type, circles as centre and radius, the dimensions (name, value), the relations and whether it is fully defined; fewer reads per segment, for large sketches |
| `limit` | `int` | ✅ | `` |  |
| `offset` | `int` | ✅ | `` |  |

**Sample call:**

```json
{
  "name": "MCP_Demo_Part",
  "limit": 10.0,
  "offset": 10.0
}
```

---

### `plane_axes`

Read where a sketch on a plane points: its x, y, normal and origin in the model.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `plane` | `str` | ✅ | `` | The plane: 'Front' ('XY'), 'Top' ('XZ'), 'Right' ('YZ'), or a plane's name, e.g. 'Plane3' |

**Sample call:**

```json
{
  "plane": "Front"
}
```

---

### `delete_sketch_entities`

Delete lines, arcs and other entities of the open sketch by id, or all of them.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `ids` | `List[str]?` | — | `None` | Ids of the entities to delete, from the latest edit_sketch reply or the add_* tools since (ids change each time a sketch is opened) |
| `all` | `bool` | — | `False` | true deletes every entity of the open sketch, ids or not; give it instead of ids |

**Sample call:**

```json
{}
```

---

### `add_spline`

Draw a smooth spline through a list of points in the open sketch, in mm.

**Prerequisite:** Active sketch edit mode

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `points` | `List[Dict[str, float]]` | ✅ | `` | List of spline control points with x, y coordinates |

**Sample call:**

```json
{
  "points": [
    {
      "x": 0.0,
      "y": 0.0
    },
    {
      "x": 10.0,
      "y": 15.0
    },
    {
      "x": 20.0,
      "y": 10.0
    }
  ]
}
```

---

### `add_centerline`

Draw a centreline (construction line) in the open sketch, in mm.

**Prerequisite:** Active sketch edit mode

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `x1` | `float?` | — | `None` | Start point X coordinate in mm |
| `y1` | `float?` | — | `None` | Start point Y coordinate in mm |
| `x2` | `float?` | — | `None` | End point X coordinate in mm |
| `y2` | `float?` | — | `None` | End point Y coordinate in mm |
| `start_x` | `float?` | — | `None` | Start point X alias |
| `start_y` | `float?` | — | `None` | Start point Y alias |
| `end_x` | `float?` | — | `None` | End point X alias |
| `end_y` | `float?` | — | `None` | End point Y alias |
| `z1` | `float?` | — | `None` | Start point Z in model mm; only with coordinates 'model' |
| `z2` | `float?` | — | `None` | End point Z in model mm; only with coordinates 'model' |
| `coordinates` | `Coordinates` | — | `sketch` | _COORDINATES |
| `construction` | `bool` | — | `False` | Construction geometry flag |

**Sample call:**

```json
{}
```

---

### `add_polygon`

Draw a regular polygon (e.g. a hexagon) in the open sketch from its centre, radius and number of sides.

**Prerequisite:** Active sketch edit mode

**Sample call:**

```json
{}
```

---

### `add_ellipse`

Draw an ellipse in the open sketch from its centre and axis lengths, in mm.

**Prerequisite:** Active sketch edit mode

**Sample call:**

```json
{}
```

---

### `add_sketch_constraint`

Add a geometric relation between sketch entities: horizontal, vertical, parallel, perpendicular, tangent, coincident, concentric, equal, symmetric, collinear or fix.

**Prerequisite:** Active sketch edit mode with named entities

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `entity1` | `str` | ✅ | `` | First entity: an id from the add_* tools or edit_sketch, 'Origin', or a point of an entity: '<id>.center', '<id>.start', '<id>.end' |
| `entity2` | `str?` | — | `None` | Second entity, named the same way, when the relation takes two |
| `entity3` | `str?` | — | `None` | Third entity ID. Required for 'symmetric' (the centerline of symmetry); must be null for all other relations. |
| `relation_type` | `str` | ✅ | `` | Relation type (parallel, perpendicular, tangent, coincident, etc.) |

**Sample call:**

```json
{
  "entity1": "Line1",
  "relation_type": "perpendicular"
}
```

---

### `add_sketch_dimension`

Add a driving dimension to sketch entities: linear, horizontal, vertical, angular, radial or diameter.

**Prerequisite:** Active sketch edit mode

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `entity1` | `str` | ✅ | `` | First entity: an id from the add_* tools or edit_sketch, 'Origin', or a point of an entity: '<id>.center', '<id>.start', '<id>.end' |
| `entity2` | `str?` | — | `None` | Second entity, named the same way, for a distance or an angle; a circle stands for its centre in a distance |
| `dimension_type` | `Literal['linear', 'horizontal', 'vertical', 'angular', 'radial', 'diameter']` | — | `linear` | 'linear': a line's length, or the shortest distance between two points, circles (centres) or lines; 'horizontal' / 'vertical': the distance along sketch x / y (or a line's run / rise); 'angular': two connected lines; 'radial' / 'diameter': an arc or circle |
| `value` | `float` | ✅ | `` | Dimension value in mm, or degrees for angular |

**Sample call:**

```json
{
  "entity1": "Line1",
  "value": 50.0
}
```

---

### `sketch_linear_pattern`

Repeat sketch entities in a straight line as a parametric sketch pattern.

**Prerequisite:** Active sketch edit mode

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `entities` | `List[str]` | ✅ | `` | Ids of the entities to repeat (from the add_* tools) |
| `direction_x` | `float` | — | `1.0` | Direction x component |
| `direction_y` | `float` | — | `0.0` | Direction y component |
| `spacing` | `float` | — | `10.0` | Spacing between instances, mm |
| `count` | `int` | — | `3` | Instances, the seed included |
| `add_dimensions` | `bool` | — | `True` | Add the pattern's spacing and count dimensions, so set_dimension can change it later. False makes plain copies. |

**Sample call:**

```json
{
  "entities": []
}
```

---

### `sketch_circular_pattern`

Repeat sketch entities about a centre as a parametric sketch pattern, e.g. a bolt circle.

**Prerequisite:** Active sketch edit mode

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `entities` | `List[str]` | ✅ | `` | Ids of the entities to repeat; the first must be a circle, arc, ellipse, polygon or rectangle |
| `count` | `int` | — | `6` | Instances, the seed included |
| `angle` | `float` | — | `360.0` | Degrees the instances spread over |
| `center_x` | `float` | — | `0.0` | Pattern centre x in sketch mm (default the origin) |
| `center_y` | `float` | — | `0.0` | Pattern centre y in sketch mm (default the origin) |
| `add_dimensions` | `bool` | — | `True` | Add the pattern's radius, angle and count dimensions, so set_dimension can change it later. False makes plain copies. |

**Sample call:**

```json
{
  "entities": []
}
```

---

### `sketch_mirror`

Mirror sketch entities about a centreline in the open sketch.

**Prerequisite:** Active sketch edit mode

**Sample call:**

```json
{}
```

---

### `sketch_offset`

Offset sketch entities by a distance in mm, outward or inward, e.g. for a wall or a clearance.

**Prerequisite:** Active sketch edit mode

**Sample call:**

```json
{}
```

---

### `sketch_tutorial_simple_hole`

Demonstrate the sketch steps: draw a 5 mm circle at the origin on the Top plane and close the sketch.

**Prerequisite:** SolidWorks running

**Sample call:**

```json
{}
```

---

### `tutorial_simple_hole`

Draw a circle on a plane and cut it as a hole, as one worked example.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `plane` | `str` | ✅ | `` | Sketch plane for the hole |
| `center_x` | `float` | ✅ | `` | Hole center X coordinate in mm |
| `center_y` | `float` | ✅ | `` | Hole center Y coordinate in mm |
| `diameter` | `float` | ✅ | `` | Hole diameter in mm |
| `depth` | `float?` | — | `None` | Hole depth in mm (None for through hole) |

**Sample call:**

```json
{
  "plane": "Front",
  "center_x": 0.0,
  "center_y": 0.0,
  "diameter": 10.0
}
```

---
