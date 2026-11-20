## ADDED Requirements

### Requirement: An ISO hole can be made from a face point

The adapter SHALL expose `create_hole_wizard_hole(kind, size, face_point, side, depth_mm, fit, thread)`, and the server SHALL register a `create_hole_wizard_hole` tool taking:
- `kind`: `clearance`, `counterbore`, `countersink` or `tapped`;
- `size`: an ISO metric size such as `M3` (a bare `M<d>`, not a pitch);
- `face_point`: `[x, y, z]` mm, the hole's centre on a flat face;
- `side` (optional): the direction the face points, as `select_face_edges` takes it;
- `depth_mm` (optional): the hole's depth from the face; left out, the hole runs through all;
- `fit` (optional): `close`, `normal` or `loose`; `normal` when left out, and not allowed for `tapped`;
- `thread` (optional, `tapped` only): `cosmetic` (the default) or `modeled`.

The tool SHALL take the optional `document` argument that other modeling tools take. The sizes SHALL come from SolidWorks' own ISO tables, and no size table SHALL be held in the server. On success the reply's `data` SHALL hold:
- `feature` (the Hole Wizard feature's name);
- `hole`: `standard` (`"ISO"`), `kind`, `size`, and the sizes SolidWorks used, in mm and degrees:
  - clearance: `fit`, `diameter_mm`;
  - counterbore: `fit`, `diameter_mm`, `cbore_diameter_mm`, `cbore_depth_mm`;
  - countersink: `fit`, `diameter_mm`, `csink_diameter_mm`, `csink_angle_deg`;
  - tapped: `tap_drill_diameter_mm`, `thread_diameter_mm`, `thread_depth_mm`, `cosmetic_thread` (bool);
- `position` (see the next requirement);
- `face`: `center_mm` and `normal` of the face the hole was made in;
- `thread` for a modeled thread (as `tools/threads` defines it);
- `measured_change` (as `tools/measured-feature-change` defines it, measured before the first change and after the last).

#### Scenario: A normal M3 clearance hole, through
- **WHEN** a part is a 40 × 20 × 10 mm block (8000 mm³) and an agent calls `create_hole_wizard_hole` with `kind: "clearance"`, `size: "M3"`, `face_point: [20, 10, 10]`
- **THEN** `hole.diameter_mm` is 3.4 (to confirm by probe 1.4) and `measured_change.volume_delta_mm3` is −90.792 within 0.01, which is −π · 1.7² · 10

#### Scenario: Close and loose fits
- **WHEN** the same call is made with `fit: "close"` and, on a fresh block, with `fit: "loose"`
- **THEN** `hole.diameter_mm` is 3.2 and 3.6 (to confirm by probe 1.4), and the volumes removed are π · 1.6² · 10 = 80.425 and π · 1.8² · 10 = 101.788 mm³, each within 0.01

#### Scenario: A blind hole ends in a drill point
- **WHEN** `depth_mm: 6` is given for the M3 normal clearance hole
- **THEN** the volume removed is π · 1.7² · 6 plus a 118° drill point of Ø3.4, which is 54.476 + 3.091 = 57.567 mm³ within 0.01

#### Scenario: A counterbore for a socket head screw
- **WHEN** `kind: "counterbore"`, `size: "M3"` through the same block
- **THEN** `hole` reads `diameter_mm` 3.4, `cbore_diameter_mm` 6.5 and `cbore_depth_mm` 3.4 (all to confirm by probe 1.4), and the volume removed is π · 1.7² · 10 + π · (3.25² − 1.7²) · 3.4 = 172.745 mm³ within 0.01. The live test computes this from the sizes the reply returns, so it holds for whatever sizes SolidWorks reports.

#### Scenario: A countersink for a countersunk screw
- **WHEN** `kind: "countersink"`, `size: "M3"` through the same block
- **THEN** `hole` reads `csink_diameter_mm` 6.72 and `csink_angle_deg` 90 (to confirm by probe 1.4), and the volume removed is the bore plus a cone frustum: with a depth of (6.72 − 3.4) / 2 = 1.66 mm, 90.792 + 19.507 = 110.299 mm³ within 0.01

#### Scenario: A tapped hole with a cosmetic thread
- **WHEN** `kind: "tapped"`, `size: "M3"`, `depth_mm: 8`
- **THEN** `hole` reads `tap_drill_diameter_mm` 2.5 and `thread_depth_mm` 6.0 (to confirm by probe 1.4), `cosmetic_thread` is true, and the volume removed is π · 1.25² · 8 plus a 118° drill point of Ø2.5, which is 39.270 + 1.229 = 40.499 mm³ within 0.01

### Requirement: The hole lands on the point and its position is a dimension

The hole's centre SHALL lie within 0.01 mm of `face_point`. The tool SHALL dimension the hole's position from the origin along the face sketch's own horizontal and vertical directions. The reply's `position` SHALL hold `dimensions` (`x` and `y`: the full dimension names, for `set_dimension`) and `fully_defined` (bool).

If the position can't be fixed, the hole SHALL be kept, `fully_defined` SHALL be false and the reply SHALL carry a `warning`.

#### Scenario: The hole is where it was asked
- **WHEN** `face_point: [20, 14, 10]` on the 40 × 20 × 10 block
- **THEN** a circular edge is centred on (20, 14, 10) within 0.01 mm, and `position.fully_defined` is true

#### Scenario: Moving the hole through its dimension
- **WHEN** `set_dimension` sets `position.dimensions.x` to 30 on that hole
- **THEN** the part's centre of mass moves toward −X, and the hole's edge is centred on x = 30

#### Scenario: Either of two top faces at one height
- **WHEN** a 4 mm wide slot, 5 mm deep, cut right across the block's width splits its top into two faces at z = 10, and holes are made at `[8, 10, 10]` and `[32, 10, 10]`
- **THEN** both succeed with `position.fully_defined` true

#### Scenario: The same hole in a side view
- **WHEN** the active view is a side view and a hole is made on a face that another face hides in that view
- **THEN** the hole is in the face that holds `face_point`, not in the face the view shows first

### Requirement: A tapped hole can carry a modeled thread

With `kind: "tapped"` and `thread: "modeled"` the tool SHALL drill the hole at the ISO basic minor diameter of the size's coarse pitch and make a modeled internal thread from the hole's mouth. The coarse pitch SHALL be the largest pitch SolidWorks' thread library holds for that diameter. The thread SHALL run the hole's thread depth for a blind hole and the full length of the hole for a through hole. The reply's `thread` SHALL hold the block that `create_thread` returns.

`thread: "modeled"` with another kind, and `fit` with `tapped`, SHALL fail with `error_type: invalid_input` before SolidWorks is called.

#### Scenario: A blind M3 hole with a modeled thread
- **WHEN** `kind: "tapped"`, `size: "M3"`, `depth_mm: 8`, `thread: "modeled"` on the 40 × 20 × 10 block
- **THEN** `thread.size` is `M3x0.5`, `thread.length_mm` is 6.0 and the volume removed is within 0.2% of 45.10 mm³: the drilled bore of Ø2.4587 (3 − 1.0825318 · 0.5) and its drill point, plus 6 mm of the thread groove

#### Scenario: A through M4 hole
- **WHEN** `kind: "tapped"`, `size: "M4"`, `thread: "modeled"`, through all, on the same block
- **THEN** `thread.length_mm` is 10.0 and the volume removed is 100.95 mm³ within 0.6: the bore of Ø3.2422 (4 − 1.0825318 · 0.7) over 10 mm plus 10 mm of groove. The 0.6 allows for how the thread ends run out of the plate (design probe 1.9).

#### Scenario: A modeled thread on a clearance hole
- **WHEN** `kind: "clearance"` and `thread: "modeled"`
- **THEN** the call fails with `error_type: invalid_input` and nothing changes

### Requirement: Bad input is refused before anything changes

The tool SHALL fail, without changing the model, when:
- `size` is not an ISO metric `M<d>`: `error_type: invalid_input`, with an example;
- `size` is not in SolidWorks' table, which the Hole Wizard shows by making no hole: `invalid_input`, naming the size;
- `depth_mm` is not above 0: `invalid_input`;
- `face_point` is on no face: `not_found`;
- `face_point` is on a face that isn't flat: `invalid_input`, saying what the face is;
- `side` is given and the face at `face_point` points another way: `invalid_input`, naming the direction it points.

#### Scenario: A size SolidWorks doesn't have
- **WHEN** `size: "M3.3"` is given for a clearance hole
- **THEN** the call fails naming `M3.3`, and the feature count is as before

#### Scenario: A point inside an earlier countersink
- **WHEN** an M3 countersink exists at (20, 10, 10) and a second hole is asked for at (22.5, 10, 10), which lies in the cone (past the 1.7 bore, within the 3.36 rim)
- **THEN** the call fails with `error_type: invalid_input` saying the face there is not flat, and the feature count is as before

#### Scenario: A side that doesn't match
- **WHEN** `face_point: [20, 10, 10]` lies on the top face (+Z) and `side: "-Z"`
- **THEN** the call fails with `error_type: invalid_input` naming the direction the face points

### Requirement: A failed hole leaves the model as it was

If a step after the first feature fails (the thread of a modeled hole is refused, or the sizes can't be read back), the tool SHALL delete what it made, rebuild, and fail with the reason. The feature count and the volume SHALL equal their values before the call.

#### Scenario: The thread is refused after the hole was made
- **WHEN** the Hole Wizard makes the hole and the thread's `CreateFeature` returns nothing
- **THEN** the call fails, the hole's feature and position sketch are gone, and the feature count and volume equal their values before the call

### Requirement: A SolidWorks without the feature says so

When SolidWorks has no `HoleWizard5` on `IFeatureManager`, or (for a modeled thread) no Thread feature or thread-profile library, the call SHALL fail with `error_type: not_supported`. The message SHALL name what was missing and the SolidWorks revision (its major number and year), and SHALL contain "does not support".

#### Scenario: No thread-profile library
- **WHEN** a modeled tapped hole is asked for on a SolidWorks whose `Metric Tap` library does not resolve
- **THEN** the call fails with `error_type: not_supported`, naming the library and the revision, and no hole is left behind

### Requirement: The mock adapter mirrors the payloads

The mock adapter SHALL implement `create_hole_wizard_hole` with the same payload keys.
- A size outside its small table SHALL be refused as SolidWorks would.
- `size` text containing `__refuse_thread__` SHALL be refused after the hole is made, so tests can exercise the cleanup path.
- `measured_change` SHALL hold null values and a note, as for other mock features.

#### Scenario: Mock cleanup path
- **WHEN** the mock adapter is active and a tapped hole is asked for with `size: "M3__refuse_thread__"` and `thread: "modeled"`
- **THEN** the call fails and the mock's feature list is as before
