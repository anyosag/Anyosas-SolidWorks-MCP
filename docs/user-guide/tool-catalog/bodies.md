# Bodies Tools

Work with the solid bodies of a multibody part: list them, insert another part as bodies, combine (add, subtract, common), move or copy, delete or keep, split, measure the gap between two, and cut a body grown by a clearance out of another (a mating negative).

> **Prerequisite:** An active Part document.

**Total tools in this category: 8**

---

### `list_bodies`

List the solid bodies of the active part: each one's name, volume, box and face count.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `visible_only` | `bool` | — | `False` | List only the visible bodies; all when false |

**Sample call:**

```json
{}
```

---

### `insert_part`

Insert another part's solid bodies into the active part, at its origin or moved.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `translate_mm` | `List[float]?` | — | `None` | The shift [dx, dy, dz] in mm |
| `rotate_deg` | `float` | — | `0.0` | The turn in degrees about `axis` (right-hand rule), after the shift |
| `axis` | `Axis` | — | `z` | _AXIS_DESCRIPTION |
| `rotate_about_mm` | `List[float]?` | — | `None` | _ABOUT_DESCRIPTION |
| `file_path` | `str` | ✅ | `` | Absolute path of the saved .sldprt whose solid bodies to insert |
| `configuration` | `str?` | — | `None` | The inserted part's configuration; its active one when omitted |
| `break_link` | `bool` | — | `False` | Break the link to the file, so later edits to it don't update these bodies |

**Sample call:**

```json
{
  "file_path": "C:\\Temp\\mcp_demo\\part.sldprt"
}
```

---

### `combine_bodies`

Combine solid bodies of the active part: add them, subtract tools from a main body, or keep what they share.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `operation` | `Literal['add', 'subtract', 'common']` | ✅ | `` | add: merge them; subtract: cut the tools out of `main`; common: keep only what they all share |
| `main` | `str` | ✅ | `` | The main body's name, as list_bodies reports it |
| `tools` | `List[str]` | ✅ | `` | The tool bodies' names; they are consumed |

**Sample call:**

```json
{
  "operation": null,
  "main": "example",
  "tools": []
}
```

---

### `move_copy_body`

Move or copy solid bodies of the active part: shift them, turn them about an axis, or both.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `translate_mm` | `List[float]?` | — | `None` | The shift [dx, dy, dz] in mm |
| `rotate_deg` | `float` | — | `0.0` | The turn in degrees about `axis` (right-hand rule), after the shift |
| `axis` | `Axis` | — | `z` | _AXIS_DESCRIPTION |
| `rotate_about_mm` | `List[float]?` | — | `None` | _ABOUT_DESCRIPTION |
| `bodies` | `List[str]` | ✅ | `` | The bodies' names, as list_bodies reports them |
| `copy_bodies` | `bool` | — | `False` | Copy the bodies instead of moving them |
| `copies` | `int` | — | `1` | How many copies, each one more shift (or turn) from the last; with copy only |

**Sample call:**

```json
{
  "bodies": []
}
```

---

### `delete_keep_body`

Delete solid bodies from the active part, or keep only them, with a Body-Delete/Keep feature.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `bodies` | `List[str]` | ✅ | `` | The bodies' names, as list_bodies reports them |
| `keep` | `bool` | — | `False` | Keep these bodies and delete every other; false deletes these |

**Sample call:**

```json
{
  "bodies": []
}
```

---

### `split_body`

Split solid bodies of the active part along a plane or a sketch, keeping every piece as a body.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `plane` | `str?` | — | `None` | The plane: Front, Top, Right, or a plane's feature name such as 'Plane1' |
| `sketch` | `str?` | — | `None` | Or a sketch's name, e.g. 'Sketch3'; its lines cut through the bodies |
| `body` | `str?` | — | `None` | Split only this body; every body the plane or sketch cuts when omitted |

**Sample call:**

```json
{}
```

---

### `body_clearance`

Measure the smallest gap between two solid bodies of the active part, in mm.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `body_a` | `str` | ✅ | `` | One body's name, as list_bodies reports it |
| `body_b` | `str` | ✅ | `` | The other body's name |

**Sample call:**

```json
{
  "body_a": "example",
  "body_b": "example"
}
```

---

### `make_mating_negative`

Cut tool bodies, grown by a clearance, out of a target body: a pocket that fits them.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `target` | `str` | ✅ | `` | The body to cut into, e.g. the lid |
| `tools` | `List[str]` | ✅ | `` | The bodies whose shape is cut, e.g. the holder's labels; one name or a list. They stay in the part |
| `clearance_mm` | `float` | ✅ | `` | The gap left around the tools, in mm, e.g. 0.15 for a 3D printed fit |
| `directions` | `int` | — | `32` | Directions each tool's copies are shifted in around `axis`; 8 under-cuts sharp tips, 32 left about 0.002 mm3 |
| `axis` | `Axis` | — | `y` | The axis the shift directions ring: the direction the tools press into the target |
| `method` | `Literal['auto', 'indent', 'shifted_copies']` | — | `auto` | auto: an Indent feature, else shifted copies; or only one |

**Sample call:**

```json
{
  "target": "example",
  "tools": [],
  "clearance_mm": 10.0
}
```

---
