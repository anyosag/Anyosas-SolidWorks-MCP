## 1. Probes (SolidWorks, scratch assemblies, each with the user's OK)

- [ ] 1.1 `tests/scripts/probe_joint_sweep.py`: the joint dimension.
  - Build a scratch assembly: a 10 × 10 × 100 mm bar and a base, a concentric mate and a coincident mate, and an angle mate (use `add_mate`, with and without `flip`).
  - Find the angle mate's dimension name: does `list_model_dimensions` list it, and in what form (`D1@Angle1`)? Try the form for a joint inside a sub-assembly (`D1@Angle1@Sub-1.SLDASM`).
  - Read `IDimension.GetType()` and `SystemValue` (radians?), and the value at the start.
  - Write 0, 45, 90, -30, 330 and 400°. Record which are taken and what each reads back. Do the same after `_flip_mate`.
  - Which way does a rising value turn the bar, with and without `flip`?
  - Write 80 values in a row and count how many reads differ (hjbaard saw 3 in 80).
  - Settle D2: whether the stepper wraps a mate's angle, the retry, and the direction the description gives.
- [ ] 1.2 Tessellation and transforms.
  - On a scratch sub-assembly of 20 parts (about 50 faces each), time `GetBodies2`, `GetFaces` and `GetTessTriangles(True)` per face, and count triangles.
  - Check `IComponent2.GetChildren` (new to the fork) on a sub-assembly, and `GetBodies2` on the sub-assembly itself.
  - Are the triangles in part or assembly coordinates? Is `Transform2` of a nested part relative to the top assembly or to its parent? Compare a nested part's placed box with `IComponent2.GetBox`.
  - Read `ArrayData`'s scale (element 12) on real parts, and a part made with a scale.
  - Time the slicing in Python for one step (`mesh_sections.section`, once change `mesh-sections` is on the branch) with and without the part-box rejection, and a 3000 mm grid at `tolerance_mm` 0.1 (time and memory).
  - Settle D3 (where `limits.check` goes, the box rejection), D7 (`MAX_GRID_CELLS`) and the open question on caps.
- [ ] 1.3 Per-step cost and broken mates.
  - On the 20-part assembly, time `ForceRebuild3(False)`, `Transform2` reads, `check_interference` and `measure_clearance` (one pair, exact) per step.
  - After a rebuild, does `Transform2` already show the solved placement (not the previous one)?
  - Delete the face a concentric mate uses; rebuild; confirm `collect_rebuild_issues` returns a `Mate...` feature with an error code, and what a suppressed mate returns.
  - Settle D4, D1's fallback, and `check_motion`'s cost note.
- [ ] 1.4 Interrupted runs.
  - Step a joint with the stepper's loop, raise `limits.OperationStopped` between steps, and confirm the `finally` restores.
  - Kill the server process mid-step, and read the dimension afterwards.
  - Does `IModelDoc2.EditUndo2(n)` (the fork's `undo`) step back one rebuild at a time or a whole run? How deep is the stack after 46 steps?
  - Settle D5 and what the description says after an interruption.
- [ ] 1.5 The bar scenario on SolidWorks.
  - Build the spec's 10 × 10 × 100 bar with an angle mate, step 0 to 90 in 2°, slice at z = 5, and compare the outline's area with 8874.4 mm² and the wall's distances with 35, 17.14 and 10.
  - Record the area at `tolerance_mm` 0.2 and 0.1, and the point counts.
- [ ] 1.6 Record the results in design.md under "Probe results" and settle D2 to D5 and D7.

## 2. Adapter: joint stepping, swept region, motion check

- [ ] 2.1 `src/solidworks_mcp/utils/swept_region.py`: port hjbaard's `region_tools.py`.
  - The header keeps "Copyright (c) 2026 Henk Jan Baard" and the MIT permission notice in full, and says what was changed (`SolidWorksError` becomes `ValueError`; the grid-size guard).
  - Tests in `tests/solidworks_mcp/utils/test_swept_region.py`: the spec's square (100), two overlapping squares (150), a square with a hole, a margin of 2 on a 100 × 10 rectangle (1452.6 within 2%), the counter-clockwise rule, a zero tolerance, no loops, a grid over the limit.
- [ ] 2.2 `adapters/solidworks/joint_sweep.py`, pure helpers: `place_triangles`, `into_frame`, `part_box_reaches`, `wrap_mate_angle`, `moved`, `smallest_per_pair`.
  - Tests in `tests/solidworks_mcp/adapters/solidworks/test_joint_sweep.py`: placement against `placement.rotation_rows_from_deg`, a frame shifted by 10 mm, the wrap of -30 and 400, `moved` at the 1e-9 and 1e-6 mm limits, the smallest per pair with ties.
- [ ] 2.3 `SolidWorksJointSweepMixin` in the same module: the stepper (D2, D4, D5), `swept_region` (D3, D6, D7) and `check_motion` (D9).
  - Needs change `mesh-sections` (`utils/mesh_sections.section(triangles, axis, height)`). Its design D3 gives loops as `(u, v)` in the order `(y, z)`, `(x, z)`, `(x, y)`; re-check that against the merged module and adapt here if it changed.
  - Every COM call goes through `_handle_com_operation`. `limits.check` goes where probe 1.2 puts it (before each face read, before each part is placed and sliced at each step). Methods of new dispatch objects are flagged (pitfalls #41, #42).
  - Tests with a fake assembly beside `fake_turning.py`: the stepper writes, reads back and restores; a refused value restores; `OperationStopped` restores; a failed restore sets `restored` false and puts the original in the message; nothing moved fails; a moving frame fails; a part box that misses the heights isn't sliced.
- [ ] 2.4 `adapters/solidworks/io.py`: move the body of `check_interference`'s `_check` into a module function `interference_report(adapter, coincident, include_multibody, ignore_hidden, wanted_names)`; the adapter method calls it. No behaviour change.
  - The existing `check_interference` tests are the guard. Add one for the module function. Do this before 2.3 uses it.
- [ ] 2.5 `adapters/base.py`: the two abstract methods (D8). Forward them in `connection_pool.py` and `circuit_breaker.py`, with tests beside each (`test_base_joint_sweep.py`, `test_connection_pool_joint_sweep.py`, `test_circuit_breaker_joint_sweep.py`). Add the mixin to `PyWin32Adapter`'s bases in `pywin32_adapter.py`.

## 3. Mock adapter

- [ ] 3.1 `mock_adapter.py`: the joint table `mock_joints` (D11), `swept_region` (box triangles, the same slicing and `swept_outline`), `check_motion` (box distances, `clash_free: null` with a note), and the real adapter's refusals.
  - Tests in `tests/solidworks_mcp/adapters/test_mock_adapter*.py`, including the spec's "Mock bar" and "Mock distances" and "A mock joint" scenarios.

## 4. Tools

- [ ] 4.1 `tools/analysis.py`: `SweptRegionInput` and `CheckMotionInput` (Pydantic, `CompatInput`) and the two tools, beside `rotation_clearance`.
  - `SweptRegionInput`: `values` and `heights_mm` with `min_length=1`; `axis` as `x`, `y` or `z`; `margin_mm` at least 0; `tolerance_mm` above 0. `CheckMotionInput`: `distances` as pairs of two names.
  - Refusals through `normalize_input`, `require_success` and `ToolOperationError`, with `error_type` `invalid_input`, `not_found` and `operation_stopped`.
  - Descriptions per `docs/agents/tool-description-standard.md`.
    - `swept_region` says to give each layer a few heights inside it and not on a face, to keep the step under width / reach in radians, that a coarser `tolerance_mm` gives fewer points, that the dimension is put back, and that tessellation is a display mesh.
    - `check_motion` says how it differs from `rotation_clearance`, and to use few values and pairs.
    - Both say what to do after an interrupted run (probe 1.4).
  - Tests in `tests/solidworks_mcp/tools/test_analysis_joint_sweep.py`.
- [ ] 4.2 `support_levels.py` (both `_UNCHECKED`) and `tool_hints.py` (`_sets`, D12).
  - An `error_classifier.py` rule only if the refusal messages don't already classify as `invalid_input` / `not_found` / `operation_stopped`.
  - Tests beside each.

## 5. Docs

- [ ] 5.1 Regenerate the tool catalog (`src/utils/generate_tool_catalog.py`) and the progress files (`docs/assets/progress/`). Check `src/utils/verify_tool_count.py`.
- [ ] 5.2 Agent skill:
  - `references/workflows.md` gains "Checking a joint through its range": find the dimension in `list_model_dimensions`, `check_motion` with fine steps, `swept_region` for the keep-out outline.
  - `references/errors-and-limits.md` gains the refusals and the interrupted-run advice.
- [ ] 5.3 `docs/agents/com-api-pitfalls.md`: entries for what probes 1.1 to 1.4 found (angle-mate values and units, nested `Transform2`, scale, interrupted runs).
- [ ] 5.4 `.\dev-commands.ps1 dev-docs-strict`.

## 6. Licence

- [ ] 6.1 `THIRD_PARTY_NOTICES.md` at the repo root: create it, or extend it if change `mesh-sections` has added it. Add `utils/swept_region.py` from hjbaard's `solidworks-mcp` (`region_tools.py`), MIT, © 2026 Henk Jan Baard, with the full permission notice.
  - Check the file is included in the sdist and wheel if the project's packaging lists licence files.
- [ ] 6.2 `design.md`'s Context credits hjbaard's `session.py` for the COM order (already written); re-read it before the PR.

## 7. Live

- [ ] 7.1 `tests/live/test_live_sw_joint_sweep.py`, one test per spec scenario that touches SolidWorks:
  - the bar's quarter-turn area (8874.4 within 1%) and its point checks;
  - one value (1000), a margin of 2 (1452.6 within 2%), the plate with a hole (1521.5), a frame 10 mm along X;
  - a height the part never reaches;
  - `check_motion` with the wall: distances 35, 17.14, 10 and `clash_free`; then with 60° (a clash at 60 only);
  - a refused value, a suppressed mate and a broken mate each leave the dimension at its original value;
  - the 20-part sub-assembly finishes within the operation time limit, or stops with `operation_stopped` and restores.
- [ ] 7.2 Run `tests/live/test_live_sw_regression.py`: the new mixin touches `pywin32_adapter.py`, and `check_interference` was refactored (2.4).
- [ ] 7.3 Record the results in design.md under "Live results", with the SolidWorks versions signed off.

## 8. Gate

- [ ] 8.1 `.\dev-commands.ps1 dev-lint` and `.\dev-commands.ps1 dev-test` (report counts only).
- [ ] 8.2 Live sign-off by the user. Then raise the support levels to `stable` for the tools the live test proved.
- [ ] 8.3 Update `openspec/queue.md` (item 24). Archive the change after its PR merges.
