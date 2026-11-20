## Context

The fork already has the pieces this change builds on:
- `list_components` (`tools/modeling.py`, `adapters/solidworks/io.py`) gives top-level instance names (`bracket-1`). Every component tool takes these names.
- `io._find_components` finds top-level components by instance name, and `component_visibility.find` finds them at every level (`sub-1/part-1`).
- `insert_component` loads the file silently (`OpenDoc6`), re-activates the assembly through `io.activate_doc`, adds the component and places it with `placement.place`.
- `placement.py` reads `IComponent2.Transform2` (`_transform_data`) and turns it into `origin_mm` and `rotation` rows (`describe`).
- `rebuild_inspector.collect_rebuild_issues` reads `GetWhatsWrong` and `rebuild_diagnostics.py` gives each issue a cause and a hint. A mate error is one of these (`swFeatureError_e` has dangling and over-defined mate codes).
- `saving.modified_references` walks the assembly's live components, so a replaced component's new file is seen by the save guard without any change there. `references.list_references` and `pack_and_go_assembly` read the file on disk through `GetDocumentDependencies2` and `GetPackAndGo`.
- `dialogs.py` (the watchdog) and `window.py` (the minimized state) wrap every `_handle_com_operation` call (`keep-solidworks-minimized`).

Nothing in the fork replaces a component or sets a colour. `set_material` (`tools/engineering_data.py`) sets a material by name and has no colour.

hjbaard's `solidworks-mcp` (MIT, © 2026 Henk Jan Baard; `session.py` `replace_component` and `set_appearance`) is the reference:
- **Replace:** select the component with `Select4`, call `AssemblyDoc.ReplaceComponents2(path, "", all_instances, 0, reattach_mates)`, clear the selection, `ForceRebuild3(False)`. Success is read from the component list, not from the return value: the new component is the one whose path is the new file and whose name is new or the old one. It then lists the mates in error.
- **Appearance:** nine doubles, `[red, green, blue, ambient, diffuse, specular, shininess, transparency, emission]`, each 0..1. A part reads and writes `MaterialPropertyValues`. A component reads `GetMaterialPropertyValues2(1, None)`; a first value below 0 means it has no colour of its own yet, so it starts from the part's values. It writes `SetMaterialPropertyValues2(values, 1, None)`, then `GraphicsRedraw2`.
- **Two traps it found:** a plain Python list is ignored without a word, so the array must be a typed `VT_ARRAY | VT_R8` VARIANT; and SolidWorks keeps colours in steps of 1/255, so the read-back is compared with that tolerance.

`swconst_enums.json` confirms the constants this change uses:
- `swReplaceComponentsConfiguration_e`: `MatchName` = 0, `ManuallySelect` = 1.
- `swInConfigurationOpts_e`: `swThisConfiguration` = 1, `swAllConfiguration` = 2, `swSpecifyConfiguration` = 3.
- `swAppearanceTargetType_e`: `Part` = 3, `Component` = 4.
- `swReplaceComponentError_e` includes `NotTopLevelComponent` = 6 and `SameFile` = 5. The fork can't yet say which call returns these (probe 1.1).

Every COM call stays on the connect thread through `_handle_com_operation`. New dispatch objects get their methods flagged before use (CLAUDE.md, method flagging; pitfalls #41, #42). This change is written fresh in the fork's adapter style, with hjbaard's code as the reference for call order and arguments.

## Goals / Non-Goals

**Goals:**
- An agent can put another part or assembly file in a component's place, keep its position and its mates where they still work, and see which mates did not survive.
- An agent can colour a part, or one component in an assembly, and make it see-through, without editing the part's file when it only means to change how the assembly looks.
- A replace doesn't hang on a SolidWorks dialog, and doesn't bring up a minimized SolidWorks.

**Non-Goals:**
- Choosing a configuration of the new file (`swReplaceComponentsConfiguration_e.ManuallySelect`). The new file's configuration is matched by name (D2).
- Repairing mates by hand. A mate that fails is reported; `add_mate` and `set_component_transform` already exist.
- Colour of faces, features or bodies, textures, materials from the render library, and `set_appearance` for a drawing.
- Appearance in every configuration or display state (D8, open question).
- Replacing a component inside a sub-assembly (SolidWorks takes top-level components only).
- Keeping the old part's BOM item number (`swKeepReplacedCompOption_e`).

## Decisions

### D1. `replace_component` takes top-level components by instance name

- The input is `component` (as `list_components` reports it), `path`, `reattach_mates` (default true) and `all_instances` (default false).
- The component is found with `io._find_components`, the lookup `set_component_transform` uses. A name with a `/` (a part inside a sub-assembly) is `invalid_input` before SolidWorks is called, because SolidWorks replaces top-level components only (`swReplaceComponent_NotTopLevelComponent`). The message names the sub-assembly to replace in instead.
- An unknown name is `not_found`, and the message lists the assembly's components.
- `path` must exist and end in `.sldprt` or `.sldasm`. A missing file is `not_found`; another extension is `invalid_input`. Both are caught before SolidWorks is called.
- The active document must be an assembly (`wrong_document_type`).

### D2. The call order

Inside one `_handle_com_operation`:
1. Read the component's placement and box (D4), and the mate issues before (D5).
2. Load the new file silently, as `insert_component` does (`OpenDoc6` with by-ref errors). Re-activate the assembly through `io.activate_doc`, never `ActivateDoc3(..., 0)`, which shows the rebuild prompt (runbook #14). Probe 1.1 shows whether loading first is needed.
3. Clear the selection, `Select4` the component alone, call `ReplaceComponents2(path, "", all_instances, 0, reattach_mates)`, clear the selection.
4. `ForceRebuild3(False)` (hjbaard's call). Probe 1.1 records whether `EditRebuild3` is enough.
5. Read the components again and find the new one (D3).

Arguments: the config name is `""` and the fourth argument is `0` (`swReplaceComponentsConfiguration_e.MatchName`). The fifth is `reattach_mates`. Probe 1.1 settles what an empty configuration name picks when the new file has no configuration of the old one's name, and what `all_instances` replaces.

### D3. Success is read from the component list

The return value of `ReplaceComponents2` isn't trusted (hjbaard). Before the call the adapter records the instance names. Afterwards the new component is the one whose `GetPathName()` is the new file (compared after `normcase` and `abspath`) and whose name is new or the old name.
- No such component: the call fails, naming the component and the path. The reply says what the assembly now holds.
- With `all_instances`, every such component is a new one. `replaced` lists the old names that are gone; `components` holds one entry per new component. Probe 1.1 settles whether the old-to-new pairing can be read, or only the two lists.
- Replacing with the file the component already uses may be refused (`swReplaceComponent_SameFile`). Probe 1.1 settles it. A part made anew and saved under the same path, which shows no geometry (hjbaard), is the case it must handle.

### D4. Placement and box

- The new component keeps the old one's `Transform2`, so the new file's own origin lands where the old origin was (probe 1.4 confirms it with a file whose origin sits elsewhere).
- The reply holds `placement` (`origin_mm`, `rotation` rows, `fixed`), read back with `placement._transform_data` and `placement.describe`. A small public `placement.read(adapter, component)` is new, so the adapter doesn't call a private function.
- `bounding_box_mm` is `[xmin, ymin, zmin, xmax, ymax, zmax]` in assembly coordinates, from `IComponent2.GetBox`. `bounding_box_before_mm` is the same for the old component. Probe 1.4 checks that `GetBox` gives assembly coordinates, unlike `IFace2.GetBox` (`clearance.py`).
- The tool doesn't try to compensate for a different origin. The agent sees both boxes and can use `set_component_transform`.
- A component whose box can't be read gets `bounding_box_mm: null` and a `warning`, as in hjbaard ("shows no geometry").

### D5. Mate errors

`mate_errors` lists the mates that are in error after the replace. Each entry has `mate` (feature name), `type`, `error` (the cause, from `rebuild_diagnostics`) and `new` (false when the mate was already in error before the call).
- **Source:** `collect_rebuild_issues` before and after, keeping the issues whose feature type is a mate. Probe 1.3 lists the type names.
- **Scope:** the mates of the replaced components (`IComponent2.GetMates()`; `set_component_transform` already counts them). Probe 1.3 settles whether `GetMates` or the whole assembly is the better scope.
- **Lost references:** hjbaard also finds a mate whose entity lost its face without any error code (`IMate2.MateEntity(i).Reference` is null). Probe 1.3 shows whether `GetWhatsWrong` misses those after a replace. If it does, the adapter adds that check, and the entry's `error` says "a face or edge it used is gone".
- A mate in error isn't a failure: the call succeeds, and the reply has a `warning` that names them and points to `add_mate` and `list_features`.
- **`reattach_mates: false`:** SolidWorks leaves the mates unresolved. Probe 1.3 records what they read as. The reply still lists them.

### D6. Nothing is saved; the new path is seen where it should be

- The assembly is changed in memory and is dirty. The reply says `saved: false`.
- `saving.modified_references` and the save guard read live components, so they see the new file, and the old file, still loaded, isn't part of the save. No change to `saving.py` is needed; task 2.4 proves it with a test and a live check.
- `list_references` and `pack_and_go_assembly` read the file on disk. They show the old path until `save_assembly`. The tool description says so. The reply carries a `note` with the same words.
- The replace never writes the new file. If the new file is a modified file of the user's, the existing confirmation rules apply when the assembly is saved.
- Probe 1.5 confirms which path each reader sees before and after a save. Whether `undo` restores the old component is a probe (1.1). The description says so only if it does.

### D7. Dialogs, the minimized window and the active document

- `ReplaceComponents2` may show a dialog: a confirmation, or one about mates that can't be kept. Probe 1.2 runs it with SolidWorks normal and minimized, with the watchdog on and off, and records what appears and what stays foreground.
- If the watchdog cancels a dialog, the call fails with `solidworks_dialog` (the existing classifier rule). The adapter re-reads the components and says in the message whether the replace happened. It never presses a button other than Cancel.
- Probe 1.2 also settles whether an additional step is needed to keep the replace silent, such as the same loading order as D2. If a dialog can't be avoided, the tool description says so and gives the watchdog's timeout.
- Loading the new file makes it the active document. Step 2 of D2 re-activates the assembly inside the same operation, so the next call doesn't fail with `active_document_changed` (runbook #16).
- A minimized SolidWorks must be minimized afterwards. The live test reads `set_solidworks_window` state before and after.

### D8. `set_appearance`: part, component, and what stays

- **Part:** with no `component` in a part, the colour goes on the part itself (`MaterialPropertyValues`). The part is changed, so its file is dirty and the save guard sees it.
- **Component:** in an assembly, `component` is required, and the colour is set on the component in the assembly (`SetMaterialPropertyValues2`). The part's own file keeps its colour. Why: one part can sit in two assemblies, or twice in one, and colouring one picture shouldn't change the user's part file or the other uses.
- **An assembly with no `component`** is `invalid_input` (it names `list_components`). So is a `component` given for a part. Colouring a whole assembly can come later.
- **Component lookup:** `component_visibility.find`, at every level, since colour has no top-level limit (`Leg-1/Thigh-1`). An unknown name is `not_found`.
- **Configuration:** this configuration only (`swThisConfiguration` = 1). Probe 1.7 confirms the option on a component. All configurations (2) is an open question.
- **What stays:** the call reads the nine current values first, changes only the fields given (`rgb` is values 0..2, `transparency` is value 7) and writes all nine. So a call with only `rgb` keeps the transparency, and one with only `transparency` keeps the colour. Ambient, diffuse, specular, shininess and emission are never changed.
- **Where the first read comes from:** a component with no colour of its own reads a first value below 0. Then the part's `MaterialPropertyValues` is the start, or the defaults `(0.8, 0.8, 0.8, 1.0, 1.0, 0.5, 0.4, 0.0, 0.0)` when the part isn't loaded (hjbaard's). Probe 1.7 confirms the sentinel.
- **Inputs:** `rgb` is three whole numbers 0..255 (Pydantic, `invalid_input` otherwise). `transparency` is 0 (solid) to 1 (clear). Probe 1.8 confirms the direction with a picture. At least one of the two is required.

### D9. Is the legacy colour enough? (probe 1.8 decides)

`MaterialPropertyValues` is the legacy colour. A part that already has an appearance (from the library, or on a face or a feature) may keep showing that appearance, and then the read-back would say "set" while the picture is unchanged. Probe 1.8 settles it by taking a picture of a part and a component with an existing appearance before and after, with `export_image`, and comparing pixels.
- If the legacy colour wins: keep D8 as written.
- If an existing appearance wins: `set_appearance` uses `IAppearanceSetting` or `RenderMaterial` on the part or component (`swAppearanceTargetPart` or `swAppearanceTargetComponent`) instead. The input and reply don't change. Only `appearance.py` does.
- If it depends on the kind of appearance: the reply carries a `warning` when an existing face or feature appearance is found, and the description says to clear it in SolidWorks.

### D10. The read-back is in the reply

`set_appearance` returns `rgb` and `transparency` as SolidWorks reads them after the write, so a separate `get_appearance` isn't needed. The values are compared with the request at 1/255 (SolidWorks stores colours in steps of 1/255). A difference larger than that fails the call with a message that shows what SolidWorks reads: "SolidWorks did not take the appearance".
A read-only `get_appearance` would help to report a model's colours without changing them. It is left for later (Open Questions), because queue items 11 and 12 want a shorter tool list.

### D11. The adapter interface

New abstract methods on `SolidWorksAdapter` (`adapters/base.py`):

```python
async def replace_component(
    self,
    component: str,
    path: str,
    reattach_mates: bool = True,
    all_instances: bool = False,
) -> AdapterResult[dict[str, Any]]: ...

async def set_appearance(
    self,
    rgb: list[int] | None = None,
    transparency: float | None = None,
    component: str | None = None,
) -> AdapterResult[dict[str, Any]]: ...
```

- Pure helpers, unit-tested without COM:
  - `component_replace.py`: `new_components(before, after, path, old_name)`, `mate_issues(issues, before)`.
  - `appearance.py`: `merge_values(current, rgb, transparency)`, `rgb_of(values)`, `values_match(a, b)`, `DEFAULT_VALUES`.
- `SolidWorksComponentReplaceMixin` and `SolidWorksAppearanceMixin` hold the COM part, and `PyWin32Adapter`'s bases gain both.
- `connection_pool.py` and `circuit_breaker.py` forward the two methods.

### D12. The mock

`mock_adapter.py` already keeps `_components`, `_component_files`, `_component_states` and `_component_transforms`.
- **Replace:** the component's file becomes `path`, and its name becomes `<file stem>-1` (made unique). Its transform and fixed state stay. `all_instances` swaps every component with the same file. A path that doesn't exist is `not_found`, as in the real adapter. The text `__refuse__` in the path is refused, so tests can drive the failure.
- **Mate errors:** a canned list per assembly (`self._mate_errors`), returned as `mate_errors`, with `new` set against the list before the call.
- **Appearance:** a table of nine values per part and per component. A component with no entry starts from its part's. The same merge and 1/255 rounding as the real adapter.

This mirrors payload shapes, not SolidWorks' solver.

### D13. Support levels and hints

- Both tools start `experimental` (`_UNCHECKED`) and move to `stable` when `test_live_sw_replace_appearance.py` passes on SolidWorks 2025 and 2026.
- Hints: `replace_component` is `_discards` (it drops the old component and can lose mates; each call acts again). `set_appearance` is `_sets`.
- Both are in the `modeling` family, so they take the `document` target (`tools/target_document.py`).

## Risks / Trade-offs

- **A replace can quietly lose mates.** SolidWorks reattaches only where faces still match. Mitigation: `mate_errors` after every replace, a `warning`, and a lost-reference check if probe 1.3 shows `GetWhatsWrong` misses them.
- **A different origin moves the geometry.** The component keeps its transform, so a new file with its origin elsewhere ends up displaced. Mitigation: both boxes in the reply, and `set_component_transform` to put it right (probe 1.4).
- **A dialog may open.** Mitigation: probe 1.2, the watchdog, and the existing `solidworks_dialog` error. The risk is not fully settled until the live run on a minimized SolidWorks.
- **The colour may not show.** An existing appearance can win over the legacy colour (D9). Mitigation: probe 1.8 by picture before any code, and a read-back that also catches a refused write.
- **Silent array marshalling.** A plain list is ignored without a word (hjbaard). Mitigation: the typed VARIANT array, a read-back after each write, and a pitfall entry.
- **The saved file lags the session.** `list_references` and `pack_and_go_assembly` read the file on disk. Mitigation: `saved: false` and a note in the reply, and the description.
- **Tool count.** Two more tools while queue items 11 and 12 work to shrink the list. Both belong to `modeling`, which stays a core family.

## Probe results

To fill in after task group 1.

## Live results

To fill in after task group 7.

## Open Questions

- Should `set_appearance` accept `configurations` (all, or by name) now, or wait for a request? D8 sets this configuration only.
- Should a read-only `get_appearance` exist, or is the read-back in `set_appearance`'s reply enough (D10)?
- Should `replace_component` take a `configuration` of the new file (`ManuallySelect`)? D2 matches by name. It depends on what probe 1.1 shows for a file whose configurations differ.
- Should `set_appearance` colour a whole assembly, without a `component`? D8 refuses it.
