## ADDED Requirements

### Requirement: The region a component sweeps can be outlined

The adapter SHALL expose `swept_region(component, dimension, values, heights_mm, axis, margin_mm, tolerance_mm, frame)`, and the server SHALL register a `swept_region` tool taking:
- `component`: an instance name as `list_components` reports it (a part or a sub-assembly);
- `dimension` and `values`, stepped as `tools/joint-stepping` says;
- `heights_mm`: one or more heights along `axis`, in the frame's coordinates, at which the component is cut;
- `axis`: `x`, `y` or `z` (default `z`), the normal of the cutting planes;
- `margin_mm`: at least 0 (default 0), how far the outline is grown;
- `tolerance_mm`: more than 0 (default 0.2), how far the traced outline may lie from the true edge;
- `frame`: a component whose axes and origin give the coordinates (default: the assembly's).

At each value the adapter SHALL cut the component's tessellated solid bodies at each height, join every section of every step, grow the union by `margin_mm`, and trace its outline. On success the reply's `data` SHALL hold:
- `regions`: largest first, each with `outline_mm` (points `[u, v]` in mm, counter-clockwise), `holes_mm` (a list of clockwise loops) and `area_mm2`;
- `count`, `component`, `frame` (the component's name, or `assembly`), `dimension`, `unit`, `values`, `axis`, `heights_mm`, `margin_mm`, `tolerance_mm`;
- `plane_axes`: the two axes other than `axis`, in x, y, z order, that `[u, v]` follow;
- `moving` (the parts that moved) and `restored`.

The tool SHALL change nothing that lasts: the dimension is put back (`tools/joint-stepping`).

#### Scenario: A bar swung through a quarter turn
- **WHEN** `Bar-1` is a 10 × 10 × 100 mm bar, x from 0 to 100, y from -5 to 5, z from 0 to 10, hinged about the Z axis through the origin (the middle of its end); the joint's angle mate reads 0° with the bar along +X, and turns it counter-clockwise from +Z as the value rises; and an agent calls `swept_region` with `component: "Bar-1"`, `dimension: "D1@Angle1"`, `values` from 0 to 90 in steps of 2 (46 values), `heights_mm: [5]` and `tolerance_mm: 0.1`
- **THEN** `count` is 1, `plane_axes` is `["x", "y"]`, and `area_mm2` is 8874 within 1%

  Hand calculation, with half-width w = 5 and L = 100, so R = √(L² + w²) = 100.125. In polar terms the bar covers, at radius r, an angle range of π for r ≤ w and 2·asin(w/r) for w < r ≤ L. Sweeping it a quarter turn adds π/2 to the range length for every r up to R. So

  A = (3π/2)·w²/2 + 2·[F(R) − F(w)] + (π/2)·(R² − w²)/2, with F(r) = (r²/2)·asin(w/r) + (w/2)·√(r² − w²).

  This gives A = 8874.4 mm². (A numerical check on a 0.25 mm grid gives 8872.8.)

  The steps must be close enough that each position of the bar overlaps the last. At the tip, 100 mm × Δ (in radians) must stay under the bar's width of 10 mm, so Δ under 5.7°. This scenario uses 2° steps: at the tip the neighbouring positions overlap by about 6.5 mm, and the outline between two tip corners lies within 100·(1 − cos 1°) = 0.015 mm of the true arc. (Ten 10° steps would leave gaps from r = 57 mm outward and lose about 16% of the area.) The 1% allowance is about 89 mm², which covers the 0.1 mm tolerance over a perimeter of about 450 mm.

#### Scenario: The outline holds the bar's end positions
- **WHEN** the same call is made
- **THEN** the point (99, 0) is inside the region (near the tip at 0°) and so is (0, 99) (near the tip at 90°); (50, 0) and (-3, 50) are inside; (-6, 50) and (80, 80) are outside; and no outline point is farther than 100.3 mm from the origin

#### Scenario: One value gives the part's own section
- **WHEN** the call is made with `values: [0]` (the dimension's value) and the other inputs as above
- **THEN** `area_mm2` is 1000 within 1% (the 100 × 10 rectangle) and `moving` is `[]`

#### Scenario: A margin grows the outline
- **WHEN** the call is made with `values: [0]` and `margin_mm: 2`
- **THEN** `area_mm2` is 1452.6 within 2%, being 100·10 + 2·2·(100 + 10) + π·2²

#### Scenario: A hole stays a hole
- **WHEN** `Plate-1` is a 40 × 40 × 10 mm plate with a through hole of diameter 10 mm on its middle, its joint's value stays at the original (`values: [30]` for a dimension reading 30°), and `heights_mm` is `[5]`
- **THEN** `count` is 1, `holes_mm` has one loop, and `area_mm2` is 1521.5 within 1% (1600 − π·5²)

#### Scenario: Coordinates in a frame
- **WHEN** the call is made with `values: [0]` and `frame: "Base-1"`, and `Base-1` sits 10 mm along +X from the assembly's origin with no rotation
- **THEN** every `u` in the outline is 10 less than it is with no `frame`, every `v` is the same, and `area_mm2` is the same

#### Scenario: A height the component never reaches
- **WHEN** `heights_mm` is `[20]` and the bar spans z from 0 to 10 at every step
- **THEN** the call fails with `error_type: invalid_input` naming the heights, the frame, and the component's range along `axis`; the dimension is unchanged

### Requirement: Bad inputs are refused before anything moves

The call SHALL fail with `error_type: invalid_input` before any step when `values` or `heights_mm` is empty, `axis` isn't `x`, `y` or `z`, `margin_mm` is below 0, `tolerance_mm` is 0 or below, or the grid the outline would need is larger than the adapter's limit (the message gives the extent, the cell size and a coarser `tolerance_mm` that fits). A `component` or `frame` that isn't in the assembly SHALL fail with `error_type: not_found`, listing what the assembly holds. A frame that is itself moved by the joint SHALL fail with `error_type: invalid_input`, naming it. A suppressed or lightweight component SHALL be refused with the reason and what to do.

#### Scenario: A zero tolerance
- **WHEN** `tolerance_mm` is 0
- **THEN** the call fails with `error_type: invalid_input` and the dimension is not written

#### Scenario: A grid that is too large
- **WHEN** the swept extent is 3000 mm across and `tolerance_mm` is 0.1
- **THEN** the call fails with `error_type: invalid_input` giving the extent and a tolerance that fits

#### Scenario: A frame the joint moves
- **WHEN** `frame` is the bar itself
- **THEN** the call fails with `error_type: invalid_input` saying the frame moves with the joint

### Requirement: The outline step is pure and ported with its notice

The outline SHALL be computed by `utils/swept_region.swept_outline(sections, tolerance_mm, margin_mm)`, a module with no COM, taking sections as lists of closed loops of `(u, v)` points in mm. The module's header SHALL keep the copyright line and MIT permission notice of the code it was ported from (hjbaard's `region_tools.py`, © 2026 Henk Jan Baard), and the repo's `THIRD_PARTY_NOTICES.md` SHALL list it.

#### Scenario: A square
- **WHEN** `swept_outline` gets one section that is the loop (0,0), (10,0), (10,10), (0,10), with `tolerance_mm` 0.2 and `margin_mm` 0
- **THEN** it returns one region with no holes, a counter-clockwise outline, and `area_mm2` of 100 within 1%

#### Scenario: Two overlapping squares join
- **WHEN** it gets two sections, the squares from (0,0) to (10,10) and from (5,0) to (15,10)
- **THEN** it returns one region with `area_mm2` of 150 within 1%

#### Scenario: Nothing to outline
- **WHEN** it gets no loops
- **THEN** it raises `ValueError` ("nothing was cut")

### Requirement: The mock adapter mirrors the payload

The mock adapter SHALL implement `swept_region` with the same payload keys. It SHALL treat each component as its box (12 triangles), turned by the registered joint (`tools/joint-stepping`), and compute the regions with the same slicing and outline code, so the bar scenario can be checked on the mock.

#### Scenario: Mock bar
- **WHEN** the mock has `Bar-1` as a box from (0, -5, 0) to (100, 5, 10), a joint turning it about Z through the origin, and `swept_region` is called with `values` from 0 to 90 in steps of 2 and `heights_mm: [5]`
- **THEN** `count` is 1 and `area_mm2` is 8874 within 1%
