## ADDED Requirements

### Requirement: A joint is stepped through its mate dimension

`swept_region` and `check_motion` SHALL move a joint only by writing the value of a named dimension of the active assembly (an angle or distance mate's dimension, as `list_model_dimensions` names it), rebuilding, and reading the value back. They SHALL NOT write any component's transform.

- `values` SHALL hold at least one number, in the dimension's unit: degrees for an angle, mm for a length. The reply's `unit` SHALL say which.
- A dimension that doesn't exist SHALL fail with `error_type: not_found`. A count dimension (such as a pattern's instances) SHALL fail with `error_type: invalid_input`.
- When the read-back differs from the value written, the adapter SHALL write once more. If it still differs, the call SHALL fail with `error_type: invalid_input`, naming the value, what the dimension reads, and the likely causes (a reference dimension, an equation, a mate that won't move that far).
- The reply SHALL echo `values` as asked, and `written` as sent to SolidWorks when they differ (an angle mate's value wrapped into 0 to 360, if probe 1.1 shows that is needed).

#### Scenario: An angle mate stepped
- **WHEN** an assembly's angle mate has the dimension `D1@Angle1` at 0° and an agent calls `check_motion` with `dimension: "D1@Angle1"` and `values: [0, 45, 90]`
- **THEN** the dimension is written 0, 45 and 90 in turn, each read back equal, and the reply's `unit` is `deg`

#### Scenario: A dimension that isn't there
- **WHEN** `dimension` is `"D9@NoMate"`
- **THEN** the call fails with `error_type: not_found` and nothing is changed

#### Scenario: A value the dimension keeps refusing
- **WHEN** the second of three values is one the dimension won't take, even after the second write
- **THEN** the call fails with `error_type: invalid_input` naming that value and what the dimension reads, and the dimension reads its original value afterwards

### Requirement: The dimension is always put back

Whatever ends the run (all steps done, a refused value, a stop at the operation time limit, the client going away), the adapter SHALL write the dimension's original value, rebuild, and read it back. The reply's `data` SHALL carry `restored` (bool). A failure that could not restore SHALL say so in its message, with the original value and the `set_dimension` call that puts it back.

#### Scenario: A run that completes
- **WHEN** a run steps `D1@Angle1` from 30° through 60° and 90°
- **THEN** the dimension reads 30° afterwards and `restored` is true

#### Scenario: A run stopped by the time limit
- **WHEN** `limits.check` raises between steps
- **THEN** the call fails with `error_type: operation_stopped`, and the dimension reads its original value

#### Scenario: A restore that fails
- **WHEN** the write that puts the original value back doesn't read back
- **THEN** `restored` is false, and the message gives the original value and the `set_dimension` call to run

### Requirement: Broken mates and joints that don't move are refused

Before any step, the adapter SHALL rebuild and refuse the run when a mate has a rebuild error (a warning doesn't count), with `error_type: invalid_input` naming each broken mate and its cause.

A run SHALL fail with `error_type: invalid_input` when no component's placement differed from its start at any step although the values differ from the original: the dimension's mate holds nothing (suppressed, or a face it used is gone). A placement differs by more than 1e-9 in a rotation entry, or 1e-6 mm in its origin.

#### Scenario: A mate whose face is gone
- **WHEN** a concentric mate's hole was deleted and an agent steps the joint's angle
- **THEN** the call fails with `error_type: invalid_input` naming the mate, and the dimension is unchanged

#### Scenario: A suppressed mate
- **WHEN** the angle mate is suppressed and an agent calls `check_motion` with `values: [0, 90]`
- **THEN** the call fails with `error_type: invalid_input` saying stepping moved no component, and the dimension is unchanged

#### Scenario: One value equal to the original
- **WHEN** `values` is `[30]` and the dimension is already 30°
- **THEN** the run succeeds with `moving: []`, since no value differs from the original

### Requirement: The mock adapter steps a joint table

The mock adapter SHALL step the joints a test registers (a dimension, the component it turns, an axis and a pivot) and apply the same refusals: not an assembly, an unknown dimension or component, a count dimension, nothing moved.

#### Scenario: A mock joint
- **WHEN** a test registers `D1@Angle1` to turn `Bar-1` about the Z axis through the origin and calls `check_motion` with `values: [0, 90]`
- **THEN** `moving` is `["Bar-1"]` and the dimension reads its original value afterwards
