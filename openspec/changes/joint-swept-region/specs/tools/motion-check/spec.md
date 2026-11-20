## ADDED Requirements

### Requirement: A joint can be checked through a range of values

The adapter SHALL expose `check_motion(dimension, values, distances)`, and the server SHALL register a `check_motion` tool taking:
- `dimension` and `values`, stepped as `tools/joint-stepping` says;
- `distances`: optional pairs of components (`[["Bar-1", "Wall-1"]]`, instance names as `list_components` reports them) whose clearance is measured at each step.

At each step, after the rebuild, the adapter SHALL run the interference check (touching faces don't count) and, for each pair, the minimum-distance measurement of `measure_clearance`. On success the reply's `data` SHALL hold:
- `steps`: per value, in order, `value`, `rebuild_ok`, `interferences` (each with its two components and `volume_mm3`) and `distances` (per pair: `between`, `clearance_mm`, `touching`, `point_a_mm`, `point_b_mm`);
- `dimension`, `unit`, `values`;
- `clash_free` (bool): no step has an interference;
- `smallest_distances`: per pair, `between`, `clearance_mm` (the smallest over the steps) and `at` (the value where it occurs, in the dimension's unit);
- `moving`: the components that moved, a part of a sub-assembly that moved with it counted as the sub-assembly;
- `restored`.

A component in `distances` that isn't in the assembly SHALL fail with `error_type: not_found` before anything moves. The tool SHALL change nothing that lasts.

The tool SHALL differ from `rotation_clearance` in what it moves: `rotation_clearance` writes a component's placement and doesn't solve mates; `check_motion` writes a mate dimension and rebuilds, so the mates solve.

#### Scenario: A bar that clears a wall
- **WHEN** `Bar-1` is the 10 × 10 × 100 mm bar of `tools/swept-region` (hinged at the origin about Z, angle mate `D1@Angle1`, along +X at 0°), `Wall-1` is a block from (15, 40, 0) to (25, 60, 10), and an agent calls `check_motion` with `values: [0, 30, 90]` and `distances: [["Bar-1", "Wall-1"]]`
- **THEN** `clash_free` is true; the steps' `clearance_mm` are 35, 17.14 and 10 (each within 0.01); and `smallest_distances` has `clearance_mm` 10 `at` 90

  Hand calculation, the bar being a strip of half-width 5 along its direction d = (cos θ, sin θ) from the origin to length 100, and the wall's nearest corner being the one with least offset along n = (-sin θ, cos θ):
  - θ = 0: the strip is y from -5 to 5, so the gap to the wall's face y = 40 is 40 − 5 = 35.
  - θ = 90: the strip is x from -5 to 5, so the gap to the face x = 15 is 15 − 5 = 10.
  - θ = 30: the corner (25, 40) has offset −25·sin 30° + 40·cos 30° = 22.141, so the gap is 22.141 − 5 = 17.141 (that corner's distance along d, 41.65, lies within the bar's length).

#### Scenario: A bar that hits the wall
- **WHEN** the same call is made with `values: [0, 30, 60, 90]`
- **THEN** `clash_free` is false, the step at 60 lists an interference between `Bar-1` and `Wall-1` with a positive `volume_mm3`, the steps at 0, 30 and 90 list none, and the clearance at 60 is 0 with `touching: true`

  At 60° the wall's corner (25, 40) has offset −1.65 and its corner (15, 60) offset 17.0 across the bar's centreline, and its corners lie between 42.1 and 64.5 mm along the bar, inside its length of 100, so the wall crosses the strip.

#### Scenario: No pairs asked
- **WHEN** `distances` is left out
- **THEN** each step has `distances: []`, `smallest_distances` is `[]`, and `clash_free` is still reported

#### Scenario: A component that isn't there
- **WHEN** `distances` names `Wall-9` and the assembly has no such component
- **THEN** the call fails with `error_type: not_found` listing the components, and the dimension was not written

### Requirement: The summary doesn't hide a step that couldn't be checked

If the interference check or a distance measurement fails at a step, the run SHALL stop and restore, and the call SHALL fail naming the step's value and the cause. A step SHALL NOT be reported clash-free when its check didn't run.

#### Scenario: A pair with no solid body
- **WHEN** a pair's component holds no solid body
- **THEN** the call fails (`error_type: invalid_input`, from `measure_clearance`'s refusal) before the dimension is written, naming the component

### Requirement: The mock adapter mirrors the payload

The mock adapter SHALL implement `check_motion` with the same payload keys. Its distances SHALL come from the components' boxes placed by the registered joint (so they are lower bounds for a turned box). Its `interferences` SHALL be `[]` and `clash_free` SHALL be null with a note, as the mock's `check_interference` has no geometry to answer from.

#### Scenario: Mock distances
- **WHEN** the mock has the bar and wall above with their boxes and the joint, and `check_motion` is called with `values: [0, 90]` and the pair
- **THEN** each step has a `clearance_mm`, `clash_free` is null with a note, and the dimension reads its original value afterwards
