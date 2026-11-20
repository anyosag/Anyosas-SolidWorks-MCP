## ADDED Requirements

### Requirement: A mesh file is sliced into closed loops

The server SHALL register a `slice_mesh` tool taking:
- `path`: an STL (binary or ASCII) or a 3MF file, chosen by its extension (any case);
- `axis`: `x`, `y` or `z` (any case);
- `heights_mm`: one to 100 finite numbers, the plane's position along `axis` in mm;
- `frame`: `object` (the default) or `build`; it matters for a 3MF only.

It SHALL NOT call the adapter, and it needs no open document. An STL is read in mm.

On success the reply's `data` SHALL hold:
- `file` (the path), `format` (`stl` or `3mf`), `axis`, `frame` and `triangles` (how many were read);
- `section_axes`: the two coordinates of every point, `["y", "z"]` for `x`, `["x", "z"]` for `y`, `["x", "y"]` for `z`;
- `sections`: one entry per height, in the order given, each with `height_mm` and `loops`.

`loops` SHALL hold the closed loops where the plane cuts the mesh, largest area first. Each loop SHALL have:
- `area_mm2`, `min_mm` and `max_mm` (the extents as `[u, v]`), rounded to 4 decimals;
- `point_count`;
- `points_mm`: the points as `[u, v]`, rounded to 4 decimals, without points that lie within 1e-4 mm of the line through their neighbours.

A plane that misses the mesh SHALL give an empty `loops` list, not an error. A vertex exactly on the plane counts as above it. The tool SHALL be read-only.

#### Scenario: A cube cut through the middle
- **WHEN** a 20 mm cube STL with a corner at the origin is sliced with `axis: "z"` and `heights_mm: [10]`
- **THEN** `sections[0].loops` holds one loop with `area_mm2` 400, `min_mm` `[0, 0]`, `max_mm` `[20, 20]` and `point_count` 4

#### Scenario: A plane on a face
- **WHEN** the same cube is sliced at `heights_mm: [0, 20]`
- **THEN** the loops at 0 are empty, and the loops at 20 hold the 400 mm² loop (a vertex on the plane counts as above it)

#### Scenario: A plane outside the mesh
- **WHEN** the same cube is sliced at `heights_mm: [-1, 25]`
- **THEN** both entries have an empty `loops` list and the call succeeds

#### Scenario: Another axis
- **WHEN** the same cube is sliced with `axis: "x"` and `heights_mm: [10]`
- **THEN** `section_axes` is `["y", "z"]` and the loop has `area_mm2` 400 and `max_mm` `[20, 20]`

#### Scenario: A hollow part gives a hole loop
- **WHEN** a closed mesh is a 20 mm cube with a closed 10 mm cube void from 5 to 15 mm on every axis, sliced on `z` at 10
- **THEN** `loops` holds two loops: area 400 (extents 0 to 20) and then area 100 (extents 5 to 15)

#### Scenario: Several heights keep their order
- **WHEN** `heights_mm` is `[15, 5]`
- **THEN** `sections` has the height 15 first and 5 second

### Requirement: Bad input is refused before the file is read

An empty `heights_mm`, more than 100 heights, a height that is not finite, an unknown `axis` or `frame`, or an extension that is not `.stl` or `.3mf` SHALL fail with `error_type: invalid_input`. A path that doesn't exist SHALL fail with `error_type: not_found`. A file that can't be read as a mesh (not an STL, a 3MF that doesn't unzip or parse, no triangles) or a mesh of more than 1,000,000 triangles SHALL fail with `error_type: invalid_input` saying why.

#### Scenario: An unknown axis
- **WHEN** `slice_mesh` is called with `axis: "w"`
- **THEN** the call fails with `error_type: invalid_input` naming the axes `x`, `y` and `z`

#### Scenario: A missing file
- **WHEN** `path` names a file that doesn't exist
- **THEN** the call fails with `error_type: not_found`

#### Scenario: A file that is not a mesh
- **WHEN** an `.stl` file holds text that is not an STL
- **THEN** the call fails with `error_type: invalid_input` and the message names the file

### Requirement: A 3MF is read in mm, with its transforms

For a 3MF, `slice_mesh` SHALL:
- scale vertices and every transform's translation by the model's `unit` (`micron`, `millimeter`, `centimeter`, `inch`, `foot`, `meter`; `millimeter` when absent), so the points are in mm, and refuse an unknown unit;
- read a `transform` attribute as 12 numbers, the 4 × 3 matrix by rows, a point going `p · R + t`;
- follow `components` through the object tree, including components in another model part (each part with its own `unit`), applying each component's transform inside its parent's;
- apply the build item's transform only when `frame` is `build`;
- join the triangles of every build item.

It SHALL parse the XML so that a DTD or an entity is refused, with `error_type: invalid_input`. It SHALL refuse components that nest deeper than 16 levels or refer back to themselves, and a model part that unpacks to more than 256 MB.

#### Scenario: A box in inches, in its own frame
- **WHEN** a 3MF has `unit="inch"` and one 2 × 1 × 1 inch box with a build item transform `0 1 0 -1 0 0 0 0 1 2 0 0`, and is sliced with `axis: "z"`, `heights_mm: [12.7]` and `frame: "object"`
- **THEN** the loop has `area_mm2` 1290.32 (50.8 × 25.4), `min_mm` `[0, 0]` and `max_mm` `[50.8, 25.4]`

#### Scenario: The same box on the build plate
- **WHEN** the same file is sliced with `frame: "build"`
- **THEN** the loop has `area_mm2` 1290.32, `min_mm` `[25.4, 0]` and `max_mm` `[50.8, 50.8]` (a quarter turn about z, then 2 inches along x)

#### Scenario: A component's transform applies in either frame
- **WHEN** a 3MF in millimetres has an object made of one component, a 10 mm cube from `(0,0,0)` placed by the component transform `1 0 0 0 1 0 0 0 1 30 0 0`, and is sliced on `z` at 5 with `frame: "object"`
- **THEN** the loop has `min_mm` `[30, 0]` and `max_mm` `[40, 10]`

#### Scenario: A file with an external entity
- **WHEN** a 3MF's model part declares an entity
- **THEN** the call fails with `error_type: invalid_input` and nothing is read from the entity

#### Scenario: A component that refers to itself
- **WHEN** an object's component refers to that same object
- **THEN** the call fails with `error_type: invalid_input` naming the cycle

### Requirement: The mesh does not need SolidWorks

`slice_mesh` SHALL work with no document open, no SolidWorks running and the mock adapter alike, and SHALL return the same data in each case. It SHALL run its slicing off the event loop.

#### Scenario: No document is open
- **WHEN** `slice_mesh` is called while no document is open
- **THEN** it succeeds with the same data as with a part open, and no adapter method is called

### Requirement: Other code can slice triangles with `section`

`utils/mesh_sections.py` SHALL expose `section(triangles, axis, height)`:
- `triangles` is a sequence of triangles, each three `(x, y, z)` points in mm;
- it returns a list of closed loops, largest area first, each a list of `(u, v)` points in mm in the plane's two coordinates (`(y, z)`, `(x, z)` or `(x, y)` for `x`, `y`, `z`), with at least three points and the first point not repeated at the end;
- a plane that misses the mesh returns `[]`; a vertex on the plane counts as above it; edges that don't close into a loop are dropped;
- it doesn't change its input, and doesn't promise a point order, a start point or which loops are holes;
- an unknown axis raises `MeshError`.

The module SHALL also expose `area(loop)`, `extents(loop)` (as `(u_min, u_max, v_min, v_max)`), `simplify(loop)`, `as_triangles(flat)` (turning nine-float triangles into this shape) and `load_mesh(path, frame)`. It SHALL need no package beyond the standard library, `defusedxml` and the fork's own `stl_mesh`.

#### Scenario: A tetrahedron cut in half
- **WHEN** `section` is called on the tetrahedron with corners `(0,0,0)`, `(10,0,0)`, `(0,10,0)`, `(0,0,10)` with `axis: "z"` and `height: 5`
- **THEN** it returns one loop of three points: `(0,0)`, `(5,0)` and `(0,5)` in some order, area 12.5

#### Scenario: Triangles as nine floats
- **WHEN** `section` is called on a list of triangles of three points each, and again on `as_triangles` of the same triangles written as nine-float tuples
- **THEN** both calls return the same loops

#### Scenario: Notice kept
- **WHEN** `utils/mesh_sections.py` is opened
- **THEN** its header carries hjbaard's copyright line (© 2026 Henk Jan Baard) and the MIT permission notice, and `THIRD_PARTY_NOTICES.md` at the repo root lists the module with the same notice
