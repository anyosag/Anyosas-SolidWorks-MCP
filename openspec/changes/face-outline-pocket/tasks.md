## 1. Probes (SolidWorks, scratch documents, each with the user's OK)

- [ ] 1.1 `tests/scripts/probe_face_outline.py`: the sketch host (D2).
  - On a 60 × 40 × 10 mm plate, select the top face and call `InsertSketch(True)`. Check `ActiveSketch` is set and read where the sketch's x and y point.
  - Repeat on a plane made with `create_reference_plane(face_point=…)`.
  - For each host, offset the face's edges (task 1.3's call) and record whether the result is linked to the edges.
  - Record the results in design.md under "Probe results" and settle D2.
- [ ] 1.2 Loops (D3).
  - On a face with a Ø10 hole, a rounded outline and a spline outline, flag and call `IFace2.GetLoops`, `ILoop2.IsOuter` and `ILoop2.GetEdges`.
  - Record the count and kind of edges per loop, how a full circle appears, and whether the order of loops is stable across a rebuild.
  - Settle D3's loop walk.
- [ ] 1.3 Offset direction and dimensions (D4, D7).
  - On the plain plate, `SketchOffsetEntities2(-0.005, False, True)` with the outer loop's edges selected. Confirm it runs inward.
  - On the plate with the hole, offset the hole loop with a positive and a negative value. Record which side each goes.
  - Try both loops in one call and in two calls in the same sketch. Record how many dimensions result and what they are called.
  - Set `IDimension.Name = "rim"` through late binding, then `set_dimension("rim@<sketch>", 8)`. Confirm the offset moves and the model rebuilds.
  - Settle D4 and D7, and the Open Question about tying hole rims to the outer one.
- [ ] 1.4 Arcs, splines and refusals (D5).
  - Offset a rounded rectangle (R5 corners, rim 2): confirm R3 corners and the 20082.74 mm³ pocket.
  - Offset an L-shaped outline (a reflex corner), an outline with a concave arc tighter than the rim, and a spline outline. Record what SolidWorks makes.
  - Offset with rim 19 (a 2 mm strip on the 40 mm side), exactly 20 and 25. Record what the call returns, and whether a refusal leaves an open or half-made sketch.
  - Settle D5's refusal rule and message.
- [ ] 1.5 Linkage (D6).
  - Build the 5 mm through pocket, then `set_dimension` the plate's length 60 → 80 and rebuild. Measure the pocket (expect 70 × 30 mm).
  - Repeat with `edit_sketch` on the plate's sketch, and with a fillet added to a plate corner after the pocket.
  - Record whether each sketch host (1.1) keeps the link. Settle D6.
- [ ] 1.6 The cut from an offset sketch.
  - Cut the offset sketch with `create_cut_extrude` (`reverse_direction=False`) for through, blind 4, and the plate with a hole (nested contours).
  - Confirm the volumes in the spec (15000, 6000, 11858.41 mm³), that a sketch on the face cuts into the part, and the placeholder `depth` is ignored for `ThroughAll`.
  - Run `check_sketch_fully_defined` on the offset sketch and record the state.
  - Settle D1, D8 and D9.
- [ ] 1.7 Projection (D11).
  - With the face's edges selected loop by loop, call `SketchUseEdge3` (check its argument list with `lookup_api_method`). Compare with selecting the face and relying on `InnerLoops`.
  - Record the entity counts for the Ø10 plate (5 for all, 4 outer, 1 inner) and that the sketch follows `set_dimension` on the plate.
  - Find how to make the entities construction geometry (`SketchUseEdge3` has no option; try `ConstructionGeometry` on each segment).
  - Run the thin wall from the spec: `create_extrusion` with `thin_feature`, `thin_thickness: 2`, `thin_side: "mid_plane"`, `depth: 10` gives +4000 mm³. Also record the volume for `one_side` and `other_side`.
  - Settle D11.

## 2. Adapter: face sketches

- [ ] 2.1 `adapters/solidworks/face_selection.py` and a new `adapters/solidworks/face_sketch.py`, pure helpers:
  - `validate_face_pick(face_point, side)`;
  - `describe_loop(edges)` (counts by curve kind);
  - `offset_signs(loops, rim_mm)` (D4);
  - `rim_too_wide_message(...)`.
  - Tests in `tests/solidworks_mcp/adapters/solidworks/test_face_selection.py` and `test_face_sketch.py`: both, neither and one of `face_point`/`side`; an outer loop of 4 lines; a loop of 1 circle; a loop with an arc and a spline; the sign for a loop of each kind.
- [ ] 2.2 `SolidWorksFaceSketchMixin` in `face_sketch.py`: `sketch_from_face_loop` and `sketch_face_offset`.
  - The face is found with `FaceEdgeSelector`; the host is the one D2 settles.
  - Every COM call goes through `_handle_com_operation`. Flag `IFace2`, `ILoop2`, `IEntity` and `ISketchManager` members before use. Clear the selection first, check the selected edge count before `SketchOffsetEntities2`, and close the sketch in a `finally` (D10).
  - Tests with a fake face (beside `fake_pocket_plate.py` and `fake_documents.py`) covering a plain face, a face with a hole, a refused offset (the sketch is closed and removed), a non-flat face and a missed selection.
- [ ] 2.3 `adapters/base.py`: the two abstract methods (D12). Forward them in `connection_pool.py` and `circuit_breaker.py`, with tests beside each. Add the mixin to `PyWin32Adapter`'s bases in `pywin32_adapter.py`.

## 3. Mock adapter

- [ ] 3.1 `mock_adapter.py`: `sketch_from_face_loop` and `sketch_face_offset` on the canned block (D13), including the too-wide refusal and the hole loop.
  - Tests in `tests/solidworks_mcp/adapters/test_mock_adapter*.py`, including the spec's "Mock block with a hole" and "Mock too-wide rim" scenarios.

## 4. Tools

- [ ] 4.1 `tools/sketching.py`: `sketch_from_face_loop` (`SketchFromFaceLoopInput`: `face_point` (3 numbers) or `side`, `loops` as `Literal["all", "outer", "inner"]`, `as_construction`, `keep_open`).
  - The input check uses `validate_face_pick`.
  - Descriptions per `docs/agents/tool-description-standard.md`; the description points to `create_extrusion`'s `thin_feature` for a thin wall.
  - Tests in `tests/solidworks_mcp/tools/test_sketching*.py`.
- [ ] 4.2 `tools/modeling.py`: `cut_offset_pocket` (`CutOffsetPocketInput`: `face_point` or `side`, `rim_mm` with `gt=0`, `depth_mm` with `gt=0` or None).
  - Flow (D1, D8–D10): `sketch_face_offset`, close, `check_sketch_fully_defined`, `create_cut_extrude` (`reverse_direction=False`, `ThroughAll` or `Blind`), `measured_change` around the cut only (the sketch changes no volume), remove the sketch and any plane on any failure.
  - Reply per the spec: `pocket`, `feature`, `measured_change`, and a `warning` when the sketch isn't fully defined.
  - The description says to use `create_cut_extrude` for a profile you draw yourself.
  - Tests in `tests/solidworks_mcp/tools/test_modeling*.py`, with the mock and with a stub adapter: the cut fails and the sketch is removed; the offset fails and the plane is removed; the depth placeholder is not used for `ThroughAll`.
- [ ] 4.3 `support_levels.py` (both `_UNCHECKED`) and `tool_hints.py` (D14).
  - An `error_classifier.py` rule only if the refusal messages don't already classify as `invalid_input` / `not_found`.
  - Tests beside each.

## 5. Docs

- [ ] 5.1 Regenerate the tool catalog (`src/utils/generate_tool_catalog.py`) and the progress files (`docs/assets/progress/`). Check `src/utils/verify_tool_count.py`.
- [ ] 5.2 Agent skill: `references/workflows.md` gains "Pocket a face and leave a rim" (the pocket, then `set_dimension` on the returned names) and "A thin wall from a face's outline" (`sketch_from_face_loop`, then `create_extrusion` with `thin_feature`).
- [ ] 5.3 `docs/agents/com-api-pitfalls.md`: entries for what probes 1.1–1.7 found (the loop reads, the offset sign for each loop kind, dimension names, linkage, construction geometry).
- [ ] 5.4 `.\dev-commands.ps1 dev-docs-strict`.

## 6. Live

- [ ] 6.1 `tests/live/test_live_sw_face_outline.py`, one test per spec scenario that touches SolidWorks:
  - the through pocket (15000 mm³), the blind pocket (6000), the rounded corners (20082.74) and the hole (11858.41);
  - `set_dimension` on the rim name (13440 mm³ left) and on the depth name (9000);
  - the plate's length 60 → 80 (11000 mm³ left);
  - the rim of 25 mm refused with the tree unchanged, the point off the part, and the curved face;
  - the projection: counts for all, outer and inner loops; the thin wall (28000, then 36800 mm³); construction; `keep_open`.
- [ ] 6.2 Run `tests/live/test_live_sw_regression.py`: the new mixin touches `pywin32_adapter.py`.
- [ ] 6.3 Record the results in design.md under "Live results", with the SolidWorks versions signed off.

## 7. Gate

- [ ] 7.1 `.\dev-commands.ps1 dev-lint` and `.\dev-commands.ps1 dev-test` (report counts only).
- [ ] 7.2 Live sign-off by the user. Then raise the support levels to `stable` for the tools the live test proved.
- [ ] 7.3 Update `openspec/queue.md`. Archive the change after its PR merges, and note on upstream issue `#77` that `thin_extrude` is covered by `create_extrusion`'s `thin_feature` and `sketch_from_face_loop` has landed.
