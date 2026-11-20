## ADDED Requirements

### Requirement: Equations and global variables can be listed

The adapter SHALL expose `list_equations()`, and the server SHALL register a `list_equations` tool with no required input. On an open part or assembly the reply's `data` SHALL hold:
- `equations`: one entry per equation in SolidWorks' order, each with:
  - `index` (int, from 0);
  - `equation` (the text SolidWorks holds);
  - `name` (the dimension or global variable it sets, without quotes);
  - `value` (float, or null when unreadable);
  - `global` (bool);
  - `broken` (bool).
- `count` (int).
- `automatic_solve_order` (bool).
- `document_units` (the active document's length unit).

The tool SHALL be read-only.

#### Scenario: A global variable drives a dimension
- **WHEN** a part has the equations `"W" = 40` and `"D1@Sketch1" = "W"` and an agent calls `list_equations`
- **THEN** `count` is 2; the first entry has `name: "W"`, `global: true` and `value` 40; the second has `name: "D1@Sketch1"`, `global: false` and `broken: false`

#### Scenario: A model without equations
- **WHEN** `list_equations` is called on a part with no equations
- **THEN** the call succeeds with `count: 0` and an empty `equations` list

#### Scenario: No part or assembly
- **WHEN** no document is open, or the active one is a drawing
- **THEN** the call fails with `error_type: invalid_input` naming the document types it takes

### Requirement: An equation that names something gone is marked broken

An entry SHALL have `broken: true` when a quoted name on its right side is neither a global variable set in the list nor a parameter the model resolves.

#### Scenario: A deleted feature leaves an equation behind
- **WHEN** `"D1@Boss" = 2 * "D1@Sketch2"` exists and `delete_feature` removes `Sketch2` and its dependents
- **THEN** `list_equations` marks that entry `broken: true`

### Requirement: Equations can be added and replaced in one call

The adapter SHALL expose `set_equations(equations)`, and the server SHALL register a `set_equations` tool taking `equations`, a list of one or more SolidWorks equation strings (a single string SHALL be accepted as a list of one).

- An equation whose left-side name matches an existing equation's (case-insensitive) SHALL replace it in place, keeping its index. Any other SHALL be added at the end.
- The call SHALL turn on automatic solve order.
- The call SHALL rebuild the model once, after all equations are applied.
- On success the reply's `data` SHALL hold:
  - `equations`: per input, in order, its `equation`, `index` and `replaced` (bool);
  - `automatic_solve_order_was` (bool);
  - `failing_features` (as `rebuild_model` reports them);
  - `measured_change` (as `tools/measured-feature-change` defines it, measured before the first change and after the rebuild);
  - `document_units`.

#### Scenario: A variable and the dimension it drives
- **WHEN** a part is a 40 × 20 mm sketch `Sketch1` (width `D1@Sketch1`) extruded 10 mm, and an agent calls `set_equations` with `['"W" = 45', '"D1@Sketch1" = "W"']`
- **THEN** both entries have `replaced: false`, `failing_features` is empty, and `measured_change.volume_delta_mm3` is 1000 within 1e-6 relative (8000 → 9000 mm³)

#### Scenario: Replacing a variable
- **WHEN** `"W" = 45` exists and an agent calls `set_equations` with `['"W" = 50']`
- **THEN** the entry has `replaced: true` and the same `index` as before, `list_equations` shows `"W"` once, and the volume is 10000 mm³

#### Scenario: A rebuild that fails a feature
- **WHEN** an equation makes a cut deeper than its part
- **THEN** the call succeeds, `failing_features` names the cut, and the reply carries a `warning` saying the equations were applied but features fail

### Requirement: A refused equation changes nothing

The left side of every equation SHALL be checked before any change. An equation without a quoted name followed by `=`, or two equations in one call that set the same name, SHALL fail the call with `error_type: invalid_input` before SolidWorks is called.

If SolidWorks refuses an equation, all equations this call added or replaced SHALL be restored to their state before the call, the model SHALL be rebuilt, and the call SHALL fail with `error_type: invalid_input` naming the refused equation. Refusals include:
- an add that returns a negative index;
- a replace whose text reads back different from the input after whitespace and case are normalised.

#### Scenario: The second of three equations names a missing dimension
- **WHEN** an agent calls `set_equations` with `['"W" = 45', '"D1@NoSuchSketch" = "W"', '"H" = 5']` on a part whose equations were `["W" = 40]`
- **THEN** the call fails naming `"D1@NoSuchSketch" = "W"`, and `list_equations` afterwards shows only `"W" = 40`

#### Scenario: No quoted left side
- **WHEN** an agent calls `set_equations` with `['W = 40']`
- **THEN** the call fails with `error_type: invalid_input`, the message shows the form `"W" = 40`, and nothing is sent to SolidWorks

### Requirement: Equations can be deleted

The adapter SHALL expose `delete_equation(equation)`, and the server SHALL register a `delete_equation` tool taking `equation`: the name it sets (quotes optional, case-insensitive) or an index from `list_equations`. It SHALL delete that one equation, rebuild once, and return `deleted` (the text removed), `count` (left), `failing_features` and `measured_change`.

An unknown name or an index out of range SHALL fail with `error_type: not_found`, listing the equations there are.

#### Scenario: Deleting a broken equation by index
- **WHEN** `list_equations` marks index 2 `broken` and an agent calls `delete_equation` with `2`
- **THEN** that equation is gone, `count` is one less, and `failing_features` is empty

#### Scenario: Unknown name
- **WHEN** an agent calls `delete_equation` with `"Q"` and no equation sets `Q`
- **THEN** the call fails with `error_type: not_found` and the message lists the existing equations

### Requirement: Dimension tools report and respect equations

`list_model_dimensions` SHALL add `driven_by` to every dimension: the text of the equation that sets it, or null.

`set_dimension` on a dimension an equation sets SHALL fail with `error_type: invalid_input` before anything is written. Its message SHALL name the equation and point to `set_equations`.

#### Scenario: A driven dimension
- **WHEN** `"D1@Sketch1" = "W"` exists and an agent calls `list_model_dimensions`
- **THEN** `D1@Sketch1` has `driven_by: "\"D1@Sketch1\" = \"W\""` and every other dimension has `driven_by: null`

#### Scenario: set_dimension on a driven dimension
- **WHEN** `"W" = 45` and `"D1@Sketch1" = "W"` exist and an agent calls `set_dimension` with `name: "D1@Sketch1"` and `value: 30`
- **THEN** the call fails with `error_type: invalid_input`, the message quotes the equation and names `set_equations`, and the dimension still reads 45

### Requirement: The mock adapter mirrors the payloads

The mock adapter SHALL implement `list_equations`, `set_equations` and `delete_equation` with the same payload keys.
- A right-hand side that is a plain number SHALL set the mock dimension it names.
- An equation containing `__refuse__` SHALL be refused, so tests can exercise the restore path.
- `measured_change` SHALL hold null values and a note, as for other mock features.

#### Scenario: Mock restore path
- **WHEN** the mock adapter is active and `set_equations` gets `['"A" = 1', '"B" = __refuse__']`
- **THEN** the call fails naming the second equation, and `list_equations` shows no `"A"`
