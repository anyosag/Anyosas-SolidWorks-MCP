# Engineering Data Tools

Read and edit the data drawings, BOMs and analyses are built from: every model dimension by name, measurements between faces, edges and vertices, materials, custom properties, and sheet-metal flat patterns.

> **Prerequisite:** An active Part (or Assembly, for dimensions and measuring) document.

**Total tools in this category: 13**

---

### `list_model_dimensions`

List every dimension in the active part or assembly with its full name and value.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `feature_name` | `str?` | — | `None` | Only list this feature's dimensions, e.g. 'Boss-Extrude1'. Omit to list every dimension in the model. |

**Sample call:**

```json
{}
```

---

### `list_equations`

List the active part's or assembly's global variables and equations with their values.

**Prerequisite:** SolidWorks running

**Sample call:**

```json
{}
```

---

### `set_global_variable`

Add a global variable to the active part or assembly, or change its value, e.g. "well_d" = 1.2mm.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `name` | `str` | ✅ | `` | The variable's name, without quotes, e.g. "well_d" |
| `value` | `float` | ✅ | `` | Its value, in `units` |
| `units` | `Literal['mm', 'cm', 'm', 'in', 'ft', 'deg', 'rad', '']` | — | `mm` | Unit written after the number: mm, cm, m, in, ft, deg, rad, or "" for a plain number (a count) |

**Sample call:**

```json
{
  "name": "MCP_Demo_Part",
  "value": 50.0
}
```

---

### `add_equation`

Drive a dimension of the active part or assembly by an equation, e.g. "D1@Sketch3" = "well_d".

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `dimension` | `str` | ✅ | `` | The dimension's full name, e.g. "D1@Sketch3" (list_model_dimensions, or a sketch pattern's `dimensions`) |
| `expression` | `str` | ✅ | `` | The right-hand side in SolidWorks syntax, names in double quotes: "\"well_d\"", "\"well_d\" * 2", "\"D1@Sketch1\" + 3mm". A bare global variable name is quoted for you. |

**Sample call:**

```json
{
  "dimension": "D1@Sketch1",
  "expression": "example"
}
```

---

### `delete_equation`

Delete an equation or a global variable of the active part or assembly.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `name` | `str?` | — | `None` | What the equation sets: a global variable ("well_d") or a dimension ("D1@Sketch3") |
| `index` | `int?` | — | `None` | Its `index` from list_equations |
| `force` | `bool` | — | `False` | Delete a global variable that other equations read (they then fail to solve) |

**Sample call:**

```json
{}
```

---

### `measure_entities`

Measure faces, edges or vertices of the active model with SolidWorks' Measure tool.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `entities` | `List[str]` | ✅ | `` | One or two entities to measure. Each is 'face:x,y,z' (a point inside the face), 'edge:x,y,z', 'vertex:x,y,z', a bare 'x,y,z' (nearest edge) or a topology name like 'Edge<1>'. Coordinates are in metres. One face gives area/perimeter; one edge gives length/radius; two entities give distance and angle. |

**Sample call:**

```json
{
  "entities": []
}
```

---

### `get_material`

Report the material assigned to the active part.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `configuration` | `str?` | — | `None` | Configuration; the active one when omitted |

**Sample call:**

```json
{}
```

---

### `set_material`

Assign a library material to the active part, e.g. "AISI 1020" or "6061-T6 (SS)".

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `material_name` | `str` | ✅ | `` | Exact library name, e.g. 'AISI 1020', '6061-T6 (SS)', 'ABS'. Use list_available_materials to find it. |
| `database` | `str?` | — | `None` | Library; 'SOLIDWORKS Materials' when omitted |
| `configuration` | `str?` | — | `None` | Configuration; all configurations when omitted |

**Sample call:**

```json
{
  "material_name": "MCP_Demo_Part"
}
```

---

### `set_density`

Give the active part a density, e.g. a resin's 1.15 g/cm³, so its mass means something without a library material.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `density_g_cm3` | `float` | ✅ | `` | The density, g/cm³: 1.15 for a typical resin, 1.24 for PLA |
| `remove_material` | `bool` | — | `False` | Take the part's material off first: SolidWorks uses an assigned material's density instead of this one, so the call is refused while the part has one |

**Sample call:**

```json
{
  "density_g_cm3": 10.0
}
```

---

### `list_available_materials`

List the materials in the installed SolidWorks material libraries.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `search` | `str?` | — | `None` | Case-insensitive filter on material name or classification, e.g. 'steel' or '6061' |

**Sample call:**

```json
{}
```

---

### `get_custom_properties`

Read the active document's custom properties, such as PartNo, Description or Revision.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `configuration` | `str?` | — | `None` | Configuration-specific properties; file-level (what title blocks usually read) when omitted |

**Sample call:**

```json
{}
```

---

### `set_custom_property`

Create or overwrite one text custom property on the active document, e.g. PartNo or DrawnBy.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `name` | `str` | ✅ | `` | Property name, e.g. 'PartNo', 'Description', 'DrawnBy' |
| `value` | `str` | ✅ | `` | Text value; may be a $PRP:"SW-Mass" style link |
| `configuration` | `str?` | — | `None` | Configuration; file-level when omitted |

**Sample call:**

```json
{
  "name": "MCP_Demo_Part",
  "value": 50.0
}
```

---

### `export_flat_pattern`

Export the active sheet-metal part's flat pattern to DXF or DWG, for laser or waterjet cutting.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | `str` | ✅ | `` | Output path ending in .dxf or .dwg |

**Sample call:**

```json
{
  "file_path": "C:\\Temp\\mcp_demo\\part.sldprt"
}
```

---
