## 1. Probes (SolidWorks, scratch documents, each with the user's OK)

- [ ] 1.1 `tests/scripts/probe_equations.py`: the manager through late binding.
  - On a new part, get `GetEquationMgr()` and read `GetCount()`.
  - Call `Equation(0)`, `Value(0)` and `GlobalVariable(0)` three ways: plain, after `flagged(eqmgr, "IEquationMgr")`, and through `_oleobj_.InvokeTypes` with gen_py's DISPIDs. Record which works.
  - Check `Add3` exists and that `config_option = 2` (`swAllConfiguration`) applies the equation to two configurations. Otherwise fall back to `Add2`.
  - Record the results in design.md under "Probe results" and settle D4's add call and D5.
- [ ] 1.2 Units.
  - On an mm part and an inch part (`set_units`), add `"D1@Sketch1" = 25`, `= 25mm` and `= 1in`.
  - Read each one's `Value(i)` and the dimension's `SystemValue`.
  - Settle D2: are unit suffixes accepted, and what does a bare number mean?
- [ ] 1.3 Refusals.
  - `Add3` with `"D1@NoSuchSketch" = 5`: record the index returned and `Status`.
  - `SetEquation` with a bad expression: confirm the old text is kept.
  - `Delete` on an out-of-range index.
  - Settle the refusal rules in D4.
- [ ] 1.4 Broken equations.
  - Make `"D1@Boss" = 2 * "D1@Sketch2"`, then delete `Sketch2` with the fork's `delete_feature`.
  - Confirm `IModelDoc2.Parameter("D1@Sketch2")` is null and the equation survives.
  - Settle D6.

## 2. Adapter: equations

- [ ] 2.1 `adapters/solidworks/equations.py`, pure helpers: `equation_names`, `left_side`, `same_equation`, `is_broken`.
  - Tests in `tests/solidworks_mcp/adapters/solidworks/test_equations.py`: quoted names with `@` and spaces, a missing left side, unit suffixes, `iif(...)` on the right.
- [ ] 2.2 `SolidWorksEquationsMixin` in the same module: `list_equations`, `set_equations` (journal, restore, one rebuild, `failing_features` from `rebuild_inspector`, `measured_change`), `delete_equation`.
  - Every COM call goes through `_handle_com_operation`; the indexed reads use the call settled in 1.1.
  - Tests with a fake equation manager (beside `fake_documents.py`) covering add, replace, restore after a refusal at each position, and a read-back mismatch.
- [ ] 2.3 `adapters/base.py`: the three abstract methods (D8). Forward them in `connection_pool.py` and `circuit_breaker.py`, with tests beside each. Add the mixin to `PyWin32Adapter`'s bases in `pywin32_adapter.py`.

## 3. Mock adapter

- [ ] 3.1 `mock_adapter.py`: the per-document equation list (D9), the three methods, numeric right-hand sides updating `_dimensions`, `__refuse__`, and broken marking.
  - Tests in `tests/solidworks_mcp/adapters/test_mock_adapter*.py`, including the spec's "Mock restore path" scenario.

## 4. Dimension tools learn about equations

- [ ] 4.1 `adapters/solidworks/documentation.py` `list_model_dimensions`: read the equation map once, and add `driven_by` to each dimension.
  - Tests: a model without equations makes one `GetCount()` call and no other equation reads.
- [ ] 4.2 `adapters/solidworks/io.py` `set_dimension`: refuse a driven dimension before writing (D7), with tests.
- [ ] 4.3 The mock's `list_model_dimensions` and `set_dimension` mirror 4.1 and 4.2, with tests.

## 5. Tools

- [ ] 5.1 `tools/engineering_data.py`: `list_equations`, `set_equations` (`SetEquationsInput`: `equations: list[str]` with `min_length=1`, a bare string wrapped through `input_compat`) and `delete_equation` (`DeleteEquationInput`: `equation: str | int`).
  - Descriptions per `docs/agents/tool-description-standard.md`. `set_equations` says to write units in equations (or convert, per 1.2) and that a refusal changes nothing. `delete_equation` says `undo` restores it.
  - Tests in `tests/solidworks_mcp/tools/test_engineering_data*.py`.
- [ ] 5.2 `support_levels.py` (all three `_UNCHECKED`) and `tool_hints.py` (D10).
  - An `error_classifier.py` rule only if the refusal messages don't already classify as `invalid_input` / `not_found`.
  - Tests beside each.
- [ ] 5.3 `list_model_dimensions` and `set_dimension` descriptions mention `driven_by` and the refusal.

## 6. Docs

- [ ] 6.1 Regenerate the tool catalog (`src/utils/generate_tool_catalog.py`) and the progress files (`docs/assets/progress/`). Check `src/utils/verify_tool_count.py`.
- [ ] 6.2 Agent skill:
  - `references/workflows.md` gains "Parametric parts with equations": variables first, link dimensions, change a variable, check `measured_change`.
  - `references/errors-and-limits.md` gains the `set_dimension` refusal.
- [ ] 6.3 `docs/agents/com-api-pitfalls.md`: entries for what probes 1.1–1.3 found (indexed properties, units, silent refusals).
- [ ] 6.4 `.\dev-commands.ps1 dev-docs-strict`.

## 7. Live

- [ ] 7.1 `tests/live/test_live_sw_equations.py`, one test per spec scenario that touches SolidWorks:
  - 40 × 20 × 10 block: `"W" = 45` linked to the width gives 9000 mm³; replacing it with 50 gives 10000 mm³.
  - The refused middle equation restores the list.
  - A broken equation is found after `delete_feature`, then deleted by index.
  - `driven_by`, and the `set_dimension` refusal.
  - A rebuild that fails a cut is a success with a warning.
  - The same unit scenarios on an inch part.
- [ ] 7.2 Run `tests/live/test_live_sw_regression.py`: the new mixin touches `pywin32_adapter.py`.
- [ ] 7.3 Record the results in design.md under "Live results", with the SolidWorks versions signed off.

## 8. Gate

- [ ] 8.1 `.\dev-commands.ps1 dev-lint` and `.\dev-commands.ps1 dev-test` (report counts only).
- [ ] 8.2 Live sign-off by the user. Then raise the support levels to `stable` for the tools the live test proved.
- [ ] 8.3 Update `openspec/queue.md` (item 18). Archive the change after its PR merges.
