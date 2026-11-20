## Why

An agent can change a dimension (`set_dimension`), but it can't make one dimension follow another. Today design intent like "the boss is always twice the plate's thickness" or "every leg is `L` long" only exists in the agent's head. One later `set_dimension` breaks it without warning. SolidWorks keeps that intent in equations and global variables, and this server has no tool that reads or writes them:

- `list_model_dimensions` lists every dimension but can't say which ones an equation drives.
- `set_dimension` on an equation-driven dimension fails only after the write, with a guess at the cause ("it may be a driven (reference) dimension, or set by an equation or a design table", `adapters/solidworks/io.py`).
- A person's part with equations can't be inspected or edited through the server at all.

The comparison with hjbaard's `solidworks-mcp` (v0.21.0) found four equation tools there and none here. This change brings that capability into the fork, built the fork's way. No GitHub issue: issues are off on the fork `anyosag/SolidworksMCP-python`, and upstream has none for equations.

## What Changes

- **New tool `list_equations`:**
  - For each equation it returns `index`, `equation` (the text SolidWorks holds), `name` (the dimension or global variable it sets), `value`, `global` and `broken`. `broken` marks one that names a dimension or variable that is gone, such as one left behind by `delete_feature`.
  - It also returns `automatic_solve_order` and `document_units`.
  - Read-only.
- **New tool `set_equations`:**
  - Takes a list of one or more SolidWorks equation strings, e.g. `'"W" = 40'`, `'"D1@Sketch1" = "W"'`, `'"D1@Boss" = 2 * "D1@Sketch1"'`.
  - An equation that sets a name already set by another is replaced in place; any other is added.
  - Every equation's left side is checked before anything changes, then there is one rebuild at the end.
  - A refused equation undoes the whole list, so the model is as it was.
  - It turns on automatic solve order, so a variable may be set below the equations that use it.
  - It returns per equation its `index` and whether it `replaced` one, plus `failing_features` and `measured_change`.
- **New tool `delete_equation`:**
  - Takes the name it sets (`"W"`, `"D1@Boss"`) or a `list_equations` index.
  - Rebuilds and returns what was deleted, `failing_features` and `measured_change`.
- **`list_model_dimensions` and `set_dimension` learn about equations:**
  - Each listed dimension gains `driven_by`: the equation text, or null.
  - `set_dimension` on a dimension an equation drives is refused before anything is written. The refusal is `invalid_input`, names the equation, and points to `set_equations`.
- **No `set_equation` (singular).** hjbaard has both. Here `set_equations` with a list of one does the same job, so the tool list doesn't grow a duplicate (#174, queue item 11).

## Capabilities

### New Capabilities
- `tools/equations`: reading, adding, replacing and deleting equations and global variables, and how dimension tools report and respect equations.

### Modified Capabilities
None. The `list_model_dimensions` and `set_dimension` changes are specified in `tools/equations`, since no spec covers those tools yet.

## Impact

- **COM/adapter code (extra risk per CLAUDE.md):**
  - New `adapters/solidworks/equations.py` (`SolidWorksEquationsMixin`) on `IModelDoc2.GetEquationMgr()` → `IEquationMgr` (`GetCount`, `Equation`, `Value`, `GlobalVariable`, `Add3` or `Add2`, `SetEquation`, `Delete`, `AutomaticSolveOrder`, `Status`).
  - `IEquationMgr`'s indexed members (`Equation(i)`, `Value(i)`, `GlobalVariable(i)`) are parameterised properties. Through forced late binding they may need method flagging or `InvokeTypes`; the probes settle which (pitfalls #41, #42).
  - `adapters/solidworks/documentation.py` (`list_model_dimensions`: `driven_by`) and `io.py` (`set_dimension`: the up-front refusal).
  - `adapters/base.py`, `connection_pool.py`, `circuit_breaker.py` forward the three new methods; `pywin32_adapter.py` adds the mixin.
  - `mock_adapter.py` gains an equation table.
- **Tools:**
  - `tools/engineering_data.py` (beside `list_model_dimensions`).
  - `support_levels.py` (experimental until the live test passes), `tool_hints.py`, `error_classifier.py` if a new rule is needed.
  - The tool catalog, the progress files, and the agent skill's `references/workflows.md` (a "parametric part" workflow).
- **Docs:** pitfall entries for what the probes find (units in equation strings, parameterised properties).
- **Tests:**
  - unit tests beside each module;
  - a probe script `tests/scripts/probe_equations.py` (scratch documents);
  - a live test `tests/live/test_live_sw_equations.py`.
- **Credit:** hjbaard's `set_equations` (MIT, © 2026 Henk Jan Baard) is the reference for the call order and the undo journal. The code is written fresh, so no copied code needs a notice; `design.md` credits it.
