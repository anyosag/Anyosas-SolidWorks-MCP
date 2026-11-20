## Context

The fork already has the pieces this change builds on:
- `list_model_dimensions` (`adapters/solidworks/documentation.py`) walks every feature's display dimensions and returns `full_name` (`D1@Sketch1`), `value` in document units, `system_value`, `kind` and `units`.
- `set_dimension` (`adapters/solidworks/io.py`) writes `SystemValue`, rebuilds, reads the value back, and fails when SolidWorks kept the old value.
- `rebuild_inspector.py` names each failing feature, with its cause and a hint (`tools/model-rebuild`).
- `measured_change` (`tools/measured-feature-change`) reports volume, box and body count before and after a feature.
- `set_units` sets a document's length unit (`tools/document-units`).

SolidWorks keeps equations in `IEquationMgr`, from `IModelDoc2.GetEquationMgr()`:
- `GetCount()`;
- the indexed properties `Equation(i)` (the text, e.g. `"D1@Sketch1" = "W"`), `Value(i)` and `GlobalVariable(i)`;
- `Add2(index, text, solve)` and `Add3(index, text, solve, config_option, config_names)`;
- `SetEquation(index, text)`, `Delete(index)`, `AutomaticSolveOrder` and `Status`.

hjbaard's `solidworks-mcp` (MIT, © 2026 Henk Jan Baard; `session.py` `set_equations`, `list_equations`, `delete_equation`) shows a working order on SolidWorks 2025:
- parse every equation's left side first;
- turn on `AutomaticSolveOrder`;
- `Add2` new ones at the end, and `SetEquation` existing ones;
- keep a journal and undo it on the first refusal;
- `ForceRebuild3(False)` once.

It also notes that a refused `SetEquation` keeps the old text without an error, so success has to be read back. This change is written fresh in the fork's adapter style, with that as the reference.

Every COM call stays on the connect thread through `_handle_com_operation`, which also gives the settle pause. New dispatch objects get their methods flagged before use (CLAUDE.md, method flagging; pitfalls #41, #42).

## Goals / Non-Goals

**Goals:**
- An agent can read a model's equations and global variables, and see which are broken.
- An agent can add, replace and delete equations and global variables, several at once with one rebuild. A refusal leaves the model as it was.
- An agent sees, from `list_model_dimensions`, which dimensions equations drive. `set_dimension` refuses to fight an equation and says which one.

**Non-Goals:**
- Design tables (`manage_design_table` stays `planned`, queue item 10).
- Configuration-specific equations. Every equation applies to all configurations (D5); a `configurations` option can come later.
- Equation functions beyond what SolidWorks parses itself (`iif`, `sin`, …). They pass through untouched; the server doesn't evaluate equations.
- Linked equation files (`LinkToFile`, `FilePath`).

## Decisions

### D1. Three tools; `set_equations` takes a list

- `set_equations` takes `equations: list[str]`, with at least one item. A single equation is a list of one.
- hjbaard's separate `set_equation` would be a second tool doing the same job, which #174 and queue item 11 are removing elsewhere.
- The input model also accepts a bare string and wraps it, through `tools/input_compat.py`. A client that sends `equation: "…"` by habit still works.

### D2. Equation strings are SolidWorks syntax, passed through

- The server doesn't rewrite or evaluate the right-hand side.
- It parses only the left side: the first quoted name, which must be followed by `=`. A missing quoted left side is `invalid_input` before anything changes, and the message gives an example.
- **Units:** SolidWorks reads a bare number in an equation in the document's units. The reply carries `document_units` so the agent can see which ones apply.
  - The tool description tells agents to write the unit when it matters (`"D1@Sketch1" = 25mm`).
  - Probe 1.2 settles whether `Add3` accepts unit suffixes, and what `Value(i)` returns for an inch part.
  - If suffixes are refused, the description says to convert instead. The server never converts silently.

### D3. Replace by the name an equation sets

- An equation whose left-side name matches an existing equation's left-side name (case-insensitive) replaces it in place, keeping its index.
- Global variables (`"W"`) and dimensions (`"D1@Sketch1"`) are matched the same way.
- Two equations in one call that set the same name are `invalid_input` before anything changes.

### D4. All or nothing: a journal and one rebuild

- **Order:** all left sides are checked first (D2, D3). Then `AutomaticSolveOrder` is turned on, and its old value is remembered so it can be reported.
- **Applying:** each equation is applied in order. An add uses `Add3`, or `Add2` if probe 1.1 shows `Add3` is missing, at index `GetCount()` without solving. A replace uses `SetEquation`.
- **Refusal:** an add that returns a negative index is refused. So is a replace whose read-back text differs from the input after whitespace and case are normalised (hjbaard's finding).
- **Undo:** on the first refusal the journal is undone in reverse (`Delete` the added ones, `SetEquation` the old text back). Then one `ForceRebuild3(False)`, and the call fails with `invalid_input`, naming the refused equation and SolidWorks' reason where `Status` gives one.
- **Success:** one `ForceRebuild3(False)`, then `failing_features` from `rebuild_inspector`. A rebuild with failing features is still a success, with a `warning`: the equations are in, and the agent decides what to fix.
- `measured_change` wraps the whole call: geometry before the first change, after the rebuild.

### D5. All configurations

- `Add3` takes `swAllConfiguration` (`config_option = 2` per `swInConfigurationOpts_e` in `data/swconst_enums.json`; probe 1.1 confirms it on SolidWorks).
- `Add2` has no option and applies to all configurations, which is the same result.

### D6. Broken equations

An equation is `broken` when a quoted name on its right side is neither:
- a global variable set by an equation in the list, nor
- a parameter the model resolves (`IModelDoc2.Parameter(name)` not null).

`delete_feature` leaves such equations behind. `list_equations` shows them, and `delete_equation` removes them.

### D7. `driven_by` and the `set_dimension` refusal

- `list_model_dimensions` reads the equation list once and maps each equation's left-side name (case-insensitive, document suffix stripped) to its text. A dimension whose `full_name` is in the map gets `driven_by: "<equation text>"`; others get `driven_by: null`.
- On a model without equations this costs one `GetCount()` call.
- `set_dimension` checks the same map before writing. A driven dimension is refused with `invalid_input`: "D1@Sketch1 is set by the equation `"D1@Sketch1" = "W"`; change it with set_equations". Nothing is written.
- The existing read-back error stays, for the other causes (driven reference dimensions, design tables).

### D8. The adapter interface

New abstract methods on `SolidWorksAdapter` (`adapters/base.py`):

```python
async def list_equations(self) -> AdapterResult[dict[str, Any]]: ...
async def set_equations(self, equations: list[str]) -> AdapterResult[dict[str, Any]]: ...
async def delete_equation(self, equation: str | int) -> AdapterResult[dict[str, Any]]: ...
```

- Pure helpers in `adapters/solidworks/equations.py`, unit-tested without COM: `equation_names(text) -> list[str]`, `left_side(text) -> str`, `same_equation(a, b) -> bool`, `is_broken(names, variables, resolves) -> bool`.
- `SolidWorksEquationsMixin` holds the COM part and is added to `PyWin32Adapter`'s bases.
- `connection_pool.py` and `circuit_breaker.py` forward the three methods.

### D9. The mock

`mock_adapter.py` keeps a list of `(text, name, global)` per document:
- **Adds and replaces** follow D3.
- **Values:** a right-hand side that is a plain number (with an optional `mm`) sets `self._dimensions[name]` when `name` is a dimension. Anything else is stored with `value: None` and no evaluation.
- **Refusals:** the text `"__refuse__"` anywhere is refused, so tests can drive D4's undo path.
- **Broken:** follows D6 against `self._dimensions` and the variables.

This mirrors the payload shapes, not SolidWorks' solver.

### D10. Support levels and hints

- All three tools start `experimental` (`_UNCHECKED`) and move to `stable` when `test_live_sw_equations.py` passes on SolidWorks 2025 and 2026.
- Hints:
  - `list_equations`: `_reads`.
  - `set_equations`: `_sets` (setting the same equations twice changes nothing).
  - `delete_equation`: destructive. Its description says `undo` can restore it.
- Family: `engineering_data`.

## Risks / Trade-offs

- **Parameterised properties through late binding.** `Equation(i)` may resolve as a property that takes no index, and then raise. Mitigation: probe 1.1 tries a plain call, `flagged(eqmgr, "IEquationMgr")`, and `_oleobj_.InvokeTypes` with the DISPIDs from gen_py. The adapter uses whichever works, and the pitfall catalogue records it.
- **Units in equations.** If `Add3` refuses unit suffixes, a bare number means document units. An agent working in mm on an inch part would get 25.4× sizes. Mitigation: `document_units` in every reply and a description line. The live test includes an inch part.
- **Silent refusals.** `SetEquation` keeps the old text on a bad expression (hjbaard). The read-back check (D4) catches that. `Add3` may add an equation that `Status` reports as an error without returning −1; probe 1.3 checks for that.
- **Rebuild cost.** One rebuild per call, not per equation. A model whose rebuild takes longer than `SOLIDWORKS_MCP_OPERATION_TIMEOUT_SECONDS` stops at the rebuild like any other tool.
- **Tool count.** Three more tools while queue items 10–12 work to shrink the list. They belong to `engineering_data`, which #176 would keep as a core family.

## Probe results

To fill in after task group 1.

## Live results

To fill in after task group 7.

## Open Questions

- Should `set_equations` accept `configurations` now (D5), or wait for a request?
- Should `delete_feature` warn when it leaves broken equations behind? It would need one `list_equations` read after each delete. It's cheap, but it belongs in `delete_feature`'s spec, not this one.
