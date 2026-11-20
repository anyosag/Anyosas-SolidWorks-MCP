## ADDED Requirements

### Requirement: A rib can be made from a line on a plane

The adapter SHALL expose `create_rib(sketch_name, thickness_mm, reverse)`, and the server SHALL register a `create_rib` tool taking:
- `plane` (a reference plane name, e.g. `Front Plane`, or one made earlier);
- `start_mm` and `end_mm` (`[x, y, z]` mm, model points on that plane);
- `thickness_mm` (float, above 0);
- `toward_mm` (optional, `[x, y, z]` mm: a point on the side to fill).

The tool SHALL sketch the line on the plane, close the sketch, and make a rib from it. The rib SHALL be centred on the plane, `thickness_mm` thick, and grow from the line until it meets the part. On success the reply's `data` SHALL hold:
- `feature` (`name`, `type`);
- `sketch` (`name`, `plane`);
- `thickness_mm`;
- `direction_flipped` (bool) and `direction_choice`;
- `measured_change` (as `tools/measured-feature-change` defines it).

#### Scenario: A gusset in an L-bracket
- **WHEN** an L-bracket is a profile of two legs extruded 20 mm, 22000 mm³ (a 60 × 10 mm leg and a 10 mm × 50 mm leg: (600 + 500) × 20), with its inner corner at (10, 10) in the profile, and an agent calls `create_rib` with a line from (10, 40) to (40, 10) on a plane through the middle of the extrusion, `thickness_mm: 4` and `toward_mm` at the inner corner
- **THEN** the rib is the triangle with 30 mm legs, 30 × 30 / 2 × 4 = 1800 mm³ thick, so `measured_change.volume_delta_mm3` is 1800 within 0.01 and the volume is 23800 mm³; the bounding box is unchanged

#### Scenario: The side is chosen when not given
- **WHEN** the same rib is made twice on copies of the bracket, once with the line from (10, 40) to (40, 10) and once from (40, 10) to (10, 40), both without `toward_mm`
- **THEN** both add 1800 mm³, and exactly one of the two replies has `direction_flipped: true`

#### Scenario: Both sides empty
- **WHEN** a line lies in free space, meeting no material on either side
- **THEN** the call fails with `error_type: invalid_input` saying the line meets no part, the sketch it made is deleted, and the feature list is as before

#### Scenario: Toward a point on the line
- **WHEN** `toward_mm` lies on the line
- **THEN** the call fails with `error_type: invalid_input` before the rib is tried, and the part is unchanged

### Requirement: A rib's input is checked before anything is built

The tool SHALL fail with `error_type: invalid_input`, naming the cause, when:
- `thickness_mm` is 0 or less;
- `start_mm` and `end_mm` are the same point;
- either point is more than 0.01 mm off the plane;
- `toward_mm` is on the line.

It SHALL fail with `error_type: not_found` when the plane doesn't exist. In every such case the part SHALL be as it was.

#### Scenario: A point off the plane
- **WHEN** `end_mm` is 3 mm off the plane
- **THEN** the call fails with `error_type: invalid_input` naming the distance, and no sketch is left

#### Scenario: A missing plane
- **WHEN** `plane` is `"Plane99"` and no such plane exists
- **THEN** the call fails with `error_type: not_found`

### Requirement: The mock adapter mirrors the payloads

The mock adapter SHALL implement `create_rib` with the same payload keys, refuse the same inputs, and report `direction_flipped: false`. `measured_change` SHALL hold null values and a note.

#### Scenario: Mock rib
- **WHEN** the mock adapter is active and an agent calls `create_rib` with valid input
- **THEN** the reply has `feature.type: "Rib"` and the rib is in `list_features`
