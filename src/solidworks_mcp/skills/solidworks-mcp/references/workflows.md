# Workflows

Tool sequences for the common jobs, with the check to make after each
step. Values are examples; sizes are millimetres.

## A plate with a hole and rounded top edges

1. `create_part` with `name: "plate"`.
2. `create_sketch` with `plane: "Top"`.
3. `add_rectangle` from (-50, -30) to (50, 30).
4. `exit_sketch`.
5. `create_extrusion` with `depth: 10`. Check `measured_change`: the box
   should grow to 100 x 10 x 60 (a Top-plane sketch extrudes along y).
6. `create_sketch` with `plane: "Top"`, then `add_circle` at (0, 0) with
   `radius: 5`, then `exit_sketch`. The reply names the sketch, e.g.
   "Sketch2".
7. `create_cut_extrude` with `sketch_name: "Sketch2"`,
   `end_condition: "ThroughAll"`, `depth: 10`. Leave out
   `reverse_direction`: the cut then tries both sides of the sketch plane and
   keeps the one that removes more. Here that is up into the block, the
   side the extrusion grew (`direction_flipped: false`). Check that the
   volume dropped by about π·5²·10 ≈ 785 mm³.
8. `select_face_edges` with `side: "top"` and `edge_type: "linear"`, then
   `add_fillet` with `radius: 2` and `edge_names: []`.
9. `verify_model` with `bounding_box_mm: [100, 10, 60]`,
   `solid_body_count: 1`, `feature_count: 3`, and a `volume_mm3` with a
   `relative_tolerance` wide enough for the fillets.
10. `set_material` (find the exact name with `list_available_materials`),
    then `save_part` with a `file_path` ending in `.sldprt`.

### The same plate in fewer calls

Steps 1-5 as one `execute_workflow` call:

```json
{"steps": [
  {"tool": "create_part", "arguments": {"name": "plate"}},
  {"tool": "create_sketch", "arguments": {"plane": "Top"}},
  {"tool": "add_rectangle", "arguments": {"x1": -50, "y1": -30, "x2": 50, "y2": 30}},
  {"tool": "exit_sketch"},
  {"tool": "create_extrusion", "arguments": {"depth": 10}}
]}
```

Check step 5's `measured_change`, then send steps 6-7 the same way.

## Sketching somewhere other than the three planes

`create_reference_plane` with `reference: "Top Plane"` and `offset: 10` makes
a plane 10 mm up; its name (e.g. "Plane1") goes into `create_sketch`.

- At an angle: `create_axis` first (its reply names the axis, e.g. "Axis1"),
  then `reference`, `angle` (degrees) and `axis`.
- On or off a face, sloped or not: `face_point`, a point on the face in mm,
  with `offset` (0 puts the plane on the face).
- Anywhere: `point` (mm) and `normal`, e.g. a vertical plane through a cell
  centre at 30 degrees: `point: [20, 0, 10]`, `normal: [0.5, 0, 0.866]`. A
  `warning` says when SolidWorks turned its normal the other way.
- An axis anywhere: `create_axis` with `point` and `direction` (or
  `second_point`), e.g. `point: [20, 0, 10]`, `direction: [0, 1, 0]` for a
  vertical axis through that centre; then `angle` and `axis` turn a plane
  about it. Both are built on a line in a 3D sketch of their own, so exit
  any open sketch first.

Every plane's reply has `axes`; `plane_axes` reads them for any plane
without opening a sketch. A plane made from a face or a point has sketch
axes you can't guess (x = +Z, y = (-0.5, 0.866, 0)): rather than working out
sketch coordinates, draw by model points.

## Drawing by model points on any plane

1. `create_sketch` on the plane.
2. Give `add_circle`, `add_line`, `add_rectangle`, `add_arc`,
   `add_centerline` or `add_outlines` `coordinates: "model"`, with the z
   values too (`center_z`, `z1`, `z2`; in `add_outlines` every point is
   `[x, y, z]`, arc centres too). Each point is projected onto the sketch
   plane; one more than 0.01 mm off it is refused, naming the point and its
   distance. The reply keeps sketch coordinates and adds `projection`.
3. A rectangle's sides still run along sketch x and y, and an arc's
   `direction` is as seen looking against the plane's normal.

## Many holes or a whole outline in one call (a honeycomb deck)

`add_outlines` draws any number of outlines in one call, so a deck of 25
hexagonal holes is one call, not 150 `add_line` calls.

1. Extrude the plate first (`create_sketch` on "Top", `add_rectangle`,
   `exit_sketch`, `create_extrusion`).
2. `create_sketch` on "Top" again. Its reply's `sketch.axes.summary` says
   sketch y is model -Z there: work out the hole centres in sketch x and y.
3. `add_outlines` with one `{"points": [[x, y], ...]}` per hexagon (six
   corners; it closes them). For a shape with curves, give `start` and
   `segments` instead, e.g. a slot: `{"start": [0, -5], "segments": [{"to":
   [20, -5]}, {"type": "arc", "center": [20, 0], "to": [20, 5]}, {"to": [0,
   5]}, {"type": "arc", "center": [0, 0], "to": [0, -5]}]}`.
4. `exit_sketch`, then `create_cut_extrude` with `end_condition: "ThroughAll"`.
   Check the volume fell by the holes' total area times the thickness.

A blind cut's reply gives `cut_extrude.floor_area_mm2`. A `warning` that it
removed far less than floor area times depth means most of the depth was
outside the part (a sketch on an offset plane, or the wrong side): don't
build on it; `undo`, then fix the plane or depth, or use `"UpToNext"` or
`"ThroughAll"`.

## Before a 3D print

1. `verify_model` with the size and volume you expect.
2. `check_wall_thickness` with your printer's thinnest wall as
   `min_wall_mm` (0.8 mm is two 0.4 mm nozzle lines). Its `thin_spots` say
   where each wall is too thin; check a doubtful one with `measure_entities`.
   A wall meant to be thin (a snap-fit flexure) goes in `exclude_boxes_mm`
   as a box around it: its points are reported under `excluded` and don't
   count against `ok`.
3. `check_printability` with the `build_direction` you'll print in (the
   way the part grows from the plate; "y" for a part standing on its base
   with Y up). It lists islands, bridges, suction cups and the area needing
   supports; try each orientation you might print in and keep the one with
   the fewest. A pocket that opens toward the plate is a suction cup on a
   resin printer: turn the part over or vent the pocket. To let it choose,
   give `orientations` (26) instead of `build_direction`: it checks the part
   in the direction needing the least support and gives `rotate_to_z`, the
   turn that makes that direction +Z in a slicer. `min_feature_mm` (0.4 on
   resin) also lists the regions thinner than that, from the mesh; check one
   with `check_wall_thickness`.
4. `export_stl`. For a smoother mesh than the user's STL options give, pass
   `deviation_mm` (0.01 is fine for most prints) and `angle_deg`; `units`
   and `binary` set the file's units and format. They apply to this file
   only, and the user's options are put back.

## A bent plate or other wall from an open outline

Draw the outline as an open chain of `add_line` (the plate's centreline or
one edge), `exit_sketch`, then `create_extrusion` with `thin_feature: true`,
`thin_thickness` and `thin_side` (`"one_side"`, `"other_side"` or `"mid_plane"`).
`both_directions: true` extrudes half the depth each side of the sketch
plane; the reply's `end_condition` says `MidPlane`.

## A revolved part (a knob or a shaft)

1. `create_sketch` on "Front".
2. Draw half the profile with `add_line` (closed), and the axis with
   `add_centerline` along it.
3. `exit_sketch`, then `create_revolve` with `angle: 360`. It uses the sketch
   you just closed and the centreline in it.

## A ring of holes

1. One sketch on the face's plane: `add_circle` for the first hole at its
   radius from the origin.
2. `sketch_circular_pattern` with that circle's `id` and `count: 6`. The
   centre is always the sketch origin.
3. `exit_sketch`, then one `create_cut_extrude`.

Or cut one hole and repeat the feature: `create_axis`, then
`pattern_circular` with the cut, the axis and `count`. Give `angle` to
spread them evenly (360 by default), or `step_angle` for the degrees between
holes (3 holes 60 degrees apart: `count: 3`, `step_angle: 60`);
`reverse_direction` goes the other way round.

## The same cut on every face of a hexagonal cage

1. One plane on one face (`create_reference_plane` with `point` and
   `normal`), one sketch on it drawn by model points, one
   `create_cut_extrude`.
2. `pattern_circular` with the cut, `axis_point` (the cage's centre),
   `axis_direction` (its axis, e.g. `[0, 1, 0]`) and `count: 6`. The axis is
   made first and kept (`pattern.axis_created`), so the next pattern about it
   can name it as `axis`. A pattern that fails removes the axis it made.

## Circles or polygons that cross or touch

SolidWorks won't extrude a sketch whose closed contours cross (two touching
rings, a hexagon over a circle): the error says `sketch_contours_intersect`.
Give `create_extrusion` or `create_cut_extrude` `regions: "all"` to build
every region they enclose, which SolidWorks unions into one solid. A contour
wholly inside another is filled too: draw holes in a sketch of their own and
cut them. Without `regions`, such a failure is retried this way when no
contour lies inside another, and the reply's `warning` says so.

## A row or grid of slots or holes

Cut (or extrude) the first one, then `pattern_linear` with its name in
`features`, `direction`, `count` (the original included) and `spacing` (mm):
8 slots 12 mm apart along x is `direction: "x"`, `count: 8`, `spacing: 12`.
`"-x"` goes the other way; a reference axis or plane name, or "x,y,z" mm on a
straight edge, works as a direction too. `direction2` with `count2` and
`spacing2` makes a grid. Check `measured_change`: the volume should change by
about (instances - 1) times the first feature's. A pattern that changes
nothing fails, and the empty pattern is deleted; try `reverse_direction`.
`bodies: true` repeats solid bodies (names from `list_bodies`).

## Starting a feature at a depth, and drafted or mid-plane cuts

- `start_offset_mm` on `create_extrusion` or `create_cut_extrude` starts it
  that far from the sketch plane: positive along the sketch normal (the side
  `create_extrusion` grows into), negative against it. Under a block that grew
  up from the Top Plane, a Top Plane sketch with `start_offset_mm: 2` starts
  2 mm inside the block, with no `create_reference_plane`. Check the bounding
  box and volume in `measured_change`.
- `end_condition: "MidPlane"` on `create_cut_extrude` cuts `depth` mm in
  total, half each side of the sketch plane (a slot through the middle of a
  wall).
- A `draft_angle` tapers inward (narrower at the far end, a cut narrower as
  it goes deeper); `draft_outward: true` flares it. A lead-in, wider at its
  mouth, is a drafted cut sketched at the mouth with the default inward
  draft.

## A conical groove or seat

1. `create_sketch` on a plane through the axis (e.g. "Front" for an axis
   along y), draw the cross-section of the material to remove as a closed
   outline (`add_outlines`), and the axis with `add_centerline` (or make one
   with `create_axis`).
2. `exit_sketch`, then `create_cut_revolve` (`angle` 360 by default; give
   `axis_entity` for a reference axis). Check the volume dropped.

## Labels: raised or engraved text

`emboss_text` does it in one call: `text`, `depth` (mm), `plane` (or
`face_point` on a flat face, with `x`, `y` counted from that point), `height`
(mm) and `mode` (`"emboss"` or `"engrave"`). Check `measured_change`: an
emboss adds volume, an engrave removes it. The reply's `sketch.axes` says
which way sketch x and y point, to place the next label.

Step by step: `create_sketch`, `add_sketch_text` (`x`, `y`, `height`,
`font`, `angle`; a sketch of its own: a profile and text in one sketch won't
extrude), `exit_sketch`, then `create_extrusion` or `create_cut_extrude` with
that `sketch_name`.

## Changing an existing model

1. `open_model`, then `list_features` and `list_model_dimensions`. On a long
   tree, `list_features` with `include_standard: false` leaves out the
   folders, origin and default planes, and `last` gives only the newest
   features.
2. Change it where it stands; don't delete and re-create a feature, which
   takes its dependents (fillets, patterns) with it:
   - an extrude's or cut's depth, end condition or direction, a revolve's
     angle, a fillet's radius: `edit_feature` with `name` and the change;
   - a feature that stands on an offset plane, to move it: move the plane
     with `edit_feature` (`offset` in mm, `flip` for the other side) rather
     than cutting or adding material; its reply gives a point of the plane
     before and after;
   - any other dimension: `set_dimension` with the full name (e.g.
     "D1@Boss-Extrude1") and the new value in its `units` (mm, degrees, or
     a count);
   - a profile: `edit_sketch`, `delete_sketch_entities` with the ids it
     lists (ids change each time a sketch is opened, so use the latest
     reply's; `all: true` deletes the whole profile), the `add_*` tools for
     the new shape, then `exit_sketch` (draw before you exit: SolidWorks
     deletes a sketch exited empty);
   - a fillet or chamfer whose edges an edit removed (it fails to rebuild):
     `select_edges` for the new edges, then `edit_feature` with its `name`
     and `edges: "selection"` (or the rules as `edge_rules`). It keeps its
     name, radius and place in the tree; its reply says how many edges
     SolidWorks took;
   - a new feature mid-tree: `set_rollback` with `after` the feature it
     builds on, add it, then `set_rollback` with no input.
3. `rebuild_model`: an error names each failing feature and how to fix it.
4. `verify_model` with the new expected size.

To turn a feature off without deleting it, `suppress_feature`. After
`delete_feature`, call `list_features`: dependents may have gone too.
Deleting a cut or boss leaves its own sketch (the reply's
`absorbed_sketches_left` names it); `delete_absorbed: true` removes it too.

To hide planes, axes or sketches (before a screenshot, say), use
`set_visibility` with `types`; its reply gives each type's `before`, so a
second call can put them back.

## Reading a model without changing it

To look inside someone's file, `open_model` with `read_only: true`:
SolidWorks then saves nothing over it (the reply says whether it really is
read-only; a file already open for editing isn't). Then:

- `get_feature_definition` with a feature's `name`: an extrude's depth and
  end condition, a fillet's radius, a pattern's count, a sketch's text
  (string, font, height). It never opens the feature for editing.
- `get_sketch` with a sketch's `name`: its plane and segments in sketch mm,
  text included, without entering the sketch (`edit_sketch` does, and can
  mark the part changed).
- `measure_entities` on two faces: for parallel planes, `distance_m` is the
  gap between them (`normal_distance_m`), not the distance between centres.

`list_references` with a `file_path` lists what a part, assembly or drawing
uses, and whether each file is there, without opening it. To move or rename
a file and keep the assemblies and drawings that use it working,
`move_document` (a `dry_run` first lists what it would change; the files
involved must be closed).

## An assembly

1. Save each part first (`save_part`): `insert_component` takes a file path.
2. `create_assembly`, then `insert_component` for each part, with the
   position of its origin in mm (`fixed: false` to float the first one).
   To put a part at an exact place and angle without mates (a stand-in at a
   height, a copy stacked above), `set_component_transform` with
   `origin_mm`, and `rotation_deg` or `matrix`; it reads the placement back.
3. `list_components` for the instance names (e.g. "plate-1").
4. `add_mate` between components. Each side is a reference plane or axis
   name (`entity_a`: "Front Plane", "Axis1") or a face (`face_a`: a point on
   it, in mm, in that part's own coordinates, as you modelled it). A pin in a
   hole: `concentric`, a point on the pin's round face and one on the hole's
   wall.
   The first part inserted is fixed. To let mates move it, or to hold a part
   still, use `set_component_state` (`fixed`); to let a sub-assembly's own
   mates move in the main assembly (a crank that turns), `flexible: true`.
5. `check_interference`, then `verify_model` with `component_count`,
   `mate_count` and `no_interference: true`. For a gap that must stay open
   (a latch's 0.25 mm play), `measure_clearance` with the two components
   gives the smallest gap and where it is; `export_image` with `section`
   (a plane and an offset) shows it cut open.
6. `save_assembly`, or `pack_and_go_assembly` for a self-contained folder.

## A hinge, latch or lid that turns: where it stops, how close it passes

1. Find the hinge axis: its direction (`axis`: "z", or [x, y, z]) and a
   point on it (`pivot_mm`, the pin's centre), in the assembly's coordinates
   for components or the part's for bodies of one part.
2. `rotation_clearance` with the part that turns as `moving`, the one it
   must clear as `other`, and `range_deg` from where it is now (0): [0, 180]
   for a lid that only opens. It steps out both ways, narrows each contact
   to `tolerance_deg`, and reports the first-contact angle each way (the
   open stop), the smallest gap and `closest_pass`, a near miss on the way
   (a latch clearing a wall). Faces resting together at the start don't
   count as contact; an overlap does.
3. A thin wall can be passed between two steps: the reply says how far a
   point moves per step. Lower `step_deg` for thin parts.
4. In an assembly the component is moved while it runs and put back after.
   Mates that fix its angle stop it: suppress them (keep the concentric one)
   and run again. In a multibody part nothing moves (temporary copies).
5. To leave a component opened at an angle, `set_component_transform` with
   `turn_deg`, `turn_axis` and `turn_pivot_mm`; it turns from where the
   component is.

## Fill lines and volumes by a plane

`volume_by_plane` with a `plane` (or `point_mm` and `normal`) gives the
volume, and the mass when a density is known, above and below it, per body,
without cutting the part. Above is the side the normal points to. With
`enclosed: true` it also gives what the part holds filled to that line, in
ml (a cup's pocket below a fill line). For fill marks at several heights,
call it once per plane (`create_reference_plane` with an offset makes them).

## A part's mass without a library material (a resin)

`set_density` with `density_g_cm3` (1.15 for a typical resin) gives the part
its own density, saved with it; mass properties and `verify_model` then use
it. A part with a material is refused unless `remove_material` takes it off.
If the reply says SolidWorks doesn't use it, `get_mass_properties` with
`density_g_cm3` gives the mass at that density from the volume.

## One part's shape cut into another (a lid over raised labels)

Work in one multibody part, not an assembly:

1. Open the part to cut (the lid) and `insert_part` the mating part (the
   holder), with `translate_mm` (and `rotate_deg`) to where it sits.
2. `list_bodies` for the body names: the lid, and the holder's bodies.
3. `make_mating_negative` with the lid as `target`, the raised features as
   `tools` and the fit's `clearance_mm` (0.15 for a print). It reports the
   volume removed and the residual overlap (what of the tools still lies in
   the lid), which should be about 0. It can take a few seconds per tool.
4. `body_clearance` between a tool and the lid confirms the gap; then
   `delete_keep_body` removes the holder's bodies.

`combine_bodies` adds, subtracts (no clearance) or intersects bodies;
`move_copy_body` places them; `split_body` cuts a body along a plane or sketch.

## A drawing

The whole drawing in one call: `generate_drawing_package` with the saved
model's `model_path`. It makes an A3 drawing with views, dimensions, title
block and notes, checks nothing overlaps, and saves it with a PDF.

Step by step instead:

1. `create_drawing`, then `create_drawing_view` for each view.
2. `insert_model_dimensions`, then `arrange_dimensions` (it reports any
   overlap it couldn't clear; `set_sheet_scale` gives more room).
3. `auto_center_marks`, `add_note`, and for an assembly `insert_bom_table`.
4. `save_file` with a `.slddrw` path, and `export_pdf`.

## Exporting

`export_step`, `export_stl` and the other `export_*` tools write the active
document; the file extension decides the format, so name it to match.
Each reads the file back and fails with `export_invalid` when it is missing,
empty or not that format. An export with something selected writes only the
selection, so they clear the selection first (and every feature tool clears
what it selected when it ends). `export_image` saves a picture in a chosen
view: useful to show the user the result.
