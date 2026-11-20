## ADDED Requirements

### Requirement: A face's loops can be projected into a sketch as linked references

The adapter SHALL expose `sketch_from_face_loop(face_point, side, loops, as_construction, keep_open)`, and the server SHALL register a `sketch_from_face_loop` tool taking:
- the face: exactly one of `face_point` (`[x, y, z]` mm on a flat face) or `side` (the direction the furthest flat face points);
- `loops`: `all` (default), `outer` or `inner`;
- `as_construction` (default false);
- `keep_open` (default false).

The tool SHALL open a sketch on the face and project the chosen loops into it with each edge linked to the face's edge, not copied. The outer loop is the one `ILoop2.IsOuter` marks; every other loop is inner. On success the reply SHALL hold:
- `sketch`: `name`, `plane`, `axes`, `plane_created` and `open`;
- `face`: `center_mm`, `normal` and `area_mm2`;
- `loops`: one per loop projected, with `index`, `outer`, `edge_count` and `curves` (counts of `line`, `arc`, `circle`, `spline` and `other`);
- `entities_added` (int) and `construction` (bool).

The tool SHALL change no geometry, so the reply carries no `measured_change`. Unless `keep_open` is true, the sketch SHALL be closed, so it can be used by name.

#### Scenario: A face with a hole
- **WHEN** the top face of a 60 × 40 × 10 mm plate with a Ø10 mm hole is given as `side: "top"`, with `loops` left as `all`
- **THEN** `loops` has two entries: the outer with `edge_count: 4` and `curves.line: 4`, and an inner with `outer: false`, `edge_count: 1` and `curves.circle: 1`; and `entities_added` is 5

#### Scenario: Outer only, and inner only
- **WHEN** the same face is projected with `loops: "outer"`, then with `loops: "inner"`
- **THEN** the first sketch holds 4 entities and the second holds 1

#### Scenario: A face with no holes, inner loops asked for
- **WHEN** `loops: "inner"` is given for the top face of a plain plate
- **THEN** the call fails with `error_type: not_found`, and the message says the face has one loop, an outer one

#### Scenario: The sketch follows the face
- **WHEN** a plain 60 × 40 × 10 mm plate's top face is projected, the sketch is made into a thin wall with `create_extrusion` (`thin_feature: true`, `thin_thickness: 2`, `thin_side: "mid_plane"`, `depth: 10`), and then `set_dimension` changes the plate's length to 80 mm
- **THEN** the wall was a 62 × 42 mm ring less a 58 × 38 mm one, 10 mm tall (volume 24000 + 4000 = 28000 mm³), and after the change the sketch is 80 × 40 mm, so the volume is 80 × 40 × 10 + (82 × 42 − 78 × 38) × 10 = 36800 mm³ (to within 1e-6 relative)

### Requirement: Loops can be projected as construction geometry

With `as_construction: true` every entity the tool adds SHALL be construction geometry, and the reply SHALL have `construction: true`. Without it the entities are real geometry and `construction` is false.

#### Scenario: Construction outline
- **WHEN** a plate's top face is projected with `as_construction: true`
- **THEN** every entity added is construction geometry and `entities_added` counts them

### Requirement: A sketch can be kept open

With `keep_open: true` the sketch SHALL stay active and the reply's `sketch.open` SHALL be true, so an agent can add geometry with the sketch tools and close it with `exit_sketch`. By default `sketch.open` is false.

#### Scenario: Add a line to a projected sketch
- **WHEN** a face is projected with `keep_open: true`, and `add_line` is then called
- **THEN** the line lands in that sketch, and `exit_sketch` closes it with the projected loops and the line

### Requirement: Bad faces and bad requests are refused

The tool SHALL fail with:
- `error_type: invalid_input` when neither or both of `face_point` and `side` are given, when `loops` isn't `all`, `outer` or `inner`, or when the face isn't flat (the message says a sketch needs a flat face);
- `error_type: not_found` when no face lies at `face_point`, or when `loops: "inner"` finds no inner loop.

A refused call SHALL leave no sketch or plane behind and SHALL leave no sketch open.

#### Scenario: A curved face
- **WHEN** `face_point` lies on the side of a Ø20 mm cylinder
- **THEN** the call fails with `error_type: invalid_input`, and the feature tree is as it was

#### Scenario: Both ways to name the face
- **WHEN** `face_point` and `side` are given together
- **THEN** the call fails with `error_type: invalid_input` before SolidWorks is called

### Requirement: The mock adapter mirrors the payloads

The mock adapter SHALL implement `sketch_from_face_loop` with the same payload keys, on its canned 10 mm block: an outer loop of 4 lines, plus one circle loop on the ±Z faces once a circle has been sketched.

#### Scenario: Mock block with a hole
- **WHEN** the mock adapter is active, a circle has been sketched, and `sketch_from_face_loop` is called with `side: "+Z"`
- **THEN** `loops` holds an outer loop with 4 lines and an inner loop with 1 circle, and `entities_added` is 5
