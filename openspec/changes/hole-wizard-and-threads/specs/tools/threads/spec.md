## ADDED Requirements

### Requirement: A modeled ISO thread can be made on a circular edge

The adapter SHALL expose `create_thread(size, edge_point, length_mm, internal)`, and the server SHALL register a `create_thread` tool taking:
- `size`: an ISO metric size with its pitch, such as `M10x1.5`;
- `edge_point`: `[x, y, z]` mm, a point on the circular edge where the thread starts (the end edge of a rod, or the mouth of a hole);
- `length_mm`: how far the thread runs into the material, above 0;
- `internal` (optional, false by default): true for a thread in a hole.

The thread SHALL be right-handed and run `length_mm` from the edge into the material. The tool SHALL take the optional `document` argument that other modeling tools take. On success the reply's `data` SHALL hold:
- `feature` (the thread feature's name);
- `thread`: `size`, `pitch_mm`, `major_diameter_mm`, `minor_diameter_mm` (the ISO basic minor diameter), `length_mm`, `internal`, `right_handed`;
- `edge`: `center_mm` and `diameter_mm` of the edge used;
- `measured_change` (as `tools/measured-feature-change` defines it).

#### Scenario: An external M10x1.5 thread on a rod
- **WHEN** a part is a Ø10 × 40 mm rod standing on the XY plane and an agent calls `create_thread` with `size: "M10x1.5"`, `edge_point: [5, 0, 40]`, `length_mm: 20`
- **THEN** `thread.pitch_mm` is 1.5, `thread.major_diameter_mm` is 10 and `thread.minor_diameter_mm` is 8.3762; the volume removed is about 268 mm³ within 0.5%, which is 20 mm of the groove: a trapezoid P/4 wide at the minor radius and 7P/8 wide at the major radius, with a height of 5√3/16 · P, swept at its centroid radius once per pitch

#### Scenario: An internal M10x1.5 thread in a hole
- **WHEN** a 30 × 30 × 20 block has a Ø8.376202 hole through it at (15, 15) and an agent calls `create_thread` with `size: "M10x1.5"`, `edge_point: [15 + 4.188101, 15, 20]`, `length_mm: 12`, `internal: true`
- **THEN** the volume removed is about 120.4 mm³ within 0.5%: 12 mm of a groove that is 3P/4 wide at the minor radius and P/8 wide at the major radius. SolidWorks' tap profile runs about 0.06% leaner than that basic trapezoid.

### Requirement: The size is checked against SolidWorks' thread library

The tool SHALL check `size` against the configurations of the thread-profile library SolidWorks holds for the thread's kind (`Metric Tap` for internal, `Metric Die` for external) before any feature is made. A size that is not in the library, or not in the form `M<d>x<p>`, SHALL fail the call with `error_type: invalid_input`. For a size in the right form, the message SHALL list the library's sizes for that diameter.

#### Scenario: A size SolidWorks would cut garbage for
- **WHEN** an agent calls `create_thread` with `size: "M10x1.3"`
- **THEN** the call fails with `error_type: invalid_input`, the message lists the library's M10 sizes (it includes `M10x1.5`), and the feature count is as before

#### Scenario: Not a metric size
- **WHEN** `size` is `1/4-20`
- **THEN** the call fails with `error_type: invalid_input` and the message shows the form `M10x1.5`

### Requirement: The edge must have the diameter the thread needs

An external thread SHALL need an edge of the nominal diameter. An internal one SHALL need an edge of the ISO basic minor diameter, D − 1.0825318 · P, because SolidWorks shifts an internal thread with its hole. An edge that differs by more than 0.01 mm SHALL fail the call with `error_type: invalid_input`. The message SHALL give the diameter needed and the diameter found, and for an internal thread say to drill that hole first. Nothing SHALL change.

#### Scenario: A tap-drill hole that is too big
- **WHEN** a block has a Ø8.5 hole and an agent asks for an internal M10x1.5 thread on its mouth
- **THEN** the call fails with `error_type: invalid_input`; the message names Ø8.376 as needed and Ø8.500 as found

#### Scenario: A rod of the wrong size
- **WHEN** the rod is Ø9.9 and an external M10x1.5 thread is asked for
- **THEN** the call fails with `error_type: invalid_input`; the message names Ø10 as needed and Ø9.900 as found

### Requirement: The edge is found from a point on it

The tool SHALL use the circular edge whose closest point to `edge_point` is within 0.01 mm. No such edge SHALL fail the call with `error_type: not_found`; the message SHALL give the centre and radius of the nearest circular edge. More than one SHALL fail with `error_type: invalid_input`, listing the centres and diameters.

#### Scenario: A point in the air
- **WHEN** `edge_point` is [30, 30, 40] on a Ø10 rod centred on the origin
- **THEN** the call fails with `error_type: not_found` and nothing changes

#### Scenario: Two coaxial edges
- **WHEN** a rod's end carries a chamfer, so the end face has an edge and the chamfer has another with the same axis
- **THEN** a point on the face's edge picks that edge, and a point on the chamfer's edge picks that one

### Requirement: A thread that would rebuild too slowly is refused

The tool SHALL refuse `length_mm` over `pitch` times the turn limit that design D9 sets (`MAX_THREAD_TURNS`), before SolidWorks is called. The refusal SHALL be `error_type: invalid_input` and say to shorten the thread or use a coarser pitch.

#### Scenario: A long fine thread
- **WHEN** `size: "M3x0.5"` and `length_mm` give more turns than `MAX_THREAD_TURNS`
- **THEN** the call fails with `error_type: invalid_input` naming the turn count and the limit, and SolidWorks is not called

### Requirement: A SolidWorks without the Thread feature says so

When SolidWorks gives no thread definition, or the profile library for the thread's kind does not resolve, the call SHALL fail with `error_type: not_supported`. The message SHALL name what was missing and the SolidWorks revision (its major number and year), and SHALL contain "does not support".

#### Scenario: No thread definition
- **WHEN** `CreateDefinition` for the thread feature returns nothing
- **THEN** the call fails with `error_type: not_supported`, the message names the revision, and no feature is added

#### Scenario: No material behind the edge
- **WHEN** `CreateFeature` returns nothing because there is no `length_mm` of material behind the edge
- **THEN** the call fails with `error_type: operation_failed`, the message asks whether there is that much material, and the feature count is as before

### Requirement: The mock adapter mirrors the payloads

The mock adapter SHALL implement `create_thread` with the same payload keys.
- It SHALL run the same size parse and diameter check as the real adapter, on the mock's own edge description.
- `measured_change` SHALL hold null values and a note, as for other mock features.

#### Scenario: Mock diameter check
- **WHEN** the mock adapter is active and `create_thread` is called for `M10x1.5` internal on an edge of Ø8.5
- **THEN** the call fails with `error_type: invalid_input` naming Ø8.376
