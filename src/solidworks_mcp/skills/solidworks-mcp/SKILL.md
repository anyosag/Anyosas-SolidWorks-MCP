---
name: solidworks-mcp
description: How to build and check SolidWorks parts, assemblies and drawings with the SolidWorks MCP server's tools. Use whenever you call its tools (create_part, create_sketch, create_extrusion, verify_model, generate_drawing_package and the rest), plan a CAD model, or recover from one of its errors.
---

# Using the SolidWorks MCP server

These tools drive a real SolidWorks session through its API. Each tool's own
description says what it does and what it needs; this skill covers what
spans tools: the order to call them in, units, choosing faces and edges,
checking the result, and what doesn't work yet.

## Before anything

- Call `get_model_info` first. It says which document is active (it follows
  a switch the user made in the SolidWorks window). `list_open_documents`
  lists every open document; `activate_document` switches.
- Every tool that works on a document takes `document`: the title, file name
  or path of an open one. Give it when more than one document is open, or
  when someone else may be using SolidWorks; the server activates it first.
  A `warning` saying another MCP server is on the same document means two
  clients are editing it: tell the user.
- Calls that don't depend on each other can share one message: Claude Code
  runs them one after another, and the server keeps a short pause between
  SolidWorks calls on purpose (driving SolidWorks back-to-back crashes it).
  Wait for a result before a call that needs it. With a client that runs
  calls at the same time, call tools one at a time.
- Each model turn costs seconds, most of a build's time. Run a known
  sequence in one call with `execute_workflow` (below).
- If your client loads tool schemas on demand (a tool search), load every
  tool the job needs in one search, not one per tool.

## The build order

A solid feature always comes from a closed sketch:

1. `create_part` (or `open_model` for an existing file).
2. `create_sketch` on a plane: "Front", "Top", "Right", or a plane from
   `create_reference_plane`. Pass `sketch_name` to name it; the name is
   given when `exit_sketch` closes it, and that reply has the final name.
3. Draw: `add_rectangle`, `add_circle`, `add_line`, `add_arc`, `add_polygon`,
   and so on; `add_outlines` draws whole outlines, or dozens of holes, in
   one call, and `add_sketch_geometry` draws many circles, lines,
   rectangles and centrelines from rows (`circles: [[x, y, r], ...]`). `create_sketch`'s reply says where sketch x and y point in the
   model (on Top, sketch y is model -Z). For a revolve, add the axis with
   `add_centerline` in this sketch, or make a reference axis with
   `create_axis`.
4. `exit_sketch`.
5. The feature: `create_extrusion` (add material), `create_cut_extrude`
   (remove it), `create_revolve`, `create_cut_revolve`, `create_sweep`,
   `create_loft`. Each takes `sketch_name`; without it, the sketch you
   closed last is used. `create_revolve` turns about the sketch's
   centreline, or about the reference axis `axis_entity` names. To start
   an extrude or cut at a depth, give `start_offset_mm` rather than making
   a plane for it.
6. Then edge treatments: `select_face_edges` or `select_edges`, then
   `add_fillet` or `add_chamfer` with empty `edge_names`.
7. Finish with `verify_model` (see "Check the result").
8. `set_material`, then `save_part` with a `file_path`.

Repeat 2-5 for each feature. For holes in a ring or a row, draw every
circle in one sketch (`sketch_circular_pattern`, `sketch_linear_pattern`)
and cut once, or repeat the cut with `pattern_circular` (`step_angle` for
the degrees between instances), `pattern_linear` (rows and grids) or
`mirror_feature`. Labels: `emboss_text` raises or engraves text in one
call; `add_sketch_text` writes it into a sketch of its own.

To change a model that exists, edit in place (`edit_feature`,
`edit_sketch`, `set_rollback`) rather than deleting and re-creating, which
takes dependents with it.

Give `create_part` its `material` when you know the exact name; it saves a
`set_material` call.

## Several steps in one call

`execute_workflow` runs a list of steps in order, exactly as separate calls
would. A step is `{"tool": "add_circle", "arguments": {...}}` or the short
`{"add_circle": {...}}`; `arguments` are that tool's own fields, without an
`input_data` wrapper. Every step is checked before any runs, and all the
problems come back at once, each with its tool's fields. A step's
`document` runs it on that open document. A sketch and its feature (steps
2-5) fit in one call; up to 200 steps per call (split a longer list into
calls run in order: each numbers its steps from 0, and a step can use only
its own call's replies). An argument that is exactly
`"$steps.<i>.<path>"` takes the value at that path in step i's reply, e.g.
`"old_name": "$steps.4.fillet.name"`, so a later step needn't guess a
feature's name. Give a step an `"id"` (`"plane_x"`) and write
`"$steps.plane_x.plane.name"` instead of counting steps. It stops at the first failed step and reports that step's
`error_type` and `hint`; the error's `data.steps` shows what ran. With
`rollback_on_error` it then deletes the features and sketches the earlier
steps added. Each step's entry is a summary (status, message, its own
warning, small facts and checks such as `measured_change` and an export's
file and mesh checks; `detail: "full"` gives whole replies), and `warnings` lists
each warning once. Read them, including each feature's
`measured_change`, before the next call. The last `export_image` picture
comes back in the reply (`export_image` alone returns its picture too), so
look at it there rather than reading the file.

Worked sequences for parts, assemblies and drawings:
[references/workflows.md](references/workflows.md).

## Save tokens

- Batch known steps in one `execute_workflow` call; keep its default
  `detail: "summary"` (and `get_sketch` `detail: "summary"` for a big sketch).
- Write steps short (`{"add_circle": {...}}`), leave out fields at their
  defaults, and give many circles or lines to `add_sketch_geometry` as rows
  rather than one step each: what you write is time too.
- Find edges with `query_geometry` before `select_edges`, so the rules hit
  the first time.
- Call `verify_model` once, at the end, with every value you expect.
- Long lists (`list_features`, `get_sketch` segments, `list_components`)
  come 50 at a time with `truncated` and `next_offset`; filter (`types`,
  `last`) before paging with `offset`.
- Tools come in groups; the server may list only some
  (`SOLIDWORKS_MCP_TOOL_GROUPS`, e.g. `core,bodies`). Turn a group on with
  `enable_tool_groups` only when you need one of its tools. A field
  description ending "..." is cut short: `describe_tool_inputs` gives it whole.

## Units

Inputs are millimetres and degrees, with two exceptions that take metres:
the `"x,y,z"` and `"face:x,y,z"` point specs in `add_fillet` and
`add_chamfer` `edge_names`, and every coordinate and result of
`measure_entities`. When a field's description says metres, believe it.
Details and the model axes: [references/selection-and-units.md](references/selection-and-units.md).

## Choosing faces and edges

- To treat the edges of one face, use `select_face_edges` (by `side`, a
  `point` in mm, or a `plane`), then `add_fillet` or `add_chamfer` with empty
  `edge_names`. Do it right before the fillet: anything that rebuilds or
  selects in between (including `calculate_mass_properties`) clears the
  selection.
- For edges by rule, use `select_edges`: the edges a `feature` made (a cut's
  rims and walls), then only the `linear` ones `parallel_to` an axis, those
  `inside_box`, or a length range. When nothing matches, the error says
  which rule removed them. Without a `feature`, a part of more than 500
  edges is refused at once (`edge_scan_too_large`; reading costs about
  0.1 s an edge): give a `feature` or a tight `inside_box`, or
  `scan_whole_part` when you mean the whole part.
- If the user says "this face" or "these edges", call
  `get_selected_entities` to read what they clicked rather than guessing.
- A point on an edge where two faces meet can pick the wrong edge; give a
  point inside a face (`"face:x,y,z"`) instead.

## Check the result

A tool returning `status: success` means SolidWorks accepted the call, not
that the geometry is what you meant. After a feature:

- `create_extrusion`, `create_revolve` and `create_cut_extrude` return
  `measured_change` (volume and bounding box before and after). A cut whose
  volume didn't drop, or an extrusion whose box didn't grow as planned, is
  wrong even though it succeeded.
- At the end, call `verify_model` with what you expect: `bounding_box_mm`,
  `volume_mm3`, `mass_kg`, `feature_count`, `solid_body_count`, and for an
  assembly `component_count`, `mate_count`, `no_interference`. Work out the
  expected values before building. A `fail` verdict still has
  `status: success`; read `verdict`.
- For one size, `measure_entities` or `get_dimension`.

## When something fails

Every error carries `error_type` and a `hint`; follow the hint. The common
ones, and `rebuild_model`, `undo` and what to tell the user:
[references/errors-and-limits.md](references/errors-and-limits.md).

## What doesn't work yet

- Every tool has a support level, in its tags (`level:stable`, `level:beta`,
  `level:experimental`, `level:planned`). A beta tool works but has a known
  gap, and an experimental one hasn't been checked on real SolidWorks: their
  description opens with "Beta: ..." or "Experimental: ...", and a beta
  tool's successful result repeats its sentence as `warning`. Read it, and
  check the result (`verify_model`, `list_features`) before building on it.
- 39 tools are planned: nothing implements them. The server leaves them
  out of its tool list unless it runs with
  `SOLIDWORKS_MCP_SHOW_PLANNED_TOOLS=true`; then their description says "Not
  available yet" and every call returns `not_supported`. Use the tool their
  description names instead.
- Some tools ignore some of their inputs, and a few behave in ways you must
  work around. The list, with the workaround for each:
  [references/errors-and-limits.md](references/errors-and-limits.md).

## A multibody master and its part files

`save_body_as_part` writes one body to its own part file (Save Bodies) and
reads the file back. After changing the master, `resplit_bodies` updates
those files in place; `list_save_bodies` shows which file each body goes
to, and `edit_save_bodies` changes the feature's assembly and options (to
move a body to another file, delete the feature and save it again).
`export_assembly_parts_stl` writes
one STL per part of an assembly.

## Rendering or inspecting in Blender

Blender can't open SolidWorks files, reads an STL's numbers as metres, and
is +Z up. Don't export an STL and import it by hand: call
`export_for_blender` (GLB with parts and colours by default, `up_axis: "z"`
for a part modelled Z-up) and run the `blender_code` it returns with the
Blender MCP's `execute_blender_code`; check that its result says
`size_matches: true`. A re-export with the same name replaces the earlier
import. The round trip, renders and what each format keeps:
[references/blender-handoff.md](references/blender-handoff.md).

## Long sessions

SolidWorks slows down as documents pile up. Close a part with `close_model`
once it is saved and you are done with it. Closing an assembly can leave its
parts loaded with no window (their `~$` lock files stay):
`list_open_documents` with `check_hidden` lists them, and `close_model` with
`close_leftovers` closes the ones without unsaved changes.

## When to write VBA instead

Only when no tool does the job (a shell, an option a tool doesn't expose):
[references/vba-fallback.md](references/vba-fallback.md). The `generate_vba_*`
tools return macro text for the user to run; they don't change the model.
To call the API yourself (its Remarks, enum values, and C# run on the live
session with `run_api_snippet`, if the user turned it on):
[references/api-escape-hatches.md](references/api-escape-hatches.md).
