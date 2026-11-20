## Why

An agent can cut a pocket today, but only from numbers it works out itself. To recess the web of a tray, a link or a frame, leaving a 5 mm rim all round, it has to read the face's outline, offset it by hand, sketch the result with `add_line` and `add_arc`, and cut it. On a face with arcs, splines or holes that is slow and error-prone. And the numbers go stale: one later `set_dimension` on the plate leaves the pocket where it was. SolidWorks has the right feature for this: Offset Entities on the face's own edges, which stays tied to them. The server doesn't expose it:

- `sketch_offset` offsets only entities the agent drew itself (ids from `add_line` and the like). It can't take a face's edges.
- `create_reference_plane` with `face_point` and `emboss_text` can sketch on a face, but nothing can project or offset that face's boundary.
- Nothing reads a face's loops (outer boundary, holes). `GetLoops`, `IsOuter` and `SketchUseEdge3` appear nowhere in `src/solidworks_mcp`.

The comparison with hjbaard's `solidworks-mcp` (v0.21.0) found `cut_offset_pocket` there and nothing like it here. Upstream issue `andrewbartels1/SolidworksMCP-python#77` asks for the other half of the same idea: `sketch_from_face_loop`, a face's boundary loops projected into a sketch as linked references, and a thin extrude of the result. This change adds the pocket tool and `sketch_from_face_loop`, which share the loop walk. The thin extrude needs no new tool (see "What Changes"). This change addresses `#77`. Work is on the fork `anyosag/SolidworksMCP-python`; issues are off there, so `#77` stays on upstream.

## What Changes

- **New tool `cut_offset_pocket`:**
  - Inputs: the face, by `face_point` (a point on it, mm) or `side`; `rim_mm`; and `depth_mm`, or leave it out to cut through.
  - It sketches on the face, offsets the face's outline inward by `rim_mm` as linked geometry, and cuts the offset region. A hole in the face keeps a rim of `rim_mm` around it.
  - Rim and depth come back as dimensions the agent can set. The pocket follows the outline when it changes.
  - The cut itself goes through `create_cut_extrude`'s path, so its side choice and failure messages apply.
  - It returns `pocket` (sketch, feature, dimension names, loop counts, `fully_defined`) and `measured_change`.
- **New tool `sketch_from_face_loop`:**
  - Inputs: the face (`face_point` or `side`); `loops` (`all` by default, `outer` or `inner`); `as_construction`; `keep_open`.
  - It opens a sketch on the face and projects the chosen loops into it as linked references (`SketchUseEdge3`). It returns the sketch name and a description of each loop.
  - It changes no geometry, so it carries no `measured_change`.
- **`thin_extrude` (the other half of #77) is already covered.**
  - `create_extrusion` has `thin_feature`, `thin_thickness` and `thin_side` (`one_side`, `other_side`, `mid_plane`) in `CreateExtrusionInput` (`tools/modeling.py`). The adapter's `FeatureExtrusionThin2` call maps `thin_side` through `_THIN_SIDES` (`adapters/solidworks/features.py`).
  - So `sketch_from_face_loop`, then `create_extrusion` with `thin_feature`, does what the issue's "combined pattern" asks. No `thin_extrude` tool is added.
  - Two differences from the issue's wording, neither needing work: the fork names the side `one_side` / `other_side` / `mid_plane`, not `one_side_1` / `one_side_2`; and the issue's `thin_cap_ends` isn't exposed (the adapter passes 0), which only matters for open profiles, not a closed face loop.
  - A live test proves the pair (tasks 7.1).
- **No change to `create_cut_extrude`.** The pocket tool calls it as it is. Its spec (`tools/cut-extrude`) is not modified.

## Capabilities

### New Capabilities
- `tools/face-outline-pocket`: pocketing a flat face inside a rim that follows its outline, holes included.
- `tools/face-loop-sketch`: projecting a face's boundary loops into a sketch as linked references.

### Modified Capabilities
None.

## Impact

- **COM/adapter code (extra risk per CLAUDE.md):**
  - New `adapters/solidworks/face_sketch.py` (`SolidWorksFaceSketchMixin`) with the two adapter methods. It uses `IFace2.GetLoops` → `ILoop2` (`IsOuter`, `GetEdges`), `IEntity.Select4`, `ISketchManager.SketchUseEdge3` and `IModelDoc2.SketchOffsetEntities2`. It reads the face through `FaceEdgeSelector` (`adapters/solidworks/face_selection.py`).
  - Many of these are zero-argument members (`GetLoops`, `IsOuter`, `GetEdges`). Through forced late binding they resolve as properties unless flagged, so each new dispatch object gets its methods flagged before use (`sw_type_info.flagged`; pitfalls #41, #42).
  - `adapters/base.py` gets two abstract methods; `connection_pool.py` and `circuit_breaker.py` forward them; `pywin32_adapter.py` adds the mixin to `PyWin32Adapter`'s bases.
  - `mock_adapter.py` gets canned versions on its 10 mm block.
- **Tools:**
  - `tools/modeling.py`: `cut_offset_pocket`, beside `create_cut_extrude` and `emboss_text`.
  - `tools/sketching.py`: `sketch_from_face_loop`.
  - `support_levels.py` (experimental until the live test passes), `tool_hints.py`, `error_classifier.py` only if a refusal needs a new rule.
  - Both families are in `DOCUMENT_FAMILIES` (`tools/target_document.py`), so both take `document` with no extra work.
- **Docs:**
  - The tool catalog and progress files.
  - The agent skill's `references/workflows.md` (a "pocket a face, leave a rim" workflow; the thin-wall pair from #77).
  - Pitfall entries for what the probes find (loop reads, offset sign, linkage).
- **Tests:**
  - unit tests beside each module (the pure helpers have no COM);
  - a probe script `tests/scripts/probe_face_outline.py` (scratch documents);
  - a live test `tests/live/test_live_sw_face_outline.py`.
- **Credit:** hjbaard's `cut_offset_pocket`, `_offset_outline` and `_open_face_sketch` (MIT, © 2026 Henk Jan Baard) are the reference for the call order. The code is written fresh, so no copied code needs a notice; `design.md` credits it.
