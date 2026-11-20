# Errors and limits

## Reading an error

Every failed call returns `status: "error"`, a `message`, an `error_type`
and a `hint`; some carry `data` with details. Act on `error_type`:

| `error_type` | What to do |
| --- | --- |
| `no_active_document` | Open or create one (`open_model`, `create_part`), or `activate_document`. |
| `no_active_sketch` | `create_sketch` first. |
| `wrong_document_type` | The active document is the wrong kind (a drawing tool on a part); switch with `activate_document`. |
| `invalid_input` | An argument is wrong, or is one a tool no longer takes; the message says what to send instead. Fix the call. |
| `not_found` | A name or path is wrong. List what exists (`list_features`, `list_components`, `list_drawing_views`, `list_open_documents`) and retry with the exact name. |
| `feature_not_built` | SolidWorks accepted the call but built nothing: the sketch isn't closed, the cut misses the body, the depth is wrong. Change the input, don't repeat the call. |
| `sketch_contours_intersect` | An extrude or cut built nothing because closed contours in its sketch cross or touch (the message names them). Make the regions apart or nested, or draw one outline that is their union, then try again. |
| `not_supported` | The tool can't run yet. Use the one its description names. |
| `circuit_open` | Calls are paused after repeated failures. Fix the cause (for example, close and draw the sketch again), then call `reset_circuit_breaker` with the operation, or wait out the timeout, and retry. |
| `solidworks_unavailable` | SolidWorks closed or crashed. Tell the user; once they start it, the next call reconnects. |
| `active_document_changed` | Nothing was done: since your last call, the user or another program switched SolidWorks to another document. If the user didn't say which document to work on, ask; then retry with `document` set to it (the message names both), or call `activate_document` first. |
| `solidworks_reconnected` | SolidWorks restarted and the server reconnected to it; the call did nothing. Your documents and sketches are gone unless the user reopened them: check `list_open_documents`, reopen or recreate what you need, then retry. |
| `confirmation_required` | Nothing was saved: saving the assembly would overwrite the user's own modified part files, listed in `data.files`. Ask the user; if they agree, call the same tool again with `overwrite_modified_references: true`. |
| `solidworks_busy` | Nothing was run: SolidWorks didn't answer for `SOLIDWORKS_MCP_BUSY_WAIT_SECONDS` (30 s by default), busy with a long operation (an earlier call, another program or the user). Wait a minute and retry; if it stays busy, tell the user. |
| `operation_stuck` | The call had no reply or progress for `SOLIDWORKS_MCP_HANG_SECONDS` (15 minutes by default), so the server was restarted. Documents stay open in SolidWorks; the server's own memory (its current document and sketch) starts fresh, and calls that were waiting got this error too. The call may have finished, finished in part or still be running: pick the document again (`list_open_documents`, `activate_document`), check what it did (`list_features`), then go on. If it happens again, SolidWorks itself may be stuck, for example on a dialog: tell the user. |
| `expectation_failed` | The feature was built, but the part doesn't meet the call's `expect`; `data` has the feature, its `measured_change` and each check. Don't call the tool again: that adds another feature. Change it (`edit_feature`) or remove it (`undo`, `delete_feature`), then check again. |
| `edge_scan_too_large` | Nothing was selected: `select_edges` would have read every edge of a large part (`data` gives the count and the time it would take). Give a `feature`, or a tighter `inside_box`; set `scan_whole_part` only when you mean the whole part. |
| `operation_stopped` | The server stopped the call part-way: it ran past `SOLIDWORKS_MCP_OPERATION_TIMEOUT_SECONDS`, or the client went away. Check what it did (`list_features`), then do the work in smaller pieces: fewer edges or outlines per call. |
| `solidworks_dialog` | SolidWorks opened a dialog during the call; `data.dialog` has its title, text and buttons. The server cancelled it (it never answers Save or Don't Save), so the call may not have finished. Tell the user what it asked if it needs a decision; otherwise fix the cause and retry. |
| `export_invalid` | SolidWorks said the export worked, but the file it left is missing, empty, cut short or not its format; `data.file_check` says what was read. Check the extension matches the format and the folder can be written, then export again. |
| `com_member_not_found`, `com_type_mismatch`, `com_wrong_thread` | A server problem, not a modelling one. Stop and tell the user the tool, the arguments and the message. |
| `api_snippet_failed` | Your `run_api_snippet` code failed; what it did before failing stays. Its data has the compile errors (with line numbers), the exception or the timeout, and stdout and stderr. Fix the code, check it with `compile_only`, and read the member's Remarks with `get_api_remarks`. |

Don't retry the same call unchanged: if it failed once with the same input,
it will again.

## Recovering

- `rebuild_model` rebuilds and, when features fail, returns an error whose
  `data.errors` names each failing feature, what it depends on and a `hint`.
- `undo` steps back one or more operations; its reply says whether the
  feature tree changed.
- `suppress_feature` turns off a feature without deleting it;
  `delete_feature` removes it and its dependents. Ask the user before
  deleting: a sketch takes every feature built from it with it, and the
  reply's `warning` names what else went.
- `get_model_info` and `list_features` show where the model stands now.

## Tools that can't run yet

39 tools are planned: nothing implements them (support level `planned`).
The server lists them only when it runs with
`SOLIDWORKS_MCP_SHOW_PLANNED_TOOLS=true`; their description says "Not
available yet", and every call returns `not_supported`. They are
mostly batch, template, macro-recording and drawing-analysis tools, plus
`tutorial_simple_hole`, `add_dimension`, `create_section_view`, `create_detail_view`,
`update_title_block`, `analyze_geometry`, `get_material_properties` and
`batch_export`. Each description names what to use today.

## Known faults and how to work around them

| Fault | Workaround |
| --- | --- |
| `add_mate` can't mate edges or vertices, only planes, axes and faces. | Mate the faces that meet at the edge. |
| SolidWorks closes every document of the name it is given, and a part and an assembly of one name share a title when Windows hides extensions. `close_model` then closes the one by its full path, or as the active document; when it can't, it fails and closes nothing. | Activate the one to close (`activate_document` with its file name and extension), then call `close_model` again; or close it in SolidWorks. |
| SolidWorks refuses a pattern or mirror whose `features` hold a pattern beside the features it already repeats (its seeds, and an inner pattern's). `pattern_linear`, `pattern_circular` and `mirror_feature` leave those out first and name them in `left_out` and the `warning`; one they can't be sure of is left out only if SolidWorks refuses it. | Name the pattern without its seeds: it repeats them with it. If SolidWorks still makes nothing, the `hint` says a pattern is listed; pattern the other features in a call of their own. |
| `set_dimension` refuses a dimension an equation sets (`invalid_input`, naming the equation; `driven_by` in `list_model_dimensions`). | Change the equation, or the variable it reads, with `set_equations`; or free the dimension with `delete_equation`. |
| `create_thread` refuses a size SolidWorks' thread library doesn't hold and has no other size of that pitch for (`invalid_input`, listing the library's sizes of that diameter). A size whose pitch the library holds (M30x1 from `M10x1.0`) is cut with that profile; `data.profile.borrowed` says so. | Pick one of the sizes listed; SolidWorks would cut a meaningless groove for another. |
| `create_thread` refuses an edge of the wrong diameter (`invalid_input`, naming the diameter needed and found). | Outside, make the rod the nominal diameter; inside, drill the ISO basic minor diameter (D - 1.0825 P) first, e.g. with `create_cut_extrude`. A deliberate print clearance is `diameter_offset_mm`. |
| `create_thread` refuses a thread of too many turns (`invalid_input`, naming the turns and the limit). | Shorten `length_mm` or use a coarser pitch: each turn slows every rebuild. |
| `create_thread` or `create_hole_wizard_hole` returns `not_supported` ("does not support", naming the SolidWorks revision). | The Thread feature, its `Metric Tap`/`Metric Die` library or `HoleWizard5` is missing; check File Locations > Thread Profiles, or sketch and cut the hole instead. |
| `create_hole_wizard_hole` refuses a point on a face that isn't flat, or a `side` the face doesn't point (`invalid_input`). | Move `face_point` onto a flat face, or leave `side` out. A failed hole leaves the model as it was. |
