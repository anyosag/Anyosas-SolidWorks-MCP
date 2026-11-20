## Context

The fork already has the pieces this change builds on:
- `utils/stl_mesh.py` reads a binary or ASCII STL (`read_triangles(path, units)` returns each triangle as nine floats in mm, and refuses more than `MAX_TRIANGLES`, 1,000,000) and checks it (`check_stl`).
- `adapters/solidworks/print_check.py` exports the active part to a temporary STL through `export_stl_with_settings`, with `MESH_SETTINGS` (`resolution: fine`, `units: mm`, `binary`, `translate_to_positive: false`), reads it back and deletes it. With `translate_to_positive: false` the mesh is in the model's own coordinates. The user's STL options are put back.
- `adapters/mock_adapter.py` writes a closed tetrahedron with 10 mm legs for any STL export (`_MOCK_STL`), and its `check_printability` runs the real analysis on it.
- `defusedxml` is a core dependency (it parses `.sldmat` files in `adapters/solidworks/documentation.py`).
- `tools/support_levels.py` has `OFFLINE_FAMILIES` (`docs_discovery`, `server_admin`, `vba_generation`): families whose tools never call SolidWorks. `tests/integration/test_support_levels.py` asks a live test of every stable tool except those in an offline family.
- A tool's family is the name of the module that defines it (`tool_boundary.tool_family`). `DOCUMENT_FAMILIES` (`target_document.py`) decides which families take the `document` argument.

hjbaard's `solidworks-mcp` (MIT, © 2026 Henk Jan Baard) has the two tools:
- `src/solidworks_mcp/mesh_tools.py` (242 lines, pure Python): `load_mesh`, `_load_stl`, `_load_3mf`, `section`, `area`, `extents`, `compare_sections`, `simplify`.
- `session.py` `slice_mesh` and `compare_with_mesh`. His `compare_with_mesh` gets the part's mesh from `_part_triangles`, a fine STL export read back through `load_mesh`. He doesn't tessellate faces either; that was the plan's guess, and the code shows the export route is already his choice.

The module was run on a 20 mm cube before this design was written, to check the numbers below:
- A cube sliced at z = 10 gives one loop with 8 raw points (each face is two triangles, so the diagonals add four), 4 after `simplify`, area 400 mm², extents (0, 20) × (0, 20).
- At z = 0 it gives no loop; at z = 20 it gives the 400 mm² loop. A vertex exactly on the plane counts as above it, so the bottom plane misses and the top plane hits.
- A cube with a closed 10 mm void inside gives two loops, 400 and 100 mm², largest first.
- A 2 × 1 × 1 inch box in a 3MF with `unit="inch"` and the build transform `0 1 0 -1 0 0 0 0 1 2 0 0`, sliced on z at 12.7 mm: `frame="object"` gives 1290.32 mm² with extents (0, 50.8) × (0, 25.4); `frame="build"` gives 1290.32 mm² with extents (25.4, 50.8) × (0, 50.8).

## Goals / Non-Goals

**Goals:**
- An agent can read the cross-sections of an STL or 3MF as closed polygons, in mm, at chosen heights, without SolidWorks.
- An agent can compare the active part's sections with a reference mesh, and see which loops differ in area and in extent.
- Change `joint-swept-region` can slice its own triangles with the same function.

**Non-Goals:**
- Meshes as SolidWorks geometry (no import, no `open_model` for STL).
- Other mesh formats (OBJ, PLY, AMF), and 3MF extensions beyond core meshes and components (materials, beam lattice, slices).
- Repairing meshes. A mesh that isn't closed gives open chains, which are dropped (D2).
- Boolean or volume comparison. The comparison is section by section.
- Assemblies. `compare_with_mesh` takes a part.

## Decisions

### D1. Two tools in two families

- `slice_mesh` lives in a new module `tools/mesh.py`, so its family is `mesh`. It needs no adapter and no document.
- `compare_with_mesh` lives in `tools/analysis.py` beside `check_printability`, which it resembles (export, read back, check). Its family is `analysis`, so it takes `document` like the other analysis tools.

One module for both would have made `compare_with_mesh` offline by association, and a stable `compare_with_mesh` without a live test would pass the support-level test. Two modules keep the test honest.

### D2. Port `mesh_tools.py` nearly as it is

The code is ported as `utils/mesh_sections.py`. The header keeps hjbaard's copyright and the MIT permission notice, and says what was changed. `THIRD_PARTY_NOTICES.md` at the repo root is created, or extended if change `joint-swept-region` got there first, with the same notice.

What stays, because it was checked above and is plain:
- `section`, `area`, `extents`, `simplify`, `compare_sections` (with D6's change), `_cross` (the edge is taken in a fixed vertex order, so two triangles sharing it give identical points and the loop closes), the 3MF transform maths (`_apply`, `_compose`; a point is a row vector times the 4 × 3 matrix) and `COLLINEAR_MM = 1e-4`.
- Points are rounded to 1e-6 mm to match the points that neighbouring triangles share.

What changes:
- **STL:** `_load_stl` is dropped. `load_mesh` reads an STL through `stl_mesh.read_triangles(path, "mm")` and turns each flat triangle into three points (`as_triangles`). That gives the fork's triangle cap and its binary-versus-ASCII rule (the size decides first, as some binary headers start with `solid`).
- **XML:** `xml.etree.ElementTree` becomes `defusedxml.ElementTree`. A file with a DTD or an entity is refused.
- **Errors:** `SolidWorksError` is hjbaard's class and doesn't exist here. The module raises `MeshError(ValueError)`. The tool turns it into `InvalidInputError`, and a missing file into a `not_found` error (D8).
- **Limits (new):**
  - the 3MF model part may not unpack to more than 256 MB (a zip bomb);
  - components nest at most 16 deep, and a cycle is an error (hjbaard's code recurses without a limit);
  - the total triangle count stays under `stl_mesh.MAX_TRIANGLES`.
- **Root part:** hjbaard reads `/3D/3dmodel.model`. The 3MF standard names the root part in `_rels/.rels`. The reader looks there first and falls back to `/3D/3dmodel.model`. Probe 1.1 checks real files to see whether the fallback is ever needed.
- **`simplify`:** it removes points on the line through their neighbours (1e-4 mm). It stays as it is: `slice_mesh` and `compare_with_mesh` both call it, because the raw loop of a flat face carries the diagonal points.

### D3. The `section` contract (for change `joint-swept-region`)

```python
def section(triangles: Iterable[Triangle3], axis: str, height: float) -> list[list[tuple[float, float]]]: ...
```

- **In:** `triangles` is a sequence of triangles, each three `(x, y, z)` points in mm. `axis` is `x`, `y` or `z` (any case). `height` is a number in mm along that axis. The sequence is read once per call, so a list serves any number of calls and a generator serves one.
- **Out:** a list of closed loops, largest area first. Each loop is a list of `(u, v)` points in mm, the plane's two coordinates: `(y, z)` for `x`, `(x, z)` for `y`, `(x, y)` for `z`. The first point is not repeated at the end. A loop has at least three points. A plane that misses the mesh gives `[]`.
- **On the plane:** a vertex exactly at `height` counts as above it.
- **Not promised:**
  - the point order (clockwise or not) and the start point;
  - which loops are holes. Nesting is the caller's to work out by containment, with `area` and `extents`;
  - that collinear points are gone. `simplify(loop)` does that.
- **Open meshes:** edges that don't close into a loop are dropped.
- **Errors:** an unknown axis raises `MeshError`. The function doesn't mutate its input.
- `as_triangles(flat)` turns `stl_mesh`'s nine-float triangles into this shape. Triangles from SolidWorks' tessellation reach the function through it, or directly if they are already three points.

Pure Python by design: `numpy` is only an optional dependency here, and `slice_mesh` has to run in a base install.

### D4. 3MF: unit, transforms and frame

- **Unit:** the model's `unit` attribute (`micron`, `millimeter`, `centimeter`, `inch`, `foot`, `meter`; default `millimeter`) scales vertices and every transform's translation into mm. A component in another model part uses that part's unit. An unknown unit is an error.
- **Transforms:** a `transform` attribute is 12 numbers, the 4 × 3 matrix by rows. A point goes `p · R + t`. Component transforms apply inside out: the component's, then its parent's, then the build item's.
- **Frame:**
  - `object` (the default): the object's own modelling coordinates. Component transforms apply; the build item's doesn't.
  - `build`: the build item's transform applies too, so the mesh is where the slicer project put it on the plate.
  - An STL has no frame. `frame` is accepted and ignored.
- **Several items:** every item of the build is read and the triangles are joined, in either frame. In `object` frame, items placed apart on the plate then overlap, so a one-item project is the clear case. The reply carries `objects` (how many items) so the agent can see.
- **Object types:** hjbaard reads every object the build refers to. The 3MF standard also has `support` and `solidsupport` types for helper geometry. Whether a slicer's file mixes them into the build, and so whether they should be left out, is probe 1.1's question and an open question below.

### D5. The part's mesh comes from the STL export

`compare_with_mesh` writes the active part to a temporary STL (`MESH_SETTINGS` from `print_check.py`: fine, mm, binary, model coordinates), reads it with `read_triangles`, and deletes it. No new COM.

**Alternative rejected:** tessellating each face through `IFace2` (the route `thickness.py` takes for wall thickness). It costs a COM call per face, hundreds on a real part, and the triangles come per face, not welded. The export is one call and gives the mesh a slicer would see. It also reuses the code that already restores the user's STL options.

Consequence, stated in the tool description: a curved wall is a polygon. A Ø20 circle sliced from a fine mesh has an area slightly under π·10² = 314.16 mm², by the chord error of the export. Probe 1.2 measures how much.

The reference mesh is read first, before SolidWorks is touched, so a bad path or file fails without an export.

### D6. Pairing loops

`compare_sections(reference, part)` pairs each reference loop, largest first, with the nearest unpaired part loop by the centre of their extents. hjbaard's code picks the nearest centre and, on a tie, the first part loop in list order. A hole and its outline share a centre, so a hole could pair with the outline.

This change adds a tie-break: among loops whose centre is within 1e-6 mm of the nearest, the one with the closest area wins. The order of the pairs stays the reference's order. Each reference loop then pairs with at most one part loop.

Loops are single, not net: a plate with a hole is two loops (the outline and the hole), each with its own area. The reply says so.

The reply gives `unmatched_reference` and `unmatched_part`: loops left without a partner. They mean a feature is missing from one side.

### D7. Slicing runs off the event loop

Slicing a large mesh at many heights is CPU work in pure Python: a million triangles at 20 heights is tens of seconds. The server's event loop is also the COM thread, so a long slice would stop every SolidWorks call.

- `slice_mesh` runs its work in `asyncio.to_thread`. It makes no COM call, so the rule that adapter calls stay on the connect thread isn't touched (`docs_discovery.py` does the same).
- `compare_with_mesh` exports on the connect thread as every adapter call does, and slices in a thread.
- Limits: at most 100 heights per call, and the 1,000,000 triangle cap. Probe 1.2 times the slice on a real mesh and sets whether the heights limit needs lowering.

### D8. Errors

All through the tool boundary: `normalize_input`, `InvalidInputError`, `ToolOperationError`.

- **`invalid_input`:** an empty or over-long `heights_mm`, a height that is not finite, an unknown axis or frame, an extension that is not `.stl` or `.3mf`, a 3MF that doesn't parse or has no triangles, a refused XML, a mesh over the cap, an `offset_mm` that isn't three numbers, no open part or an active document that isn't a part.
- **`not_found`:** the file doesn't exist. The message says "The mesh file <path> does not exist", which the classifier's `does not exist` rule already maps to `not_found` (a test asserts it).
- An STL that isn't closed or that holds no triangles at the height gives an empty `loops` list for that height, not an error.

### D9. The adapter interface

One new abstract method on `SolidWorksAdapter` (`adapters/base.py`):

```python
async def compare_with_mesh(
    self,
    path: str,
    axis: str,
    heights_mm: list[float],
    frame: str = "object",
    offset_mm: list[float] | None = None,
) -> AdapterResult[dict[str, Any]]: ...
```

- `adapters/solidworks/mesh_compare.py` holds `compare_part(adapter, ...)`, as `print_check.check_part` does. It reads the reference, exports the part, slices and compares. Both the real adapter and the mock call it.
- `SolidWorksMeshCompareMixin` has the one method, which checks the active document is a part (`_require_doc_type({1}, ...)`, as `check_printability` does) and calls `compare_part`. It is added to `PyWin32Adapter`'s bases.
- `connection_pool.py` and `circuit_breaker.py` forward the method.
- `slice_mesh` has no adapter method. It calls `mesh_sections` directly.

### D10. The mock

`mock_adapter.py` `compare_with_mesh` checks that the current model is a part and calls the real `compare_part` with itself. Its STL export writes the tetrahedron with 10 mm legs. Sliced at 5 mm on any axis, that is a right triangle with 5 mm legs: area 12.5 mm², extents (0, 5) × (0, 5). So the mock is a real, checkable result and not a canned one. It mirrors the payload shape, not SolidWorks' tessellation.

### D11. Support levels and hints

- `slice_mesh`: `stable`, once its unit tests pass. It never calls SolidWorks, and `OFFLINE_FAMILIES` gains `mesh` so the support-level test doesn't ask for a live test. This is how `docs_discovery` and `vba_generation` already work: "stable on unit tests alone" (the docstring in `support_levels.py`).
- `compare_with_mesh`: `_UNCHECKED` (`experimental`) until `test_live_sw_mesh_sections.py` passes on SolidWorks 2025 and 2026. Then `stable`.
- Hints: both `_reads`. `compare_with_mesh` writes a temporary file, as `check_printability` does, and is `_reads` too.

## Risks / Trade-offs

- **Tessellation error.** The part is a mesh. A curved wall differs from a reference's by up to the chord error of the export, so a small `area_diff_mm2` on a round feature isn't a fault. The description says so. Probe 1.2 measures it on a Ø20 hole.
- **Pure Python speed.** One slice is a pass over all triangles. D7 keeps the server responsive, and the heights limit keeps one call bounded. If probe 1.2 finds it too slow, a numpy prefilter by height range can be added behind the same contract.
- **Touching loops.** Where two loops touch at a point, the trace can follow the wrong branch. The ported code doesn't handle it. A real part's section rarely has it; no scenario relies on it. Noted in the tool description.
- **3MF variety.** Slicers write the root part, units and extensions differently. Probe 1.1 reads real files before the reader is finished.
- **Path reads.** The tools read any path the server can read, like `check_stl` on an export. They only read, and only `.stl` and `.3mf`.
- **Tool count.** Two more tools, one in a family of its own, while queue items 11 and 12 work to shrink the list. `mesh` can fold into `analysis` if #174 decides so.

## Probe results

To fill in after task group 1.

## Live results

To fill in after task group 7.

## Open Questions

- Should `slice_mesh` and `compare_with_mesh` take `units` for an STL (default `mm`)? An STL has no unit. `stl_mesh.read_triangles` already accepts one. It would help an STL saved in inches; it adds an input nobody has asked for.
- Should a loop carry `is_hole` (nesting by containment)? Change `joint-swept-region` may want it. It would be added to `section`'s callers, not its contract.
- Should `slice_mesh` take a `tolerance_mm` that simplifies a curved loop to fewer points (Douglas–Peucker, as hjbaard's `region_tools.py` has)? A tessellated circle returns hundreds of points today.
- Should objects of type `support` be left out of a 3MF's triangles? Probe 1.1 decides.
- Should `compare_with_mesh` accept an assembly (one STL of the whole assembly)? `export_file` already turns on `swSTLComponentsIntoOneFile` (toggle 72) for an assembly, so one STL is possible. It is left out for scope: an assembly's mesh in model coordinates is untested here.
