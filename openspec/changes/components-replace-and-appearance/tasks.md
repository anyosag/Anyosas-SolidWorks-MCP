## 1. Probes (SolidWorks, scratch documents, each with the user's OK)

Scratch parts: three blocks with the origin at a corner (40 × 20 × 10, 50 × 20 × 10, 40 × 20 × 20 mm), one block whose origin is at its centre, and a plain disc. One scratch assembly places a block on a base with a coincident face mate and a concentric pin-in-hole mate.

- [ ] 1.1 `tests/scripts/probe_replace_appearance.py`: `ReplaceComponents2`.
  - Replace a component with each block, with the new file loaded and not loaded first. Record whether loading is needed (D2).
  - Try `(path, "", False, 0, True)` and `(path, "", True, 0, True)`. Record the return value, the new instance name, whether `all_instances` pairs old with new (D3), and whether `EditRebuild3` is enough in place of `ForceRebuild3(False)`.
  - Replace with a file whose configurations differ from the old one's, with config `""`. Record which configuration is used (D2, Open Questions).
  - Replace with the file the component already uses, and with a part made anew under the same path. Record the result and any `swReplaceComponentError_e` (D3).
  - Replace a part inside a sub-assembly. Confirm it is refused (D1).
  - Check whether `undo` restores the old component (D6).
- [ ] 1.2 Dialogs and the minimized window.
  - Run 1.1's replaces with SolidWorks normal and minimized, with the watchdog on (`SOLIDWORKS_MCP_DIALOG_TIMEOUT_SECONDS=5`) and off.
  - Record any dialog (title, text), whether it blocks, whether the window stays minimized, and whether SolidWorks takes the foreground.
  - Settle D7.
- [ ] 1.3 Mates.
  - Replace the block with a taller block (same bottom face), a block with another bottom face, and the disc, with `reattach_mates` true and false.
  - Record what `collect_rebuild_issues` returns for each mate: feature type names, codes, and whether a mate that lost its face has an error code.
  - Record what `IComponent2.GetMates()` returns for the new component, and the `IMate2.MateEntity(i).Reference` of a lost face.
  - Settle D5: the type names to filter on, the scope, and whether the lost-reference check is needed.
- [ ] 1.4 Origin and box.
  - Replace a corner-origin block with the centre-origin block, with no mates. Read `Transform2`, `GetBox` of the old and the new component, and the geometry's place in a picture.
  - Confirm `IComponent2.GetBox` gives assembly coordinates (D4).
  - Replace a component with a sub-assembly file and the reverse. Record what the box and name look like.
- [ ] 1.5 The saved file.
  - After a replace, call `list_references` and `GetPackAndGo` on the assembly's file before and after a save.
  - Confirm D6: the old path before the save, the new path after.
- [ ] 1.6 Material values marshalling.
  - On a part, read `MaterialPropertyValues`. Write nine doubles as a plain list, and as a `VT_ARRAY | VT_R8` VARIANT. Record which one takes.
  - Record the flagging `IComponent2.GetMaterialPropertyValues2` and `SetMaterialPropertyValues2` need (pitfalls #41, #42), and `GraphicsRedraw2`.
- [ ] 1.7 Component values.
  - On a component that was never coloured in the assembly, read `GetMaterialPropertyValues2(1, None)`. Confirm the first value is below 0 (D8).
  - Set a colour, read it back, save and reopen the assembly, and read it again. Check that the part's save flag stays false.
  - Try `config_opt` 1 and 2 (`swThisConfiguration`, `swAllConfiguration`) on an assembly with two configurations.
  - Colour a component inside a sub-assembly (`sub-1/part-1`).
- [ ] 1.8 Is the legacy colour enough?
  - Take a part with a library appearance, one with a face colour, and one with a feature colour. Set the colour with `MaterialPropertyValues` and with `SetMaterialPropertyValues2` on its component.
  - Take a picture with `export_image` before and after each. Compare the pixels, not the read-back.
  - Confirm the direction of `transparency` (0 solid, 1 clear) by picture.
  - If an existing appearance wins, find the call that works: `IAppearanceSetting` or `RenderMaterial` on `swAppearanceTargetPart` (3) or `swAppearanceTargetComponent` (4).
  - Record the results and settle D9.
- [ ] 1.9 Record every result in design.md under "Probe results", and settle D2 to D9.

## 2. Adapter: replace a component

- [ ] 2.1 `adapters/solidworks/component_replace.py`, pure helpers: `new_components(before, after, path, old_name)` and `mate_issues(issues, before)`.
  - Tests in `tests/solidworks_mcp/adapters/solidworks/test_component_replace.py`: a name that changes, a name that stays, `all_instances` with two new components, a path compared after `normcase`, no new component, and a mate issue that was there before.
- [ ] 2.2 `placement.read(adapter, component)`: the transform as `origin_mm` and `rotation`, for the adapter to call without a private function.
  - Tests in `test_placement.py`.
- [ ] 2.3 `SolidWorksComponentReplaceMixin` in the same module: `replace_component`.
  - The checks of D1, the call order of D2, the component list read of D3, the placement and boxes of D4, and the mate errors of D5.
  - Every COM call goes through `_handle_com_operation`; new dispatch objects are flagged; the new file is loaded and the assembly re-activated through `io.activate_doc` (runbook #14).
  - Tests with fakes (beside `fake_documents.py`): a replace, `all_instances`, no component left, an unknown name, a name with `/`, a missing file, a wrong extension, and a mate in error.
- [ ] 2.4 Save guard check.
  - Test that `saving.modified_references` finds the new component's file and not the old one after a replace.
  - Add a `note` and `saved: false` to the reply (D6).
- [ ] 2.5 `adapters/base.py`: the abstract `replace_component` (D11). Forward it in `connection_pool.py` and `circuit_breaker.py`, with tests beside each (`test_base_*.py` pattern). Add the mixin to `PyWin32Adapter`'s bases in `pywin32_adapter.py`.

## 3. Adapter: appearance

- [ ] 3.1 `adapters/solidworks/appearance.py`, pure helpers: `merge_values`, `rgb_of`, `values_match` and `DEFAULT_VALUES`.
  - Tests in `tests/solidworks_mcp/adapters/solidworks/test_appearance.py`: only `rgb`, only `transparency`, both, rounding to whole numbers, the 1/255 tolerance, a `-1` first value, and the nine values kept.
- [ ] 3.2 `SolidWorksAppearanceMixin` in the same module: `set_appearance` for a part and for a component (D8).
  - The typed VARIANT array settled in 1.6; the component lookup through `component_visibility.find`; the start from the part's values; `GraphicsRedraw2`; the read-back check.
  - If probe 1.8 says an existing appearance wins, the call that works (D9).
  - Tests with fakes: a part, a component, a component with no values of its own, a refused write, an unknown component, and the checks on input.
- [ ] 3.3 `adapters/base.py`: the abstract `set_appearance` (D11). Forward it in `connection_pool.py` and `circuit_breaker.py`, with tests beside each. Add the mixin to `PyWin32Adapter`'s bases.

## 4. Mock adapter

- [ ] 4.1 `mock_adapter.py` `replace_component` (D12): the file and name swap, the kept transform and fixed state, `all_instances`, the missing file, `__refuse__`, and the canned `mate_errors` with `new`.
  - Tests in `tests/solidworks_mcp/adapters/test_mock_adapter*.py`, including a replace followed by `list_components`.
- [ ] 4.2 `mock_adapter.py` `set_appearance`: the appearance table per part and per component, the same merge and rounding, and the same refusals as the real adapter.
  - Tests in the same files.

## 5. Tools

- [ ] 5.1 `tools/modeling.py`: `ReplaceComponentInput` (`component`, `path`, `reattach_mates`, `all_instances`) and `replace_component`; `SetAppearanceInput` (`rgb` as three ints 0..255, `transparency` 0..1, `component`; one of `rgb` and `transparency` required) and `set_appearance`.
  - Descriptions per `docs/agents/tool-description-standard.md`.
  - `replace_component` says: the assembly is changed in memory and not saved; `list_references` and `pack_and_go_assembly` read the saved file; mates that fail are in `mate_errors`; a different origin moves the geometry. It says whether `undo` restores it, per probe 1.1.
  - `set_appearance` says: in an assembly the colour belongs to the assembly and the part file keeps its own; what is left out stays; the reply is the read-back.
  - Errors through `require_success`, `normalize_input` and `ToolOperationError`, with no `try/except` in the tool.
  - Tests in `tests/solidworks_mcp/tools/test_modeling*.py`.
- [ ] 5.2 `support_levels.py` (both `_UNCHECKED`) and `tool_hints.py` (D13).
  - An `error_classifier.py` rule only if the refusal messages don't already classify as `invalid_input`, `not_found` or `wrong_document_type`.
  - Tests beside each.

## 6. Docs

- [ ] 6.1 Regenerate the tool catalog (`src/utils/generate_tool_catalog.py`) and the progress files (`docs/assets/progress/`). Check `src/utils/verify_tool_count.py`.
- [ ] 6.2 Agent skill (`src/solidworks_mcp/skills/solidworks-mcp/references/workflows.md`): "Swap a part in an assembly" (replace, read `mate_errors`, fix them, `save_assembly`) and "Colour an assembly's components" (a colour per component, a see-through housing with `export_image`).
- [ ] 6.3 `docs/agents/com-api-pitfalls.md`: entries for what probes 1.1 to 1.8 found (the replace call's arguments and dialogs, the typed array, the legacy colour).
- [ ] 6.4 `.\dev-commands.ps1 dev-docs-strict`.

## 7. Live

- [ ] 7.1 `tests/live/test_live_sw_replace_appearance.py`, one test per spec scenario that touches SolidWorks:
  - a 40 × 20 × 10 block replaced by a 50 × 20 × 10 block: both boxes as in the spec;
  - a mated block replaced by a taller one: no `mate_errors`; one that loses its face: the mate is listed;
  - `all_instances` with two instances;
  - a part made anew under the same path;
  - `save_assembly`, then `list_references` shows the new path and not the old one;
  - a replace with SolidWorks minimized: still minimized afterwards;
  - a part coloured, then a picture shows the colour (`export_image` pixels);
  - a component coloured, a second instance and the part file unchanged;
  - only `transparency`, only `rgb`.
- [ ] 7.2 Run `tests/live/test_live_sw_regression.py`: the new mixins touch `pywin32_adapter.py`.
- [ ] 7.3 Record the results in design.md under "Live results", with the SolidWorks versions signed off.

## 8. Gate

- [ ] 8.1 `.\dev-commands.ps1 dev-lint` and `.\dev-commands.ps1 dev-test` (report counts only).
- [ ] 8.2 Live sign-off by the user. Then raise the support levels to `stable` for the tools the live test proved.
- [ ] 8.3 Update `openspec/queue.md` (item 23). Archive the change after its PR merges.
