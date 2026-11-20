## Why

A part that must mate with something that only exists as a mesh (a scan, a printed test piece, a slicer project, a vendor's STL) can't be modelled from numbers. Its cross-sections are the profiles to build. Today an agent has no way to read them:

- `check_stl` (`utils/stl_mesh.py`) reads an STL, but only to count triangles, check that it is closed and total its volume.
- `check_printability` and `check_wall_thickness` read the active part's own mesh. Nothing reads a mesh from a file, and nothing reads a 3MF at all.
- After the agent models the part, it can't check the result against the reference except by eye.

The comparison with hjbaard's `solidworks-mcp` (v0.21.0) found two tools there (`slice_mesh`, `compare_with_mesh`) and none here. This change brings them into the fork, built the fork's way. No GitHub issue: issues are off on the fork `anyosag/SolidworksMCP-python`, and upstream has none for meshes.

## What Changes

- **New tool `slice_mesh`:**
  - Takes `path` (an STL or a 3MF), `axis` (`x`, `y` or `z`), `heights_mm` and `frame` (`object` or `build`, for a 3MF).
  - For each height it returns the closed loops where the plane cuts the mesh, largest first (the outline, then holes and pockets). Each loop has `area_mm2`, `min_mm`, `max_mm`, `point_count` and `points_mm`.
  - It needs no SolidWorks and no open document. Read-only.
- **New tool `compare_with_mesh`:**
  - Takes `path`, `axis`, `heights_mm`, `frame` and `offset_mm`.
  - It exports the active part to a temporary STL, slices that and the reference mesh at the same heights, and pairs the loops. For each pair it returns `reference_area_mm2`, `part_area_mm2`, `area_diff_mm2` and `max_extent_diff_mm`. It also returns the worst of each.
  - It adds no COM code: the part's mesh comes from the export `check_printability` already uses. hjbaard tessellates the part's faces; this change doesn't (D5).
  - Read-only. The user's STL options are put back, and the temporary file is deleted.
- **A 3MF reader:**
  - Reads the archive with `zipfile` and the XML with `defusedxml`.
  - Applies the file's `unit`, each build item's transform and nested components.
  - `frame: "build"` applies the build transforms (where a slicer put the object on the plate); `"object"` doesn't.
- **A shared slicing function:** `mesh_sections.section(triangles, axis, height)`, with a plain contract (D3). Change `joint-swept-region` reuses it to slice triangles read from SolidWorks.

## Capabilities

### New Capabilities
- `tools/mesh-slicing`: reading an STL or 3MF and slicing it into closed loops, and the `section` function other changes reuse.
- `tools/mesh-comparison`: comparing the active part's sections with a reference mesh.

### Modified Capabilities
None.

## Impact

- **COM/adapter code (extra risk per CLAUDE.md): very little.**
  - `slice_mesh` doesn't touch the adapter.
  - New `adapters/solidworks/mesh_compare.py`: `compare_part(adapter, ...)` and `SolidWorksMeshCompareMixin`. It calls `export_stl_with_settings` (already in the adapter) and does no COM of its own. The mixin is added to `PyWin32Adapter`'s bases in `pywin32_adapter.py`. This follows `print_check.py`.
  - `adapters/base.py` gains one abstract method, `compare_with_mesh`; `connection_pool.py` and `circuit_breaker.py` forward it.
  - `mock_adapter.py` runs the real `compare_part` on its mock STL (a tetrahedron).
- **New modules:**
  - `utils/mesh_sections.py`: hjbaard's `mesh_tools.py` ported nearly as it is (pure Python), with his MIT notice (D2).
  - `tools/mesh.py`: a new tool family with `slice_mesh` (D1).
- **Tools:**
  - `tools/analysis.py` gains `compare_with_mesh`, beside `check_printability`.
  - `support_levels.py`: `slice_mesh` starts `stable` once its unit tests pass (it never calls SolidWorks, so a live test can't add anything); `compare_with_mesh` starts `experimental` (`_UNCHECKED`). `OFFLINE_FAMILIES` gains `mesh`.
  - `tool_hints.py`, and `tools/__init__.py` for the new family.
  - Family lists elsewhere: `tests/integration/test_tool_descriptions.py`, `src/utils/generate_tool_catalog.py`, `src/utils/check_tool_docs_consistency.py`, `mkdocs.yml`.
  - The tool catalog, the progress files, and the agent skill's `references/workflows.md` (a "match a mesh" workflow).
- **Tests:**
  - unit tests beside each module;
  - fixture meshes under `tests/solidworks_mcp/utils/data/`: a 20 mm cube STL and a 3MF with a build transform and inch units;
  - a probe script `tests/scripts/probe_mesh_sections.py`;
  - a live test `tests/live/test_live_sw_mesh_sections.py` (for `compare_with_mesh`).
- **Licence:** `utils/mesh_sections.py` keeps hjbaard's copyright and permission notice in its header. `THIRD_PARTY_NOTICES.md` at the repo root is created or extended with the entry (change `joint-swept-region` may add another).
- **Dependencies:** none new. `defusedxml` is already in `pyproject.toml`. `numpy` is not a core dependency, so `mesh_sections` stays pure Python and `slice_mesh` works in a base install.
- **Credit:** hjbaard's `mesh_tools.py` (MIT, © 2026 Henk Jan Baard) is the ported code. `session.py` `slice_mesh` and `compare_with_mesh` are the reference for the payloads; `design.md` credits both.
