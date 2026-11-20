## Context

The fork already has the pieces this change builds on:
- `FaceEdgeSelector` (`adapters/solidworks/face_selection.py`) finds a flat face by point (`face_at_point`, within 0.01 mm) or by side (`pick_side_face`, the furthest flat face facing a direction), and gives each face's `IFace2` handle. `validate_face_query` is the shared input check for `select_face_edges`.
- `create_reference_plane` with `face_point` (`adapters/solidworks/features.py`, `_select_face_at_point`) makes a plane on a flat face. `emboss_text` uses it to sketch on a face, then reads where the sketch's axes point (`plane_axes`, `sketch_point`).
- `create_cut_extrude` (`adapters/solidworks/features.py`, `tools/modeling.py`) cuts from a named sketch: it picks the side that removes material, recovers when a set side finds none, and explains a cut that builds nothing (`tools/cut-extrude`).
- `check_sketch_fully_defined` (`adapters/solidworks/sketch.py`) reads whether a sketch is fully constrained.
- `measured_change` (`tools/measured-feature-change`) reports volume, box and body count before and after a feature.
- `create_extrusion` already has `thin_feature`, `thin_thickness` and `thin_side`.

What is missing is any read of a face's loops and any way to offset or project them. `GetLoops`, `IsOuter`, `SketchUseEdge3` and `SketchOffsetEntities2` are not used anywhere in the source. `sketch_offset` calls `SketchOffset2` on entity ids that earlier `add_*` calls registered, so it can't take a model edge.

hjbaard's `solidworks-mcp` (MIT, © 2026 Henk Jan Baard; `session.py`: `cut_offset_pocket`, `_offset_outline`, `_open_face_sketch`, `_select_face_through`) shows a working order on SolidWorks 2025:
- select the face, open a sketch on it (`InsertSketch(True)` with the face selected), and keep it closed in a `finally`;
- take `IFace2.GetLoops()`, find the loop whose `IsOuter()` is true, and select every edge from its `GetEdges()` with `IEntity.Select4`;
- call `SketchOffsetEntities2(-rim, False, True)`, and note that a negative offset runs inward from a face's outer loop;
- read `GetConstrainedStatus()` (3, `swFullyConstrained` in `data/swconst_enums.json`);
- name the offset's first dimension `rim`, close the sketch, and cut with `FeatureCut4`.

Its pocket offsets the outer loop only. A hole in the face is simply inside the offset region, so the cut passes through it and leaves no rim around it. The tool here goes further: it keeps a rim around each hole (D4). That behaviour is not proven by hjbaard's code, so it is a probe (1.3). This change is written fresh in the fork's adapter style, with hjbaard's call order as the reference.

Every COM call stays on the connect thread through `_handle_com_operation`. New dispatch objects get their methods flagged before use (CLAUDE.md, method flagging; pitfalls #41, #42).

## Goals / Non-Goals

**Goals:**
- An agent pockets a flat face with a rim that follows its outline (lines, arcs, splines) in one call, through or to a depth.
- A hole in the face keeps a rim around it.
- The rim and the depth are dimensions the agent can change with `set_dimension`, and the pocket follows the face when the face changes.
- An agent projects a face's loops into a sketch as linked references, for a thin wall (`create_extrusion` with `thin_feature`), an offset, or a cut of its own.
- A failure leaves no sketch or feature behind.

**Non-Goals:**
- Non-flat faces. A sketch needs a plane.
- A different rim per loop, or a rim that varies along the outline. One `rim_mm` for the whole outline (Open Questions).
- Offsetting outward (a boss that grows beyond the face). `sketch_from_face_loop` then `sketch_offset` or `create_extrusion` serves that.
- A pocket on a face of a component in an assembly. Part documents only, as `select_face_edges`.
- Isolating one inner loop by pick. `loops` takes `all`, `outer` or `inner` (D11).

## Decisions

### D1. Two adapter methods; the tool calls the cut path

- `sketch_from_face_loop` (adapter) opens a sketch on the face and projects loops into it with `SketchUseEdge3`.
- `sketch_face_offset` (adapter) opens a sketch on the face and offsets the loops with `SketchOffsetEntities2`.
- Both share the face pick, the loop walk and the sketch host (D2, D3) as helpers in `face_sketch.py`.
- The `cut_offset_pocket` tool calls `sketch_face_offset`, then `adapter.create_cut_extrude(ExtrusionParameters(sketch_name=…, end_condition="ThroughAll" or "Blind", depth=…, reverse_direction=False))`. It does not call `FeatureCut4` itself. Side choice, recovery and the "built nothing" message come from the cut path, so `tools/cut-extrude` needs no change.
- The tool follows `emboss_text`'s shape: it orchestrates adapter calls, with no `try/except`, using `require_success`.
- `reverse_direction=False` cuts against the sketch normal, which for a sketch on a face is into the part (`tools/cut-extrude`). If the sketch's normal points the other way, the cut path's recovery cuts the other way and the reply says `direction_flipped`.

### D2. Sketch host: the face itself, or a plane made on it

- Preferred: select the face and call `InsertSketch(True)`, as hjbaard does. It adds no feature to the tree.
- Fallback: make a plane with `create_reference_plane(face_point=…)` and sketch on that, as `emboss_text` does. The reply then has `plane_created`, and the tree has one more plane.
- Probe 1.1 settles which works through late binding on SolidWorks 2025 and 2026, and whether the offset or projection stays linked to the face's edges from a sketch on a plane (D6).
- Either way the reply's `sketch` block has `name`, `plane` (the face described, or the plane's name), `axes` (from `plane_axes` when the host is a plane; read off the sketch otherwise) and `plane_created` (name or null).

### D3. Picking the face and reading its loops

- The face is given by exactly one of `face_point` or `side`, as `select_face_edges` takes `point` or `side`. The two are exclusive, and neither alone is `invalid_input`.
- It is found with `FaceEdgeSelector.face_at_point` or `pick_side_face`. A point on no face is `not_found`. A face that isn't flat is `invalid_input`: the message says a sketch needs a flat face.
- A new pure helper `validate_face_pick(face_point, side)` in `face_selection.py` checks the pair. It is `validate_face_query` without `plane`, so its message doesn't name an input these tools don't take.
- Loops come from `IFace2.GetLoops()`; each `ILoop2` gives `IsOuter()` and `GetEdges()`. `GetLoops` and `IsOuter` take no arguments, so they are flagged.
- A flat face has one outer loop and any number of inner loops. A face with no outer loop is an error: `not_found`, naming what the face has.
- Probe 1.2 reads a face with a hole, with a rounded outline and with a spline outline. It records the count and kind of edges, how a full circle appears (one edge, no vertices; face_selection's header notes this), and whether the order of `GetLoops` is stable.

### D4. Offset direction: outer loop inward, hole loops away from the hole

- The rim is material left standing. So the outer loop moves inward, and each inner loop moves outward from its hole, into the material. Hole radius 5, rim 5 gives an offset circle of radius 10.
- hjbaard verified only the outer loop: a negative `SketchOffsetEntities2` offset runs inward. For an inner loop the sign is a hypothesis: positive grows a closed contour, so it moves away from the hole.
- It can't be one call. `SketchOffsetEntities2` takes one signed distance for what is selected, and the outer loop and the hole loops need opposite signs. The adapter makes one call for the outer loop, then one for all inner loops together.
- Probe 1.3 settles the sign for each, whether the second call can be made in the same open sketch with the first offset's entities left behind, and how many dimensions result (D7).
- If the probe shows the hole offset can't be made this way, the pocket still runs to the hole's edge, as hjbaard's does, and the tool says so in `warning`. The spec's hole scenario then needs a decision before implementation (Open Questions).

### D5. Splines and arcs: passed to SolidWorks, not interpreted

- The server selects every edge of a loop and lets `SketchOffsetEntities2` (with `Chain` true) handle lines, arcs, circles and splines. It does not read curve types to compute anything.
- When SolidWorks refuses (the call returns false), the tool fails with `invalid_input`: "SolidWorks could not offset the outline by 20 mm. Is the rim wider than half the face, or tighter than a curve in the outline?" The face described in the message is the one picked.
- Probe 1.4 finds what happens on:
  - a rounded rectangle (convex arcs: the offset arc's radius is the original minus the rim);
  - a reflex corner (an L shape: does SolidWorks join with an arc or a mitre?);
  - a concave arc tighter than the rim;
  - a spline outline;
  - a rim that is half the face's width exactly.
- It also records whether a refused offset leaves a half-made sketch to remove (D10). The spec asserts numbers only for the cases that have a hand calculation (rectangles, convex arcs).

### D6. Linked after an upstream change: required, proven by a probe

- The point of offsetting the face's own edges, not copies of their coordinates, is that the pocket follows when the face changes.
- Probe 1.5 builds the pocket, then runs `set_dimension` on the plate's length (60 to 80), rebuilds, and measures the pocket again. It repeats with `edit_sketch` on the plate's sketch.
- The spec requires the link. If the probe shows it doesn't hold from the sketch host in D2, the host changes. If it doesn't hold at all, the requirement is rewritten before implementation and the tool's description says the pocket does not follow.

### D7. Rim and depth as dimensions

- The offset adds a driving dimension. hjbaard renames the first one `rim` (`IDimension.Name`), which gives `set_dimension` a stable name (`rim@Sketch2`).
- The reply returns `rim_dimensions`: the names `set_dimension` takes, the outer loop's first, then any hole offsets'. It returns `depth_dimension`: the cut's depth dimension (`D1@<cut name>`), or null for a through cut.
- Probe 1.3 confirms that `Name` can be set through late binding, that `set_dimension("rim@Sketch2", 8)` changes the offset and rebuilds, and whether the hole offsets get a dimension of their own. If they do, whether an Equal relation or an equation (queue item 18's `set_equations`) can tie them to the outer one so a single change moves all. That is an Open Question; the spec asserts only that the names are returned and work.
- The names come from the dimensions as read back, not assumed.

### D8. Fully defined

- After the sketch is closed, the tool calls `check_sketch_fully_defined(sketch_name)` and puts `fully_defined` in the reply. It does not read `GetConstrainedStatus` itself.
- A sketch that isn't fully defined doesn't fail the tool; the reply carries a `warning`, because an under-defined offset can shift when the model rebuilds.

### D9. Through or to a depth

- `depth_mm` left out: `end_condition="ThroughAll"`, one way. Given: `Blind` with `depth=depth_mm`. A `depth_mm` of 0 or less is `invalid_input` (Pydantic `gt=0`). `rim_mm` is `gt=0` too.
- `ThroughAll` ignores `depth` in the cut path; `ExtrusionParameters.depth` is required, so the tool passes 1.0 as a placeholder. Task 5.1's test asserts it isn't used.

### D10. A failure leaves nothing behind

- The sketch is closed in a `finally` before anything else, so a failed offset never leaves a sketch open (hjbaard's lesson; pitfall #45: an empty sketch is deleted on exit).
- If the offset fails, the sketch the tool opened (and the plane, when D2's fallback made one) is removed with `delete_feature`.
- If the cut fails after the sketch is made, the tool removes the sketch and any plane it made, then re-raises the cut path's error. The feature tree is as it was.
- If a removal fails, the reply's error says which feature is left over.

### D11. What `sketch_from_face_loop` takes and returns

- Input: the face (D3), `loops` (`all` default, `outer` or `inner`), `as_construction` (default false), `keep_open` (default false).
- The issue's `include_inner_loops: bool = True` is `loops`: `all` is true, `outer` is false. One input covers it plus the inner-only case that a pocket's cut-through of a hole needs. The issue's `face_entity` (an entity reference) isn't used; the fork has no such references.
- Inner loops are chosen by `ILoop2.IsOuter()` being false. `inner` on a face with no holes is `not_found` and lists the loops the face has.
- It selects the edges of the chosen loops and calls `SketchUseEdge3` with `Chain` false and `InnerLoops` false, so the loops come from the selection and not from the face. Probe 1.7 confirms the argument list (also through the fork's `lookup_api_method`) and compares it with selecting the face itself and letting `InnerLoops` pick.
- It returns:
  - `sketch` (`name`, `plane`, `axes`, `plane_created`, `open`);
  - `face` (`center_mm`, `normal`, `area_mm2`, as `FaceInfo.describe`);
  - `loops`: one per chosen loop, with `index`, `outer`, `edge_count` and `curves` (counts of `line`, `arc`, `circle`, `spline`, `other`);
  - `entities_added` (segments in the sketch afterwards) and `construction`.
- `as_construction`: probe 1.7 finds whether `SketchUseEdge3` has no construction option and the segments must be flagged one by one (`ConstructionGeometry`).
- `keep_open` leaves the sketch active, so an agent can add geometry with `add_line` and close it with `exit_sketch`. Default is to close it, so the sketch is ready for `create_extrusion` or `create_cut_extrude` by name.
- The projected segments are not entered in the adapter's sketch-entity registry, so `sketch_offset` and the other `add_*`/`sketch_*` tools that take entity ids can't address them. The agent offsets a face loop with `cut_offset_pocket`, or in SolidWorks. Registering them is an Open Question.

### D12. The adapter interface

New abstract methods on `SolidWorksAdapter` (`adapters/base.py`):

```python
async def sketch_from_face_loop(
    self, face_point: list[float] | None, side: str | None,
    loops: str, as_construction: bool, keep_open: bool,
) -> AdapterResult[dict[str, Any]]: ...

async def sketch_face_offset(
    self, face_point: list[float] | None, side: str | None, rim_mm: float,
) -> AdapterResult[dict[str, Any]]: ...
```

- `sketch_face_offset` returns `sketch_name`, `sketch` (the block of D2), `face`, `loops` (as D11), `rim_dimensions` and `fully_defined` is added by the tool (D8). It always closes the sketch.
- Pure helpers in `adapters/solidworks/face_sketch.py`, unit-tested without COM: `validate_face_pick(face_point, side)` (in `face_selection.py`), `describe_loop(edges) -> dict` (counts by curve kind), `offset_signs(loops, rim_mm) -> list[tuple[loop, float]]` (outer: −rim, inner: +rim, per D4), `rim_too_wide_message(...)`.
- `SolidWorksFaceSketchMixin` holds the COM part, with every call through `_handle_com_operation`, and is added to `PyWin32Adapter`'s bases.
- `connection_pool.py` and `circuit_breaker.py` forward both methods.

### D13. The mock

`mock_adapter.py` has a canned 10 mm block whose +Z/-Z faces carry a hole once a circle has been sketched (`select_face_edges`' mock). The two mock methods mirror the payloads on that block:
- **Face pick:** the same `validate_face_pick` and the same `not_found` as `select_face_edges`.
- **Loops:** a square outer loop of 4 lines, plus one circle on the ±Z faces when a circle was sketched.
- **Offset:** a rim of 5 mm or more on the 10 mm face is refused as too wide (the same message as the real tool, rim ≥ half the face). Otherwise a sketch name is returned, `rim_dimensions` has one name per loop, `fully_defined` is true.
- **`measured_change`:** null values and a note, as for other mock features. The cut is the mock's `create_cut_extrude`.
- This mirrors payload shapes, not SolidWorks' offset.

### D14. Support levels and hints

- Both tools start `experimental` (`_UNCHECKED`) and move to `stable` when `test_live_sw_face_outline.py` passes on SolidWorks 2025 and 2026.
- Hints (`tool_hints.py`): `cut_offset_pocket` is `_adds("Cut an Offset Pocket")`; `sketch_from_face_loop` is `_adds("Sketch from Face Loops")`. Each call adds a feature or sketch.
- Families: `modeling` and `sketching`. Both are in `DOCUMENT_FAMILIES`, so `document` is accepted (`tools/target_document.py`).
- Descriptions follow `docs/agents/tool-description-standard.md`. `cut_offset_pocket` says "for a pocket you draw yourself, use `create_cut_extrude`" and `sketch_from_face_loop` says "to cut a pocket with a rim, use `cut_offset_pocket`", as queue item 1 asks for overlapping tools.

## Risks / Trade-offs

- **Zero-argument members as properties.** `GetLoops`, `IsOuter`, `GetEdges` and `GetConstrainedStatus` resolve as properties through forced late binding. They are flagged first, and the probe records any that fail anyway.
- **Selection state.** The offset and the projection both work on the current selection. A leftover selection or a missed `ClearSelection2` adds an edge the agent didn't ask for. The adapter clears the selection first, and a count check (selected edges equals the loop's edge count) fails the call before `SketchOffsetEntities2` runs.
- **A sketch left open.** A rejected offset can leave the sketch active, and the next `InsertSketch` then closes it instead of opening one (hjbaard's note). D10's `finally` closes it.
- **The link may not survive.** D6. If probe 1.5 fails, the tool's headline benefit goes, and the requirement and description change before anything is built.
- **Hole offsets.** D4's sign for inner loops is a hypothesis, and one signed call per loop kind means two dimensions (D7). Neither is proven by hjbaard's code.
- **Reflex corners and tight curves.** An inward offset at a reflex corner needs an arc, and a curve tighter than the rim can't be offset. SolidWorks decides; the spec asserts numbers only for rectangles and convex arcs.
- **Tool count.** Two more tools while queue items 10–12 work to shrink the list. They fit the `modeling` and `sketching` families, which stay core.

## Probe results

To fill in after task group 1.

## Live results

To fill in after task group 7.

## Open Questions

- Should hole rims be tied to the outer rim (an Equal relation, or an equation), so one `set_dimension` moves all? Or should `cut_offset_pocket` take a separate `hole_rim_mm`? Both depend on probe 1.3.
- Should the segments `sketch_from_face_loop` adds be entered in the sketch-entity registry, so `sketch_offset`, `add_sketch_dimension` and friends can address them?
- Should `loops: "inner"` take a point to pick one hole, for a face with many? (D11 takes all inner loops or none.)
- Should `cut_offset_pocket` work on a face of an assembly component? It needs the component's context for the edge selection, and `select_face_edges` has a `component_faces` path to start from.
- Should the thin-wall pair from #77 get a one-call wrapper (a `thin_wall_from_face` tool)? The pair already works, so it waits for a request.
