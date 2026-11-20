## Why

An agent can insert a component (`insert_component`), place it (`set_component_transform`), mate it, hide it and fix it. It can't swap one for another, and it can't colour anything:

- **Swapping a part.** Today the only way to put a different file in a component's place is to delete the component and insert again. That throws away every mate and the component's position. An agent that revises a bracket and wants the assembly to use the new file has to rebuild the mates by hand, or tell the user to do it.
- **A component that went blank.** A part made anew and saved under the same path can show no geometry in its assembly. Replacing the component is the cure, and nothing here does it.
- **Colour.** Nothing sets a colour or a see-through level. `set_material` sets the material, which changes mass but not what a picture of the part looks like. An agent that renders a cover-off picture (`export_image` with `hide_components`) can't make the parts tell apart, or make a housing see-through.

The comparison with hjbaard's `solidworks-mcp` (v0.21.0) found `replace_component` and `set_appearance` there and neither here. This change brings both into the fork, built the fork's way. No GitHub issue: issues are off on the fork `anyosag/SolidworksMCP-python`.

## What Changes

- **New tool `replace_component`:**
  - Takes `component` (an instance name as `list_components` reports it), `path` (the new `.sldprt` or `.sldasm`), `reattach_mates` (default true) and `all_instances` (default false).
  - The new file takes the old component's place. Its mates are kept where they still find their faces.
  - Returns the new component (`name`, `path`), its `placement` (origin, rotation, fixed) and `bounding_box_mm`, the `replaced` names (old to new), and `mate_errors`: the mates that are in error after the replace, each with its cause.
  - The assembly is changed in memory and not saved. The reply says so, and says that `list_references` and `pack_and_go_assembly` read the saved file.
- **New tool `set_appearance`:**
  - Takes `rgb` (three whole numbers 0..255), `transparency` (0 solid to 1 clear) and an optional `component`. At least one of `rgb` and `transparency` is required. What is left out stays as it was.
  - In a part, with no `component`, it colours the part. In an assembly, `component` is required and the colour belongs to the assembly: the part's own file keeps its own colour.
  - Returns the colour and transparency as SolidWorks reads them back, the `scope` (`part` or `component`) and the `component`.
- **No `get_appearance`.** The read-back in the reply covers the need. A separate tool would grow the tool list while queue items 11 and 12 work to shrink it (design D6).

## Capabilities

### New Capabilities
- `tools/assembly-component-replacement`: putting another file in a component's place, keeping its position and mates, and reporting the mates that fail.
- `tools/component-appearance`: colour and transparency of a part, or of one component in an assembly.

### Modified Capabilities
None. `tools/assembly-mates` and `tools/document-session-management` are untouched: `replace_component` finds components the way `set_component_transform` does, and takes the same `document` target as every `modeling` tool.

## Impact

- **COM/adapter code (extra risk per CLAUDE.md):**
  - New `adapters/solidworks/component_replace.py` (`SolidWorksComponentReplaceMixin`) on `IAssemblyDoc.ReplaceComponents2` and `IComponent2.Select4`, `GetMates`, `GetBox`, `Transform2`.
  - New `adapters/solidworks/appearance.py` (`SolidWorksAppearanceMixin`) on `IModelDoc2.MaterialPropertyValues` (a part) and `IComponent2.GetMaterialPropertyValues2` / `SetMaterialPropertyValues2` (a component), then `GraphicsRedraw2`.
  - Both go through `_handle_com_operation`. The new file is loaded silently and the assembly re-activated through `io.activate_doc`, as `insert_component` does (runbook #14).
  - `ReplaceComponents2` may open a dialog. The dialog watchdog (`adapters/solidworks/dialogs.py`) cancels it, and a minimized SolidWorks must stay minimized (`adapters/solidworks/window.py`). Probe 1.2 settles both.
  - `MaterialPropertyValues` is an array property and must be sent as a typed `VT_ARRAY | VT_R8` VARIANT. A plain list is silently ignored (hjbaard). Probe 1.6 settles the marshalling and the method flagging (pitfalls #41, #42).
  - `adapters/base.py` gets two abstract methods. `connection_pool.py` and `circuit_breaker.py` forward them. `pywin32_adapter.py` adds the two mixins.
  - `mock_adapter.py` gains a component swap and an appearance table (its own task).
- **Reused as they are:** `adapters/solidworks/io.py` (`_find_components`, `_as_com`, `activate_doc`), `placement.py`, `rebuild_inspector.py` (`collect_rebuild_issues`), `rebuild_diagnostics.py`, `saving.py` (the save guard).
- **Tools:**
  - `tools/modeling.py` beside `insert_component`. Inputs are Pydantic models (`ReplaceComponentInput`, `SetAppearanceInput`).
  - `support_levels.py` (both `_UNCHECKED`), `tool_hints.py`, `error_classifier.py` only if a refusal message doesn't already classify.
  - The tool catalog, the progress files, and the agent skill's `references/workflows.md` (a "swap a part" and a "colour an assembly" workflow).
- **Docs:** pitfall entries for what the probes find (the replace call's arguments, dialogs, material values marshalling).
- **Tests:**
  - unit tests beside each module;
  - a probe script `tests/scripts/probe_replace_appearance.py` (scratch documents);
  - a live test `tests/live/test_live_sw_replace_appearance.py`.
- **Credit:** hjbaard's `replace_component` and `set_appearance` (MIT, © 2026 Henk Jan Baard) are the reference for the call order and arguments. The code is written fresh, so no copied code needs a notice; `design.md` credits it.
