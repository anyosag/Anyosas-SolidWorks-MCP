## ADDED Requirements

### Requirement: A part can be shelled

The adapter SHALL expose `create_shell(thickness_mm, open_faces, outward)`, and the server SHALL register a `create_shell` tool taking:
- `thickness_mm` (float, above 0);
- `open_faces` (a list of faces to remove, empty for a closed hollow), each given by `face_point` (`[x, y, z]` mm) or `side`, exactly one;
- `outward` (bool, default false): the wall grows outward from the original faces instead of inward.

On success the reply's `data` SHALL hold:
- `feature` (`name`, `type`) of the shell the tree gained;
- `thickness_mm`, `open_faces` (count) and `outward`;
- `measured_change` (as `tools/measured-feature-change` defines it).

#### Scenario: An open box
- **WHEN** a part is a 40 × 40 × 20 mm block (volume 32000 mm³) and an agent calls `create_shell` with `thickness_mm: 2` and one open face, the top
- **THEN** the volume is 8672 mm³ (32000 − 36 × 36 × 18 = 32000 − 23328), `measured_change.volume_delta_mm3` is −23328, and the bounding box is unchanged at 40 × 40 × 20 mm

#### Scenario: A closed hollow
- **WHEN** the same block is shelled with `thickness_mm: 2` and `open_faces: []`
- **THEN** the volume is 11264 mm³ (32000 − 36 × 36 × 16 = 32000 − 20736), the reply says `open_faces: 0`, and the part has one solid body

#### Scenario: Two open faces
- **WHEN** the same block is shelled with `thickness_mm: 2`, opening the top and one 40 × 20 mm side face
- **THEN** the volume is 7376 mm³ (32000 − 38 × 36 × 18 = 32000 − 24624)

#### Scenario: Walls grow outward
- **WHEN** the same block is shelled with `thickness_mm: 2`, the top open and `outward: true`
- **THEN** the original block is the cavity and the bounding box is 44 × 44 × 22 mm, so the volume is 10592 mm³ (44 × 44 × 22 − 32000 = 42592 − 32000)

#### Scenario: Faces by side and by point
- **WHEN** an agent opens the top once with `side: "top"` and once, on another copy of the block, with a `face_point` at the centre of the top face
- **THEN** both give the same volume, 8672 mm³

### Requirement: A shell that can't be built changes nothing

The tool SHALL fail with `error_type: invalid_input`, before SolidWorks is called, for `thickness_mm` of 0 or less, and for a face given with both or neither of `face_point` and `side`. It SHALL fail with `error_type: not_found`, naming the point or side, when a face resolves to nothing.

When SolidWorks builds no shell, the tool SHALL fail with `error_type: invalid_input` naming the thickness, and SHALL leave the part as it was: the same features, volume and bounding box. A shell the tree gained but SolidWorks flagged as failed SHALL be deleted.

#### Scenario: A wall too thick
- **WHEN** an agent calls `create_shell` with `thickness_mm: 25` on the 40 × 40 × 20 mm block, the top open
- **THEN** the call fails naming 25 mm, and the part still has its original feature list and a volume of 32000 mm³

#### Scenario: Zero thickness
- **WHEN** an agent calls `create_shell` with `thickness_mm: 0`
- **THEN** the call fails with `error_type: invalid_input` and nothing is sent to SolidWorks

#### Scenario: A point on no face
- **WHEN** an agent gives a `face_point` 5 mm outside the block
- **THEN** the call fails with `error_type: not_found` naming the point, and the part is unchanged

### Requirement: A shell acts on one body

On a part with more than one solid body, every open face SHALL be on the same body, and that body is shelled. Faces on two bodies, or a closed hollow (`open_faces` empty), SHALL fail with `error_type: invalid_input` naming the solid bodies.

#### Scenario: Faces on two bodies
- **WHEN** a part has two separate 40 × 40 × 20 mm blocks and an agent opens the top of each in one call
- **THEN** the call fails with `error_type: invalid_input` naming both bodies, and neither is shelled

#### Scenario: Closed hollow on a multibody part
- **WHEN** the same part gets `open_faces: []`
- **THEN** the call fails with `error_type: invalid_input` naming the bodies, and the part is unchanged

### Requirement: The mock adapter mirrors the payloads

The mock adapter SHALL implement `create_shell` with the same payload keys. It SHALL refuse a non-positive thickness, a face point on no mock face, and a thickness of half the mock block or more. `measured_change` SHALL hold null values and a note, as for other mock features.

#### Scenario: Mock refusal
- **WHEN** the mock adapter is active and `create_shell` gets a `face_point` on no face
- **THEN** the call fails with `error_type: not_found`
