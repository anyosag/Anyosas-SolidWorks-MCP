## ADDED Requirements

### Requirement: A face can be rounded off completely

The adapter SHALL expose `add_full_round(top, side_faces)`, and the server SHALL register an `add_full_round` tool taking:
- `top` (the face to round off, by `face_point` or `side`, exactly one);
- `side_faces` (optional, two faces in the same form).

The tool SHALL make one full-round fillet between the two side faces, tangent to both and to `top`. The radius is half the distance between the side faces. On success the reply's `data` SHALL hold:
- `feature` (`name`, `type`);
- `width_mm`: the distance between the side faces;
- `measured_change` (as `tools/measured-feature-change` defines it).

Without `side_faces`, the sides SHALL be the two opposite flat faces along `top`'s edges, standing square to `top`, that lie closest together.

#### Scenario: A rib's top
- **WHEN** a plate carries a rib 4 mm wide, 40 mm long and 15 mm tall, and an agent calls `add_full_round` with `top` at a point on the rib's top face
- **THEN** `width_mm` is 4, and the material removed is 40 × 4² × (1/2 − π/8) = 68.6726 mm³ within 0.01; the bounding box is unchanged, since the crown stays at the original height

#### Scenario: Read back as a full round
- **WHEN** `get_feature_definition` is called on the feature
- **THEN** its `fillet_type` is `full_round`

#### Scenario: Sides given
- **WHEN** a post is 10 × 10 mm and 15 mm tall, and an agent gives `top` and two `side_faces` 10 mm apart
- **THEN** `width_mm` is 10 and the material removed is 10 × 10² × (1/2 − π/8) = 107.3009 mm³ within 0.01

### Requirement: A full round that is unclear or can't be built changes nothing

The tool SHALL fail with `error_type: invalid_input`, naming the cause, when:
- `top` has both or neither of `face_point` and `side`;
- the face isn't flat;
- the sides can't be found: `top` has no two opposite flat faces along it;
- two pairs of sides are equally far apart (within 1e-6 mm), so the direction is unclear; the message says to give `side_faces`;
- `side_faces` doesn't hold two faces that are opposite and flat.

It SHALL fail with `error_type: not_found`, naming the point or side, when a face resolves to nothing. When SolidWorks builds no fillet, it SHALL fail with `error_type: invalid_input` naming `top`. In every such case the part SHALL be as it was and the selection cleared.

#### Scenario: A square post
- **WHEN** an agent calls `add_full_round` with `top` on a 10 × 10 mm post's top, without `side_faces`
- **THEN** the call fails with `error_type: invalid_input` saying two pairs of sides are 10 mm apart and to give `side_faces`, and the part is unchanged

#### Scenario: A face with no opposite sides
- **WHEN** `top` is the top of a triangular prism, whose three side faces have no opposite pair
- **THEN** the call fails with `error_type: invalid_input` saying a full round needs two opposite flat faces along the top

#### Scenario: A point on no face
- **WHEN** `top` is a `face_point` 5 mm outside the part
- **THEN** the call fails with `error_type: not_found` naming the point

### Requirement: The mock adapter mirrors the payloads

The mock adapter SHALL implement `add_full_round` with the same payload keys and the same input refusals. `width_mm` SHALL be null with a note, and `measured_change` SHALL hold null values and a note.

#### Scenario: Mock refusal
- **WHEN** the mock adapter is active and `top` has both `face_point` and `side`
- **THEN** the call fails with `error_type: invalid_input`
