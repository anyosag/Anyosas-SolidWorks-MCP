## ADDED Requirements

### Requirement: The active part's sections are compared with a reference mesh

The adapter SHALL expose `compare_with_mesh(path, axis, heights_mm, frame="object", offset_mm=None)`, and the server SHALL register a `compare_with_mesh` tool taking the same inputs. `path`, `axis`, `heights_mm` and `frame` are as for `slice_mesh`. `offset_mm` is `[dx, dy, dz]` in mm, `[0, 0, 0]` when absent; it is added to every reference point to bring the reference into the part's frame.

The call SHALL:
- read the reference mesh first, so a bad file fails before SolidWorks is touched;
- export the active part to a temporary STL at a fine mesh, in mm, in the model's own coordinates, put the user's STL options back, and delete the file;
- slice both meshes at each height on `axis`.

On success the reply's `data` SHALL hold:
- `axis`, `frame`, `offset_mm`, `part_triangles` and `reference_triangles`;
- `sections`: one per height, in the order given, each with `height_mm`, `pairs`, `unmatched_reference` and `unmatched_part`;
- `worst_area_diff_mm2` (the largest absolute `area_diff_mm2`) and `worst_extent_diff_mm` (the largest `max_extent_diff_mm`), over all heights, rounded to 4 decimals;
- `mesh`: the STL settings applied, and whether the user's were restored.

Each pair SHALL have `reference_area_mm2`, `part_area_mm2`, `area_diff_mm2` (part minus reference) and `max_extent_diff_mm` (the largest difference between the two loops' `u_min`, `u_max`, `v_min` and `v_max`), rounded to 4 decimals. The tool SHALL be read-only.

#### Scenario: A part that matches its reference
- **WHEN** the active part is a 20 mm cube with a corner at the origin, the reference is a 20 mm cube STL, and the call has `axis: "z"` and `heights_mm: [10]`
- **THEN** `sections[0].pairs` has one pair with areas 400 and 400, `area_diff_mm2` 0 and `max_extent_diff_mm` 0 within 1e-3, and both `unmatched` counts are 0

#### Scenario: The reference is in another frame
- **WHEN** the part is a 40 × 30 × 5 mm plate spanning x −20 to 20, y 0 to 5, z −15 to 15 (sketched on the Top plane and extruded 5 mm), the reference is the same plate as an STL spanning 0 to 40, 0 to 5, 0 to 30, and the call has `axis: "y"`, `heights_mm: [2.5]` and no `offset_mm`
- **THEN** the pair has `area_diff_mm2` 0 and `max_extent_diff_mm` 20; with `offset_mm: [-20, 0, -15]` both are 0

#### Scenario: A feature the reference lacks
- **WHEN** the part is the plate above with a 20 × 10 mm pocket cut 4 mm deep from its top face (y = 5), the reference is the plate with a closed 12 × 10 × 3 mm void centred in the same place (y 1.5 to 4.5), and the call has `axis: "y"`, `heights_mm: [3]` and the right `offset_mm`
- **THEN** there are two pairs: the outline (1200 and 1200, difference 0) and the hole (reference 120, part 200, `area_diff_mm2` 80, `max_extent_diff_mm` 4); `worst_area_diff_mm2` is 80 and `worst_extent_diff_mm` is 4

#### Scenario: A loop with no partner
- **WHEN** the part section at a height has the plate outline and the 20 × 10 pocket, and the reference's section at that height is the outline alone
- **THEN** `pairs` has one entry and `unmatched_part` is 1

### Requirement: A hole is paired with a hole

Each reference loop, largest first, SHALL be paired with the nearest unpaired part loop, by the centre of their extents. Where several part loops have a centre within 1e-6 mm of the nearest, the one whose area is closest to the reference loop's SHALL be chosen. A loop SHALL pair at most once. Loops left over SHALL be counted in `unmatched_reference` and `unmatched_part`. Areas are those of single loops, not net of the holes inside them.

#### Scenario: An outline and a hole share a centre
- **WHEN** the reference has a 400 mm² outline and a 100 mm² hole, both centred on the same point, and the part has a 400 mm² outline and a 144 mm² hole centred there
- **THEN** the outline pairs with the outline (difference 0) and the hole with the hole (`area_diff_mm2` 44)

### Requirement: The comparison needs a part and a readable reference

No open document, an active assembly or drawing, an empty or over-long `heights_mm`, an unknown `axis` or `frame`, an `offset_mm` that is not three finite numbers, or an unreadable reference SHALL fail with `error_type: invalid_input`; a path that doesn't exist SHALL fail with `error_type: not_found`. None of these SHALL start an export. A failed export SHALL fail the call with the adapter's message, and no temporary file SHALL be left.

#### Scenario: An assembly
- **WHEN** the active document is an assembly
- **THEN** the call fails with `error_type: invalid_input`, saying to compare one part

#### Scenario: A missing reference
- **WHEN** `path` names a file that doesn't exist and a part is open
- **THEN** the call fails with `error_type: not_found`, and the adapter's `export_stl_with_settings` was not called

### Requirement: The mock slices its own mesh

The mock adapter SHALL implement `compare_with_mesh` through the same comparison code, on its mock STL (a tetrahedron with corners `(0,0,0)`, `(10,0,0)`, `(0,10,0)`, `(0,0,10)`), and SHALL refuse a non-part as the real adapter does.

#### Scenario: The mock part against itself
- **WHEN** the mock part is compared with an STL of the same tetrahedron at `axis: "z"`, `heights_mm: [5]`
- **THEN** there is one pair with areas 12.5 and 12.5, `area_diff_mm2` 0 and `max_extent_diff_mm` 0
