## 1. Probes (SolidWorks, scratch documents, each with the user's OK)

- [ ] 1.1 `tests/scripts/probe_shell_rib_full_round.py`, shell.
  - On a 40 × 40 × 20 mm block, find which call exists and works: `IModelDoc2.InsertFeatureShell(thickness_m, outward)` (hjbaard) or `IFeatureManager.FeatureShell2` / `InsertFeatureShell` (the stopgap macro in `tools/vba_generation.py`). Record its arguments, its return value and the new feature's `GetTypeName2`.
  - Try zero, one and two open faces, selected with `Select4(append, SelectData)`: which mark, if any, does it need? Does zero faces need a selected body or face?
  - Try `outward` true and false, and read the volume and box each time against the spec's numbers (8672, 11264, 7376, 10592 mm³).
  - Try a thickness of 25 mm: what does it return, and does the tree keep a failed shell?
  - On a part with two solid bodies: which body does a face selection shell, and can a closed hollow name a body?
  - Record the results in design.md under "Probe results", and settle D3 and the `body` question.
- [ ] 1.2 Rib.
  - On the L-bracket from the rib spec, sketch the line and call `IFeatureManager.InsertRib` with hjbaard's arguments. Name each of the ten arguments on SolidWorks 2026; check the two-sided flag gives a rib centred on the plane, and try `swRibType_e` and `swRibExtrusionDirection_e` values (`data/swconst_enums.json`).
  - Which sign of the line's side means `reverse = True`, in the sketch's own axes? Do it for a sketch on the Front Plane, an offset plane, and a plane made from a face (non-world axes).
  - What does a line that meets no material on the chosen side do: an error, no new feature, or a feature with an error flag?
  - Record the new feature's type name, and whether the sketch stays selected after `exit_sketch`.
  - Settle D4 and D5.
- [ ] 1.3 Full round.
  - On the plate-and-rib part from the spec, select side 1, the top and side 2 with marks 2, 512 and 4 (hjbaard's) and call `FeatureFillet3` with type 3. Try `IFeatureManager.FeatureFillet3` (15 arguments) and `IModelDoc2.FeatureFillet3` (9 arguments) and record which builds, and what each returns.
  - Check the removed volume against 40 × 4² × (1/2 − π/8) = 68.6726 mm³, and that `get_feature_definition` reads `full_round`.
  - Try wrong marks, to see the failure.
  - Check `GetPersistReference3` on faces (to count each side face once), and `GetTwoAdjacentFaces2` on a top face's edges.
  - Settle D6.
- [ ] 1.4 Faces by point and side on shelled and ribbed parts.
  - On a shelled block, does `FaceEdgeSelector.face_at_point` return the inner face for a point on it, and `pick_side_face` the outer skin for `side`?
  - Does a `face_point` on the curved top of a full round resolve to a face?
  - Settle D2.
- [ ] 1.5 Definitions (for the open questions, not required for the tools).
  - On a shell and a rib, `IFeature.GetDefinition`: which data interface, and which members hold the thickness, the open faces and the side.
  - Record them in design.md. This decides whether `edit_feature` and `get_feature_definition` learn these features.

## 2. Adapter

- [ ] 2.1 `adapters/solidworks/shell_rib.py`, shell, with the call settled in 1.1.
  - Resolve each face with `FaceEdgeSelector.resolve`, check they are on one body, select them with `SelectData`, call the shell, find the new feature by tree diff.
  - Check the tree count and the volume; delete a failed shell.
  - Every COM call goes through `_handle_com_operation`; dispatches are flagged.
  - Tests in `tests/solidworks_mcp/adapters/solidworks/test_shell_rib.py` with fake faces and a fake model: the face checks, a faces-on-two-bodies refusal, a refusal when the tree didn't grow.
- [ ] 2.2 Same module, rib.
  - Pure `rib_reversed(start, end, toward)` (the sign from 1.2; a point on the line refused).
  - `create_rib(sketch_name, thickness_mm, reverse)`: select the sketch, call `InsertRib`, find the feature by type.
  - With `reverse=None`, make it one way, read `_part_volume`, delete it with `_delete_feature_impl`, make it the other way, and keep the larger (D5). Either import the two helpers from `features.py` or move them to a shared module.
  - Tests: both sides, one side empty, both empty, equal volumes with a warning, `rib_reversed` on all four quadrants.
- [ ] 2.3 `adapters/solidworks/full_round.py`.
  - Pure `full_round_sides(sides)` (D6) and a face-reference validator.
  - `add_full_round(top, side_faces)`: the side faces along `top`'s edges (each once), the three face sets with marks, `FeatureFillet3` type 3 in the form settled by 1.3, then the feature by tree diff if a number came back.
  - Tests in `test_full_round.py`: a rib's sides, a square post (tie refused), a prism (no pair), explicit `side_faces`, no new feature.
- [ ] 2.4 Interface.
  - `adapters/base.py`: the three abstract methods (D7). Forward them in `connection_pool.py` and `circuit_breaker.py`, with tests beside each.
  - Add both mixins to `PyWin32Adapter`'s bases in `pywin32_adapter.py`.

## 3. Mock adapter

- [ ] 3.1 `mock_adapter.py`: `create_shell` (D8: thickness check, `_mock_face_at`, a `Shell` feature).
  - Tests in `tests/solidworks_mcp/adapters/test_mock_adapter*.py`, including the spec's "Mock refusal".
- [ ] 3.2 `mock_adapter.py`: `create_rib` (a `Rib` feature, the same input refusals) with tests.
- [ ] 3.3 `mock_adapter.py`: `add_full_round` (the same refusals, `width_mm: null` and a note) with tests.

## 4. Tools

- [ ] 4.1 `tools/modeling.py`, shared: a `FaceRef` model (`face_point` or `side`, exactly one, `side` checked with `side_vector`).
- [ ] 4.2 `tools/modeling.py`: `create_shell` (`CreateShellInput`: `thickness_mm` above 0, `open_faces: list[FaceRef]` defaulting to empty, `outward`).
  - Reads the summary before and after (`_read_summary`, `_attach_measured_change`).
  - The description says: shell before cutting openings; a face by point reaches inner faces; one body at a time.
  - Tests in `tests/solidworks_mcp/tools/test_modeling_shell.py`.
- [ ] 4.3 `tools/modeling.py`: `create_rib` (`CreateRibInput`: `plane`, `start_mm`, `end_mm`, `thickness_mm`, `toward_mm`).
  - Sketches as `emboss_text` does (`create_sketch`, `plane_frame.project`, `add_line`, `exit_sketch`), deletes the sketch on failure, then calls the adapter with `reverse` from `rib_reversed` or `None`.
  - The description says `toward_mm` saves a rebuild.
  - Tests in `test_modeling_rib.py`.
- [ ] 4.4 `tools/modeling.py`: `add_full_round` (`AddFullRoundInput`: `top`, `side_faces` of two). Tests in `test_modeling_full_round.py`.
  - Each description follows `docs/agents/tool-description-standard.md`.
- [ ] 4.5 `support_levels.py` (all three `_UNCHECKED`) and `tool_hints.py` (`_adds`, D9).
  - An `error_classifier.py` rule only if a refusal doesn't classify as `invalid_input` or `not_found`.
  - Tests beside each.
- [ ] 4.6 `tools/vba_generation.py`: the `generate_vba_part_modeling` description (D11); fix or remove the shell macro's call if 1.1 shows `FeatureShell2` doesn't exist. Update `tests/solidworks_mcp/tools/test_vba_generation.py`.

## 5. Docs

- [ ] 5.1 Regenerate the tool catalog (`src/utils/generate_tool_catalog.py`) and the progress files (`docs/assets/progress/`). Check `src/utils/verify_tool_count.py`. Update `docs/user-guide/tool-catalog/vba-generation.md` for the new description.
- [ ] 5.2 Agent skill:
  - `references/workflows.md` gains "Hollow a part, add ribs, round a rib's top".
  - `references/vba-fallback.md` no longer lists a shell as a case with no tool.
- [ ] 5.3 `docs/agents/com-api-pitfalls.md`: entries for what probes 1.1–1.4 found (the shell call, `InsertRib`'s arguments and side, the full-round marks and `FeatureFillet3` form).
- [ ] 5.4 `.\dev-commands.ps1 dev-docs-strict`.

## 6. Live

- [ ] 6.1 `tests/live/test_live_sw_shell_rib_full_round.py`, one test per spec scenario that touches SolidWorks. Face and rib points come from the measured bounding box, as `tests/live/coupon.py` does, not from assumed axes (#199).
  - Shell: 40 × 40 × 20 mm block, 2 mm: top open 8672 mm³; closed 11264; two open faces 7376; outward 10592 with a 44 × 44 × 22 box; by side and by point alike.
  - Shell refusals: 25 mm wall, point on no face, faces on two bodies, closed hollow on two bodies; the part is unchanged each time.
  - `check_wall_thickness` on the open box reads 2 mm.
  - Rib: the L-bracket's 1800 mm³ gusset, 22000 to 23800 mm³, with `toward_mm`; both line orders without it, one `direction_flipped`; a line in free space refused with its sketch deleted.
  - Full round: the 4 × 40 mm rib removes 68.6726 mm³, box unchanged, `get_feature_definition` says `full_round`; the square post refused, then 107.3009 mm³ with `side_faces`.
- [ ] 6.2 Run `tests/live/test_live_sw_regression.py`: two new mixins touch `pywin32_adapter.py`.
- [ ] 6.3 Record the results in design.md under "Live results", with the SolidWorks versions signed off.

## 7. Gate

- [ ] 7.1 `.\dev-commands.ps1 dev-lint` and `.\dev-commands.ps1 dev-test` (report counts only).
- [ ] 7.2 Live sign-off by the user. Then raise the support levels to `stable` for the tools the live test proved.
- [ ] 7.3 Update `openspec/queue.md`. Archive the change after its PR merges.
