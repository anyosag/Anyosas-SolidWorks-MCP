# Export Tools

Convert SolidWorks models to industry-standard interchange and manufacturing formats: STEP, IGES, STL, PDF, DWG, and raster images. Includes a batch-export tool for processing whole directories.

> **Prerequisite:** An active document or a valid file path to an existing model.

**Total tools in this category: 9**

---

### `export_step`

Export the active document to STEP, like File > Save As with the STEP type.

**Prerequisite:** Active or specified model document

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | `str` | ✅ | `` | Full path for the exported file |
| `format_type` | `str?` | — | `None` | Not needed: the file extension sets the format; defaults to the tool's own format |

**Sample call:**

```json
{
  "file_path": "C:\\Temp\\mcp_demo\\part.sldprt"
}
```

---

### `export_iges`

Export the active document to IGES, like File > Save As with the IGES type.

**Prerequisite:** Active or specified model document

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | `str` | ✅ | `` | Full path for the exported file |
| `format_type` | `str?` | — | `None` | Not needed: the file extension sets the format; defaults to the tool's own format |

**Sample call:**

```json
{
  "file_path": "C:\\Temp\\mcp_demo\\part.sldprt"
}
```

---

### `export_stl`

Export the active document to STL, like File > Save As with the STL type.

**Prerequisite:** Active or specified model document

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `resolution` | `Literal['coarse', 'fine', 'custom']?` | — | `None` | Mesh resolution for this file: coarse, fine, or custom with deviation_mm and angle_deg. Left out: the user's STL setting |
| `deviation_mm` | `float?` | — | `None` | Largest distance between the mesh and the true surface, in mm. Smaller is finer; it makes the resolution custom |
| `angle_deg` | `float?` | — | `None` | Largest angle between neighbouring facets, in degrees. Smaller is finer on small curved details; it makes the resolution custom |
| `units` | `Literal['mm', 'cm', 'm', 'in', 'ft']?` | — | `None` | Units written in the file. Left out: the user's STL setting |
| `binary` | `bool?` | — | `None` | Binary STL (true) or ASCII (false). Left out: the user's setting |
| `file_path` | `str` | ✅ | `` | Full path for the exported file |
| `format_type` | `str?` | — | `None` | Not needed: the file extension sets the format; defaults to the tool's own format |

**Sample call:**

```json
{
  "file_path": "C:\\Temp\\mcp_demo\\part.sldprt"
}
```

---

### `export_pdf`

Export the active document to PDF, like File > Save As with the PDF type.

**Prerequisite:** Active or specified drawing document

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | `str` | ✅ | `` | Full path for the exported file |
| `format_type` | `str?` | — | `None` | Not needed: the file extension sets the format; defaults to the tool's own format |

**Sample call:**

```json
{
  "file_path": "C:\\Temp\\mcp_demo\\part.sldprt"
}
```

---

### `export_dwg`

Export the active document to DWG, like File > Save As with the DWG type.

**Prerequisite:** Active or specified drawing document

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | `str` | ✅ | `` | Full path for the exported file |
| `format_type` | `str?` | — | `None` | Not needed: the file extension sets the format; defaults to the tool's own format |

**Sample call:**

```json
{
  "file_path": "C:\\Temp\\mcp_demo\\part.sldprt"
}
```

---

### `export_image`

Save a picture of the active model, in a standard view or as it is on screen, as PNG or JPG.

**Prerequisite:** Active document

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file_path` | `str?` | — | `None` | Full path for the exported image |
| `output_path` | `str?` | — | `None` | Alternative output path |
| `model_path` | `str?` | — | `None` | Path to the model file |
| `format_type` | `str` | — | `jpg` | Image format (jpg, png, bmp, tiff) |
| `format` | `str?` | — | `None` | Alternative format field |
| `image_format` | `str?` | — | `None` | Image format alias |
| `width` | `int` | — | `1920` | Image width in pixels |
| `height` | `int` | — | `1080` | Image height in pixels |
| `resolution` | `str?` | — | `None` | Resolution alias |
| `view_orientation` | `str` | — | `isometric` | View to turn the model to (front, back, left, right, top, bottom, isometric, dimetric, trimetric), or current to keep the view on screen exactly as it is, zoom included |
| `orientation` | `str?` | — | `None` | Alias of view_orientation |
| `zoom_to_fit` | `bool?` | — | `None` | Zoom so the whole model fills the picture. Left out: yes for a named view, no for current, so a close-up stays a close-up |
| `zoom_box_mm` | `List[float]?` | — | `None` | Zoom to this part of the model: two opposite corners of a box in model coordinates, mm, as [x1, y1, z1, x2, y2, z2] |
| `hide` | `List[DisplayType]?` | — | `None` | Hide these for the picture only, e.g. ["planes", "sketches"]; they are shown again afterwards as they were |
| `section` | `ImageSection?` | — | `None` | Cut the model on a plane for this picture only, with the cut faces capped, e.g. {"plane": "Front Plane", "offset_mm": 0}, to show a snap inside its socket; the cut is taken off afterwards |
| `view_direction` | `List[float]?` | — | `None` | Look at the model from this direction instead of a standard view: [x, y, z] in model coordinates, pointing from the model toward you (the outward normal of the side you want to see). [0, 0, 1] is the front view, [1, 1, 1] the isometric, [1, -1, 1] the isometric of a Z-up part |
| `up` | `List[float]?` | — | `None` | With view_direction: the model direction shown pointing up, [x, y, z], at an angle to view_direction. Left out: +Y, or +Z with up_axis z |
| `up_axis` | `Literal['y', 'z']` | — | `y` | Which model axis is up. y: SolidWorks' standard views. z: for a part modelled Z-up, every standard view is turned so +Z is up (front looks from -Y, top from +Z, isometric from [1, -1, 1]) |
| `hide_components` | `List[str]?` | — | `None` | Assembly only: hide these components for the picture only, e.g. ["cover-1"] for a cover-off view, by the names list_components gives; they are shown again afterwards, also when the picture fails |

**Sample call:**

```json
{}
```

---

### `export_for_blender`

Export the active part or assembly for Blender, with the code that imports it right.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `folder` | `str?` | — | `None` | Folder for the mesh and its manifest; created if missing. Left out: a solidworks_mcp\blender\<name> folder in the system temp folder |
| `name` | `str?` | — | `None` | Name for the files and the Blender collection. Left out: the document's name. Keep it the same on a re-export so Blender replaces the earlier import |
| `file_format` | `Literal['glb', 'stl']` | — | `glb` | glb: SolidWorks' glTF exporter, with each component as its own object in place and its colours. stl: one mesh in model coordinates, no colours or parts |
| `up_axis` | `Literal['y', 'z']` | — | `y` | Which model axis is up: y for SolidWorks' views, z for a part modelled Z-up. It ends up on Blender's +Z |
| `resolution` | `Literal['coarse', 'fine']?` | — | `None` | STL only: mesh resolution for this file. Left out: the user's STL setting. A GLB uses SolidWorks' own tessellation |

**Sample call:**

```json
{}
```

---

### `batch_export`

Export every matching SolidWorks file in a folder to one format.

**Prerequisite:** Writable source and output directories

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_directory` | `str` | ✅ | `` | Directory containing SolidWorks files |
| `output_directory` | `str` | ✅ | `` | Directory for exported files |
| `format_type` | `str?` | — | `None` | Target export format |
| `export_format` | `str?` | — | `None` | Alternative export format |
| `file_pattern` | `str?` | — | `None` | File pattern alias |
| `recursive` | `bool` | — | `False` | Search subdirectories recursively |
| `include_subdirectories` | `bool` | — | `False` | Include subdirectories in search |
| `file_patterns` | `List[str]` | — | `['*.sldprt', '*.sldasm', '*.slddrw']` | File patterns to include |

**Sample call:**

```json
{
  "source_directory": "C:\\Temp\\mcp_demo",
  "output_directory": "C:\\Temp\\mcp_demo\\exports"
}
```

---

### `export_assembly_parts_stl`

Export every part of the active assembly to its own STL file, one per part.

**Prerequisite:** SolidWorks running

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `resolution` | `Literal['coarse', 'fine', 'custom']?` | — | `None` | Mesh resolution for this file: coarse, fine, or custom with deviation_mm and angle_deg. Left out: the user's STL setting |
| `deviation_mm` | `float?` | — | `None` | Largest distance between the mesh and the true surface, in mm. Smaller is finer; it makes the resolution custom |
| `angle_deg` | `float?` | — | `None` | Largest angle between neighbouring facets, in degrees. Smaller is finer on small curved details; it makes the resolution custom |
| `units` | `Literal['mm', 'cm', 'm', 'in', 'ft']?` | — | `None` | Units written in the file. Left out: the user's STL setting |
| `binary` | `bool?` | — | `None` | Binary STL (true) or ASCII (false). Left out: the user's setting |
| `folder` | `str` | ✅ | `` | Folder for the STL files; created if missing |
| `name_pattern` | `str` | — | `{name}.stl` | Each file's name: {name} (the part file's name), {config} (the configuration the assembly uses), {assembly} and {index}; must end in .stl |

**Sample call:**

```json
{
  "folder": "example"
}
```

---
