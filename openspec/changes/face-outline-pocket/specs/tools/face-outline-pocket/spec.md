## ADDED Requirements

### Requirement: A pocket leaves a rim that follows the face's outline

The adapter SHALL expose `sketch_face_offset(face_point, side, rim_mm)`, and the server SHALL register a `cut_offset_pocket` tool taking the face (exactly one of `face_point`, `[x, y, z]` mm on a flat face, or `side`, the direction the furthest flat face points), `rim_mm` (greater than 0) and `depth_mm` (greater than 0, or left out to cut through).

The tool SHALL sketch on the face, offset the face's outer loop inward by `rim_mm` as geometry linked to the face's edges, and cut the region that offset encloses. The cut SHALL be made by the `create_cut_extrude` path: one way, into the part. Through SHALL be Through All; a `depth_mm` SHALL be Blind. On success the reply SHALL hold `pocket`, `feature` and `measured_change` (as `tools/measured-feature-change` defines it).

#### Scenario: A through pocket in a plate
- **WHEN** a 60 × 40 mm rectangle is sketched on the Top Plane and extruded 10 mm up, and `cut_offset_pocket` is called with `side: "top"` and `rim_mm: 5`
- **THEN** the volume drops by 50 × 30 × 10 = 15000 mm³ (24000 → 9000, to within 1e-6 relative), a 5 mm rim stands all round, and `measured_change.volume_delta_mm3` is -15000

#### Scenario: A pocket to a depth
- **WHEN** the same plate is pocketed with `rim_mm: 5` and `depth_mm: 4`
- **THEN** the volume drops by 50 × 30 × 4 = 6000 mm³, and the pocket floor lies 4 mm below the top face

#### Scenario: Rounded corners keep their curve
- **WHEN** the plate's outline has R5 corners (area 60 × 40 − (4 − π) × 5² ≈ 2378.54 mm²) and `cut_offset_pocket` is called with `rim_mm: 2` through
- **THEN** the pocket's outline has R3 corners, and the volume drops by (56 × 36 − (4 − π) × 3²) × 10 = 20082.74 mm³ (to within 1e-6 relative)

#### Scenario: The face is picked by a point
- **WHEN** the plate's top face is at y = 10 mm and `cut_offset_pocket` is called with `face_point: [0, 10, 0]` and `rim_mm: 5`
- **THEN** the same pocket is made as with `side: "top"`

### Requirement: A hole in the face keeps a rim around it

Each inner loop of the face SHALL be offset away from its hole by `rim_mm`, so the pocket stops `rim_mm` from the hole's edge. The hole itself SHALL stay open and the material between the pocket and the hole SHALL stand.

#### Scenario: A plate with a centre hole
- **WHEN** the 60 × 40 × 10 mm plate has a Ø10 mm hole through its centre (volume 24000 − 785.40 = 23214.60 mm³) and `cut_offset_pocket` is called with `side: "top"` and `rim_mm: 5` through
- **THEN** the pocket's region is the 50 × 30 mm rectangle less a Ø20 mm circle, and the volume drops by (1500 − π × 10²) × 10 = 11858.41 mm³ (to 11356.19 mm³, to within 1e-6 relative)

### Requirement: Rim and depth come back as dimensions

The reply's `pocket` SHALL hold:
- `sketch_name`: the offset sketch;
- `rim_dimensions`: the names `set_dimension` takes for the rim, the outer loop's first, then each hole's if it has its own;
- `depth_dimension`: the cut's depth dimension, or null for a through cut;
- `loops`: the outer loop and each inner loop, as `sketch_from_face_loop` describes them;
- `fully_defined` (bool, from `check_sketch_fully_defined`).

`feature` SHALL hold `name`, `depth` (mm, or null), `through` (bool) and `direction_flipped` (as `create_cut_extrude` reports it). If the sketch isn't fully defined the reply SHALL carry a `warning` saying so.

#### Scenario: Changing the rim afterwards
- **WHEN** the 5 mm pocket in the plain plate above is made, and `set_dimension` is called with the first name in `rim_dimensions` and `value: 8`
- **THEN** the model rebuilds, and the through pocket is 44 × 24 mm: the volume left is 24000 − 44 × 24 × 10 = 13440 mm³

#### Scenario: Changing the depth afterwards
- **WHEN** the 4 mm deep pocket is made, and `set_dimension` is called with `depth_dimension` and `value: 6`
- **THEN** the volume drops by 50 × 30 × 6 = 9000 mm³ in all

### Requirement: The pocket follows the outline when it changes

The offset SHALL be linked to the face's edges, not copied from their coordinates.

#### Scenario: The plate grows
- **WHEN** the 5 mm through pocket is made in the 60 × 40 × 10 mm plate, and `set_dimension` changes the plate's length to 80 mm
- **THEN** the pocket becomes 70 × 30 mm, and the volume left is 80 × 40 × 10 − 70 × 30 × 10 = 11000 mm³

### Requirement: A refused pocket changes nothing

When the pocket can't be made, the tool SHALL fail and leave no sketch, plane or feature behind: the feature count afterwards SHALL equal the count before. Failures are:
- `error_type: invalid_input` when neither or both of `face_point` and `side` are given, when `rim_mm` or `depth_mm` isn't greater than 0, when the face isn't flat (the message says a sketch needs a flat face), or when SolidWorks can't offset the outline (the message names the rim and says it may be wider than half the face or tighter than a curve);
- `error_type: not_found` when no face lies at `face_point`, or the face has no outer loop;
- the cut path's own error (for example `feature_not_built`) when the cut builds nothing, after the pocket's sketch is removed.

#### Scenario: A rim that is too wide
- **WHEN** `cut_offset_pocket` is called on the 60 × 40 mm top face with `rim_mm: 25`
- **THEN** the call fails with `error_type: invalid_input`, the message names the 25 mm rim, and the feature tree is as it was

#### Scenario: A point off the part
- **WHEN** `cut_offset_pocket` is called with a `face_point` that lies on no face
- **THEN** the call fails with `error_type: not_found` and nothing is added

#### Scenario: Both ways to name the face
- **WHEN** `cut_offset_pocket` is called with `face_point` and `side` together
- **THEN** the call fails with `error_type: invalid_input` before SolidWorks is called

### Requirement: The mock adapter mirrors the payloads

The mock adapter SHALL implement `sketch_face_offset` with the same payload keys, on its canned block.
- A rim of half the block's 10 mm face or more SHALL be refused as too wide, with the real tool's message.
- `measured_change` SHALL hold null values and a note, as for other mock features.

#### Scenario: Mock too-wide rim
- **WHEN** the mock adapter is active and `cut_offset_pocket` is called with `side: "top"` and `rim_mm: 5`
- **THEN** the call fails with `error_type: invalid_input` naming the 5 mm rim
