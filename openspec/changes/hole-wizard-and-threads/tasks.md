## Status (2026-11-20, branch `threads`)

Sections 2 to 6 and the docs of 7.1 and 7.2 are built without SolidWorks (mock and fake-COM tests only; another session held SolidWorks). Nothing in section 1 has run, so every decision a probe was to settle is still open, however the code reads: the `HoleWizard5` value vectors are hjbaard's (they agree with the API help's Remarks), the position pin (D3), the size properties read back (D5), the library spellings, the thread's direction from the edge, the through length of a modeled hole (D7) and `MAX_THREAD_TURNS` (a provisional 100, D9). `tests/scripts/probe_hole_wizard.py` holds every probe as a step, plus a `chamber` step for the imaging chamber's M30 x 1 threads.

What the code does beyond, or differently from, the design, for review:
- Both tools are in `tools/modeling.py`, so they take the `modeling` family's `features` group (`tools/tool_groups.py` now names holes and threads in that group's description). The COM code is in its own modules, `adapters/solidworks/holes.py` and `threads.py`; `features.py` is untouched.
- A hole whose centre lies on the origin's sketch x or y axis can't take a dimension of 0, so that coordinate gets a relation (points vertical or horizontal; coincident for both) and its `position.dimensions` name is null.
- Replies carry a little more than D11: `hole.thread` (`cosmetic` or `modeled`, what the hole got), `thread_feature` (the modeled thread's feature name), `library` (the profile file a thread used) and `failing_features`.
- The API help gives every `IThreadFeatureData` member as "SOLIDWORKS 2018 FCS, Revision Number 26.0" and `HoleWizard5` as 2014, not hjbaard's 2016 (D8); the `not_supported` messages say so.
- The mock keeps circular edges given by `mock_add_round_edge` and each hole's mouth; with none at `edge_point` it assumes an edge of the diameter the thread needs and says so in a `note`. Its size table holds only the spec's "to confirm" M3 values, marked as mock data (probe 1.4 has not run).
- No `error_classifier.py` rule was needed: the messages classify through the existing rules (tests check each one).

## 1. Probes (SolidWorks, scratch documents, each with the user's OK)

`tests/scripts/probe_hole_wizard.py` holds every probe below as a step (written 2026-11-20, not run). Each step builds its own scratch part (a 40 × 20 × 10 mm block, a Ø10 × 40 mm rod, a 30 × 30 × 20 mm block). Run it on SolidWorks 2025 and 2026 if both are installed, and record the revision of each (`RevisionNumber`).

- [ ] 1.1 `HoleWizard5`: the argument list and the value vectors.
  - Read the signature with `lookup_api_method` and `get_api_remarks` on the installed type library, and count the arguments (hjbaard passes 27).
  - Call it through late binding with the D4 enums, for each kind, through and blind. For each, measure the volume removed and compare it with the hand calculation from the sizes read back.
  - Re-prove the `Value1`…`Value12` vectors one by one. Record what a zero, a `-1` and a wrong slot do.
  - Record the results in design.md under "Probe results" and settle D4.
- [ ] 1.2 Picking the face.
  - `SelectByRay` from 0.01 mm above the point along the normal: the selection count after, with and without `ClearSelection2` first (pitfall #59).
  - Repeat in a side view where another face hides the target (the `ShowNamedView2` left view), on a block whose top is split in two by a slot, and over an earlier hole's countersink.
  - Settle D2.
- [ ] 1.3 Where the point lands.
  - Make a hole at `[20, 14, 10]` and read its edge's centre. Record the offset from the point (hjbaard saw about 0.04 mm).
  - Find the position sketch (a sub-feature) and check that it can be edited with `EditSketch` and its point dimensioned from the origin through the fork's own sketch calls, in the face sketch's own horizontal and vertical directions. Check `check_sketch_fully_defined`.
  - Check whether `HoleWizard5` takes a face plus pre-selected sketch points, so the position is exact from the start.
  - Settle D3.
- [ ] 1.4 The sizes SolidWorks reports.
  - Read `IWizardHoleFeatureData2` for each kind. Which properties hold the clearance, counterbore, countersink and tap-drill values, and in which unit.
  - Read M3 for close, normal and loose (the "to confirm" values in the specs: 3.2, 3.4, 3.6; counterbore Ø6.5 × 3.4; countersink Ø6.72 at 90°; tap drill 2.5, thread depth 6.0), and a few more sizes the live test will use.
  - Record what an unknown size (`M3.3`) does: `None`, or a hole.
  - Settle D5 and fill the mock's size table (D13).
- [ ] 1.5 The thread feature and its library.
  - `CreateDefinition(87)` gives an `IThreadFeatureData`; call `InitializeThreadData`; set `Type` to `Metric Tap` and `Metric Die`; read each back. Record the file it resolves to.
  - `ISldWorks.GetConfigurationNames(file)`: the sizes it lists for M3, M4 and M10, and how they are spelled (`M10x1.0` or `M10x1`).
  - Check each `IThreadFeatureData` member the design uses (`Edge`, `Size`, `EndCondition`, `BlindDepth`, `RightHanded`, `ThreadMethod`) for the right call form under late binding, and which interfaces need flagging.
  - Settle D6 steps 2 to 4.
- [ ] 1.6 Thread diameters.
  - External M10x1.5 on Ø10, Ø9.9 and Ø10.1 rods; internal on holes of Ø8.376, Ø8.5 and Ø8.2.
  - Record what SolidWorks cuts for each, to confirm the 0.01 mm rule and that an internal thread shifts with its hole.
  - Compare the volumes removed with the hand calculations in the threads spec. Settle D6 step 6.
- [ ] 1.7 Versions.
  - Record `RevisionNumber` and what `CreateDefinition(87)` and `HoleWizard5` do on each installed version.
  - Read the API help (`get_api_remarks`) for the version each call was added in, and record whether it agrees with hjbaard's "2016".
  - Older versions can't be probed here: record which were available.
  - Settle D8.
- [ ] 1.8 Rebuild time.
  - Time `CreateFeature` and the next `ForceRebuild3` for M3x0.5 at 10, 50 and 100 mm, and M10x1.5 at 20 and 100 mm.
  - Settle D9's `MAX_THREAD_TURNS`, and whether any step needs more than one `limits.check`.
- [ ] 1.9 A modeled tapped hole.
  - Through and blind, M3 and M4: the thread's start edge (the hole's mouth), its length (the `ThreadDepth`, or the through length), and how to read the through length.
  - Whether the cosmetic thread stays on beside the modeled one, and whether the drawing then shows both.
  - The volume ends of a through thread (hjbaard saw up to 0.5 mm³ off where it runs out of the plate).
  - Settle D7.
- [ ] 1.10 Failure and cleanup.
  - What `HoleWizard5` returns for an unknown size; what a refused `CreateFeature` leaves in the tree (a failed feature, or nothing).
  - That `delete_feature` on the hole removes its position sketch and thread, and the feature count and volume come back.
  - Settle D10.
- [ ] 1.11 Drawing import (pitfall #18).
  - On a drawing of a part with a Hole Wizard hole whose position was dimensioned by D3, run the fork's dimension import with `include_hole_wizard`.
  - Record whether the position dimensions arrive, once each.

## 2. Adapter: threads

- [x] 2.1 `adapters/solidworks/threads.py`, pure helpers: `parse_thread_size`, `minor_diameter`, `coarse_size`, `thread_turns`.
  - Tests in `tests/solidworks_mcp/adapters/solidworks/test_threads.py`: `M10x1.5` → (10, 1.5), `M3x0.5`, a decimal diameter, a bad form, `M10x1.5` minor diameter 8.3762, the coarse pitch from a list of library sizes, and the turn count.
- [x] 2.2 `SolidWorksThreadsMixin` in the same module: `create_thread` (D6).
  - Every COM call goes through `_handle_com_operation`. New dispatch objects are flagged (`IThreadFeatureData`, `IEdge`, `ICurve`), using the call forms settled in 1.5.
  - The circular-edge walk uses `PartIndex` and calls `limits.check` between edges (D9).
  - `not_supported` for a missing definition or library, with the revision (D8).
  - `ForceRebuild3`, `failing_features` from `rebuild_inspector`, `measured_change`.
  - Tests with a fake part (beside `fake_documents.py`): the size and diameter checks, a point on no edge, two candidate edges, a missing library, a refused `CreateFeature`, and the turn limit.

## 3. Adapter: Hole Wizard

- [x] 3.1 `adapters/solidworks/holes.py`, pure helpers: `wizard_values(kind, fit, through)` and `check_hole_input`.
  - Tests in `tests/solidworks_mcp/adapters/solidworks/test_holes.py`: the vectors measured in 1.1, each kind, through and blind; a fit with `tapped`; `modeled` with another kind; a bad size form; a depth of 0.
- [x] 3.2 `SolidWorksHolesMixin` in the same module: `create_hole_wizard_hole` (D2, D3, D4, D7, D10).
  - The face is found with `FaceEdgeSelector.face_at_point`, checked for flatness and `side`, then picked by `SelectByRay`.
  - The position is fixed by the call settled in 1.3. The sizes are read back as 1.4 found.
  - A modeled thread calls the thread routine from 2.2 on the hole's mouth.
  - `limits.check` between the steps. A failure after the first feature deletes what was made, rebuilds, and checks that the feature count and volume are back (D10).
  - `not_supported` for a missing `HoleWizard5`, with the revision (D8).
  - Tests with a fake part: each kind's payload, the position fix and its failure `warning`, a point on no face, on a cone and with a wrong `side`, an unknown size, and the cleanup after a refused thread.

## 4. Interface

- [x] 4.1 `adapters/base.py`: the two abstract methods (D12).
- [x] 4.2 `connection_pool.py` and `circuit_breaker.py` forward them, with tests beside each.
- [x] 4.3 Add both mixins to `PyWin32Adapter`'s bases in `pywin32_adapter.py`.

## 5. Mock adapter

- [x] 5.1 `mock_adapter.py`: `create_hole_wizard_hole` and `create_thread` (D13), the small size table from 1.4, the size and diameter checks, `__refuse_thread__`, and the feature list.
  - Tests in `tests/solidworks_mcp/adapters/test_mock_adapter*.py`, including the specs' "Mock cleanup path" and "Mock diameter check" scenarios.

## 6. Tools

- [x] 6.1 `tools/modeling.py`: `CreateHoleWizardHoleInput` and `CreateThreadInput` (`CompatInput`), and the two tools (D1, D11).
  - Input checks per the specs: `kind`, `fit` and `thread` as literals, a size pattern, `depth_mm > 0`, `face_point` and `edge_point` of three numbers, `length_mm > 0`.
  - Descriptions per `docs/agents/tool-description-standard.md`. They say that sizes come from SolidWorks, which diameter an internal thread needs and the drill-first order, that a modeled thread slows later rebuilds, that the position can be moved with `set_dimension`, and that a failed call changes nothing.
  - Errors only through `require_success`, `normalize_input` and `ToolOperationError`; no `try/except`.
  - Tests in `tests/solidworks_mcp/tools/test_modeling_holes.py`.
- [x] 6.2 `support_levels.py` (both `_UNCHECKED`) and `tool_hints.py` (D14).
  - `error_classifier.py` rules, only where the messages don't already classify: the `not_supported` message (it contains "does not support"), the missing-face message (`not_found`), and the diameter and size messages (`invalid_input`).
  - Tests beside each, with one per spec message.

## 7. Docs

- [x] 7.1 Regenerate the tool catalog (`src/utils/generate_tool_catalog.py`) and the progress files (`docs/assets/progress/`). Check `src/utils/verify_tool_count.py`.
- [x] 7.2 Agent skill:
  - `references/workflows.md` gains "Standard holes and printable threads": make the plate, call the hole tool with a size, read the sizes back, drill the minor diameter before an internal `create_thread`.
  - `references/errors-and-limits.md` gains the thread-size, diameter, `not_supported` and turn-limit refusals.
- [ ] 7.3 `docs/agents/com-api-pitfalls.md`: entries for what probes 1.1 to 1.10 found (the `HoleWizard5` vectors, the 0.04 mm landing, the unknown-size thread, rebuild time).
- [ ] 7.4 `.\dev-commands.ps1 dev-docs-strict`. (Not run 2026-11-20: mkdocs is not installed in this `.venv`.)

## 8. Live

- [ ] 8.1 `tests/live/test_live_sw_hole_wizard.py`, one test per spec scenario that touches SolidWorks, each checked against its hand calculation (written 2026-11-20 without the drawing import, which waits on probe 1.11; not run):
  - M3 clearance, close / normal / loose, through (80.425, 90.792, 101.788 mm³) and blind (57.567 mm³);
  - counterbore (172.745), countersink (110.299), tapped cosmetic blind (40.499), each also checked from the sizes the reply returns;
  - modeled M3 blind (45.10 within 0.2%) and M4 through (100.95 within 0.6);
  - position: edge centre within 0.01 mm of `[20, 14, 10]`, `fully_defined`, and `set_dimension` moves the hole; a split top face; a side view;
  - refusals: `M3.3`, a point in a countersink, a wrong `side`, `modeled` on a clearance hole;
  - external and internal M10x1.5 (268 and 120.4 mm³ within 0.5%), `M10x1.3`, the wrong-diameter refusals, a point in the air, and the turn limit;
  - the drawing import of the position dimensions (probe 1.11), if it passed.
- [ ] 8.2 Run `tests/live/test_live_sw_regression.py`: the two new mixins touch `pywin32_adapter.py`.
- [ ] 8.3 Record the results in design.md under "Live results", with the SolidWorks versions signed off.

## 9. Gate

- [ ] 9.1 `.\dev-commands.ps1 dev-lint` and `.\dev-commands.ps1 dev-test` (report counts only).
- [ ] 9.2 Live sign-off by the user. Then raise the support levels to `stable` for the tools the live test proved.
- [ ] 9.3 Update `openspec/queue.md` (item 21). Archive the change after its PR merges.
