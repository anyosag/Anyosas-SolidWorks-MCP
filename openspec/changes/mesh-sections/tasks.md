## 1. Probes (offline, plus scratch SolidWorks documents with the user's OK)

- [ ] 1.1 `tests/scripts/probe_mesh_sections.py`, 3MF part (no SolidWorks): read real 3MF files the user supplies (a PrusaSlicer or Bambu Studio project, a Cura export, and a SolidWorks 3MF save if SolidWorks can write one).
  - Record the root part name from `_rels/.rels` and whether it is `/3D/3dmodel.model`.
  - Record each file's `unit`, whether build items carry transforms, whether components point to other parts (`p:path`), and each object's `type`.
  - Settle D2's root part rule, and the open question on `support` objects.
- [ ] 1.2 Same script, SolidWorks part (a scratch part with a Ø20 × 10 mm boss, a Ø8 through hole and a fillet).
  - Export it with `MESH_SETTINGS` and read it with `read_triangles`.
  - Slice at a height inside the boss, and at a height exactly on a flat face. Record the loops, the Ø20 loop's area against π·10² = 314.16 mm², and whether the hole loop is closed.
  - Time `section` on a mesh of about 100,000 and 1,000,000 triangles, at 1 and 20 heights, in pure Python.
  - Record the results in design.md under "Probe results" and settle D5's chord-error wording and D7's heights limit.

## 2. Mesh module

- [ ] 2.1 `utils/mesh_sections.py`, ported from hjbaard's `mesh_tools.py` (D2).
  - Header: his copyright line and the MIT permission notice, and a line saying what changed.
  - Keep `section`, `area`, `extents`, `simplify`, `compare_sections` (with D6's tie-break) and the 3MF maths.
  - Change: STL through `stl_mesh.read_triangles`; `defusedxml`; `MeshError(ValueError)`; the root part from `_rels/.rels`; the nesting, cycle and size limits; add `as_triangles`.
- [ ] 2.2 Fixtures under `tests/solidworks_mcp/utils/data/` and a script `tests/scripts/make_mesh_fixtures.py` that writes them:
  - `cube_20mm.stl`, binary, corner at the origin;
  - `box_2x1x1_inch_build_transform.3mf`: `unit="inch"`, one 2 × 1 × 1 inch box, build transform `0 1 0 -1 0 0 0 0 1 2 0 0`.
  - Cases that need only a few lines (the hollow cube, a component, a cycle, an entity, a bad unit, a tetrahedron) are built in the tests.
- [ ] 2.3 `THIRD_PARTY_NOTICES.md` at the repo root: create it, or extend it if change `joint-swept-region` already did. One entry: `utils/mesh_sections.py`, from hjbaard's `solidworks-mcp` `mesh_tools.py`, with his copyright line and the MIT permission notice in full.
- [ ] 2.4 Tests in `tests/solidworks_mcp/utils/test_mesh_sections.py`, one per spec scenario that touches the module:
  - the cube at 0, 10, 20, −1 and 25, on `x` and `z`;
  - the hollow cube (two loops, 400 and 100);
  - the tetrahedron at 5 (area 12.5);
  - the inch box in both frames (1290.32 mm²; extents `(0, 50.8, 0, 25.4)` and `(25.4, 50.8, 0, 50.8)`);
  - the component cube (extents `(30, 40, 0, 10)`);
  - an unknown unit, an entity, a cycle, a nesting of 17 levels;
  - `compare_sections`: matched, shifted, and the hole-with-outline tie (D6, hole areas 100 and 144, difference 44);
  - `as_triangles`.

## 3. Slice tool

- [ ] 3.1 `tools/mesh.py`: `register_mesh_tools` and `slice_mesh` (`SliceMeshInput`: `path`, `axis`, `heights_mm` with 1 to 100 finite items, `frame`).
  - Slicing in `asyncio.to_thread` (D7); errors per D8, with the missing file as `not_found`.
  - The description per `docs/agents/tool-description-standard.md`: needs no SolidWorks; a curved wall is a polygon; the frame choice; returns.
  - Tests in `tests/solidworks_mcp/tools/test_mesh.py`: every scenario in `mesh-slicing`, including no adapter call (a bare adapter that fails on any call) and that a missing file classifies as `not_found`.
- [ ] 3.2 Register the family:
  - `tools/__init__.py` (`register_mesh_tools`);
  - `support_levels.py`: `slice_mesh` `_STABLE`, and `mesh` added to `OFFLINE_FAMILIES`;
  - `tool_hints.py`: `slice_mesh` `_reads`;
  - `error_classifier.py` only if "does not exist" doesn't already give `not_found` for the message in D8;
  - tests beside each.
- [ ] 3.3 Family lists: `tests/integration/test_tool_descriptions.py`, `src/utils/generate_tool_catalog.py` (a `mesh` entry), `src/utils/check_tool_docs_consistency.py` (`mesh.py`), `mkdocs.yml`.

## 4. Adapter: compare

- [ ] 4.1 `adapters/solidworks/mesh_compare.py`: `compare_part(adapter, path, axis, heights_mm, frame, offset_mm)` (D5, D9).
  - Read and offset the reference first, export with `MESH_SETTINGS` from `print_check.py`, slice both in a thread, compare, delete the folder in a `finally`.
  - Tests in `tests/solidworks_mcp/adapters/solidworks/test_mesh_compare.py` with a fake adapter whose `export_stl_with_settings` writes a plate STL: the matched, shifted and unmatched scenarios, a failed export (no file left), a bad reference (no export call).
- [ ] 4.2 `SolidWorksMeshCompareMixin` in the same module: `compare_with_mesh` guarded to a part. Add it to `PyWin32Adapter`'s bases in `pywin32_adapter.py`; test the guard (assembly, drawing, no document).
- [ ] 4.3 `adapters/base.py`: the abstract `compare_with_mesh` (D9). Forward it in `connection_pool.py` and `circuit_breaker.py`, with tests beside each.

## 5. Mock adapter

- [ ] 5.1 `mock_adapter.py` `compare_with_mesh` (D10): a part guard and `compare_part` on the mock STL.
  - Tests in `tests/solidworks_mcp/adapters/test_mock_adapter_mesh_compare.py`: the tetrahedron against itself at z = 5 (12.5, 12.5, 0, 0), against a 20 mm cube at z = 5 (reference 400, part 12.5, `area_diff_mm2` −387.5, `max_extent_diff_mm` 15), and an assembly refused.

## 6. Compare tool

- [ ] 6.1 `tools/analysis.py`: `compare_with_mesh` (`CompareWithMeshInput`: as `SliceMeshInput` plus `offset_mm` as three finite numbers).
  - The description says the part is a fine mesh and a curved wall differs by the chord error (the figure from probe 1.2), loops are single, and `unmatched_*` mean a missing feature.
  - Tests in `tests/solidworks_mcp/tools/test_analysis_mesh_compare.py`.
- [ ] 6.2 `support_levels.py` (`_UNCHECKED`) and `tool_hints.py` (`_reads`), with tests beside each.

## 7. Docs

- [ ] 7.1 Regenerate the tool catalog (`src/utils/generate_tool_catalog.py`) and the progress files (`docs/assets/progress/`). Check `src/utils/verify_tool_count.py`.
- [ ] 7.2 Agent skill: `references/workflows.md` gains "Match a mesh": slice the reference at the heights that show each feature, build those profiles, then `compare_with_mesh` at the same heights and read `unmatched_*` and the worst differences.
- [ ] 7.3 `docs/agents/com-api-pitfalls.md`: entries only for what probe 1.2 found (chord error, an exact-height plane).
- [ ] 7.4 `.\dev-commands.ps1 dev-docs-strict`.

## 8. Live

- [ ] 8.1 `tests/live/test_live_sw_mesh_sections.py`, one test per spec scenario that touches SolidWorks:
  - the 40 × 30 × 5 mm plate on the Top plane, matched against its reference with and without `offset_mm [-20, 0, -15]` (20 mm, then 0);
  - the 20 × 10 mm pocket against the 12 × 10 × 3 mm void (hole pair: 120 and 200, difference 80, extent 4);
  - the cube against its own export (diffs under 1e-3);
  - an assembly refused, a missing file refused with no export;
  - the user's STL options are as before, read from `mesh.restored` and again from the preferences.
- [ ] 8.2 Run `tests/live/test_live_sw_stl_settings.py` and `test_live_sw_regression.py`: the new mixin touches `pywin32_adapter.py` and uses the STL export.
- [ ] 8.3 Record the results in design.md under "Live results", with the SolidWorks versions signed off.

## 9. Gate

- [ ] 9.1 `.\dev-commands.ps1 dev-lint` and `.\dev-commands.ps1 dev-test` (report counts only).
- [ ] 9.2 Live sign-off by the user. Then raise `compare_with_mesh` to `stable`.
- [ ] 9.3 Update `openspec/queue.md` (item 20). Archive the change after its PR merges.
