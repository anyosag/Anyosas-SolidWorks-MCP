## Context

The fork already has the pieces this change builds on:
- `set_dimension` and `get_dimension` (`adapters/solidworks/io.py`) write and read `IDimension.SystemValue` and rebuild. `dimension_kind` maps `IDimension.GetType()` to length (mm), angle (degrees) or count, and `from_system_value` converts back. `_flip_mate` flips a distance or angle mate through `FlipDimension` and `ModifyDefinition`.
- `adapters/solidworks/thickness.py` reads every face's display tessellation (`IFace2.GetTessTriangles(True)`, flagged first) to sample points. It uses the numbers once and keeps none; this change keeps the triangles.
- `adapters/solidworks/placement.py` knows `IComponent2.Transform2`: `ArrayData` is 16 doubles, a 3×3 rotation as rows, the translation in metres, a scale. A point goes to `p · R + t` (probe 3 of change `cellvis-assembly-and-print-checks`).
- `adapters/solidworks/rebuild_inspector.py` returns each failing feature as a `RawRebuildIssue` with `type` (`GetTypeName2`, which starts with `Mate` for a mate), `code` and `is_warning`. `rebuild_diagnostics.py` describes broken, dangling and over-defined mates.
- `check_interference` (`io.py`), `measure_clearance` (`adapters/solidworks/clearance.py`, with a module function of that name) and `rotation_clearance` (`adapters/solidworks/rotation_clearance.py`).
- `adapters/solidworks/limits.py`: `check("doing what")` between the COM calls of a loop, which raises `OperationStopped` (`error_type: operation_stopped`).

`rotation_clearance` turns a component by writing `Transform2` and reading it back. Its module docstring records that a coincident mate did not hold the arm: nothing rebuilds the mates between steps. That suits a free hinge and is wrong for a joint whose motion the mates define. A mate dimension (an angle mate's angle, a distance mate's distance) is what a person changes to move such a joint, and `ForceRebuild3` then solves the mates.

hjbaard's `solidworks-mcp` (MIT, © 2026 Henk Jan Baard; `session.py`, `swept_region` at line 7214, `check_motion` at 7146, with `_face_triangles`, `_transform_data`, `_placement_into`, `_refuse_broken_mates`, `_write_dimension`, `_joint_angle` and `_dimension_unit`) is the reference for the COM order:
- refuse broken mates;
- read the dimension and its original `SystemValue`;
- for each value, write `SystemValue`, rebuild, read it back, and retry a write that is ignored;
- read each part's transform and place its triangles;
- restore in a `finally`;
- fail when nothing moved.

The COM code here is written fresh in the fork's adapter style. The one file taken nearly as written is the pure `region_tools.py` (209 lines: `swept_outline`, grid fill, grow, trace, Douglas-Peucker), ported as `utils/swept_region.py` with his MIT notice (D10).

Every COM call stays on the connect thread through `_handle_com_operation`, which also gives the settle pause. New dispatch objects get their methods flagged before use (CLAUDE.md, method flagging; pitfalls #41, #42).

**What the fork doesn't have, so it is new here:** a mate list or mate editor (the Cellvis session ranked it low), `IComponent2.GetChildren` (nothing in `src/` calls it), and any tool that steps a dimension through a range.

## Goals / Non-Goals

**Goals:**
- An agent gets the outline of what a component sweeps in a plane as a joint's dimension moves, ready to size a pocket or a keep-out sketch.
- An agent can check a joint's whole range for clashes and for the smallest gap to named neighbours, driving the mates the way a person would.
- A failed or stopped run leaves the dimension as it was found, and says if it could not.

**Non-Goals:**
- Dynamic simulation, motion studies, contact forces. The tools step a dimension and look at geometry.
- A mate editor or mate list. The agent gets the dimension's name from `list_model_dimensions`.
- Sweeps of a free component (no mate): `rotation_clearance` does that.
- Cutting a pocket from the outline. `sketch_from_face_loop` and `cut_offset_pocket` (change `face-outline-pocket`) are the next step; the outline's points are in the plane's own axes so a sketch can take them.
- Finding the clash's exact angle by bisection. `check_motion` reports the steps it was given. `rotation_clearance` bisects for a free turn.

## Decisions

### D1. `check_motion` is in this change

The two tools are the same loop with a different thing done at each step. Splitting them would duplicate the stepper (D2), its restore, its mate check and its tests, and the second change would reopen the first one's probes. So both go in, behind one `JointStepper` in `joint_sweep.py`:
- `swept_region` reads triangles once, and at each step places them and slices.
- `check_motion` at each step runs the interference check and the clearances.

`check_motion` is the smaller tool, and it is the one that closes the "Partial" gap in the tool map. If probe 1.3 shows a per-step interference check is too slow to be useful, `check_motion` keeps its place but its `distances` default to none and the description says so; it doesn't leave the change.

### D2. The stepper

One class, used as a context in the adapter's operation:
1. **Find the dimension** with `IModelDoc2.Parameter(name)`, as `get_dimension` does, and its kind through `io.dimension_kind`. A count is refused (`invalid_input`: a pattern's instances isn't a joint).
2. **Refuse broken mates** (D4), before anything moves.
3. **Remember** the original `SystemValue` and each moving candidate's transform.
4. **Per value:** convert from the tool's unit (mm or degrees) to `SystemValue` with `dimension_kind`'s factor; write; `ForceRebuild3(False)`; read back. If the read-back differs, write once more (hjbaard measured an ignored write 3 times in 80; probe 1.1 counts it here). If it still differs the run stops with `invalid_input` naming the value, what it read, and the likely cause: a reference (driven) dimension, an equation (change `parametric-equations` makes `set_dimension` name the equation; this reuses its check once merged), or a mate the solver won't move that far.
5. **Restore** in a `finally` (D5).

**Angle mates.** hjbaard writes `value % 360` for an angle mate, because SolidWorks took 0 to 360 there and not negative angles. This fork's `set_dimension` doesn't wrap. Probe 1.1 settles it: whether a negative or over-360 `SystemValue` is taken by an angle mate, how the dimension reads after `_flip_mate`, and which direction a rising value turns. If a negative value is refused, the stepper wraps into [0, 360) for a mate's angle, and the reply reports the values written next to the values asked. It never silently changes the request: `values` echoes what the agent gave, `written` what went to SolidWorks.

**Units** come from `dimension_kind`: degrees for an angle, mm for a length. The reply's `unit` says which. A length written as degrees (or the reverse) would be off by a factor of 1000 or 57.3, so the kind is read, not assumed.

### D3. Geometry: tessellate once, place per step

For `swept_region`:
- **Which parts.** The component, and if it is a sub-assembly, its solid descendants. `IComponent2.GetChildren` is new to the fork; probe 1.2 confirms it, and whether `Transform2` of a nested part is relative to the top assembly or to its parent. A suppressed or lightweight part is refused the way `clearance._solid_bodies` refuses it (name, reason, what to do); a part with no solid body is skipped, and a component with none at all is `invalid_input`.
- **Read once.** For each solid body (`GetBodies2`) and face (`GetFaces`), read `GetTessTriangles(True)` as `thickness.py` does (flagged first, metres, nine numbers per triangle) and keep the triangles in mm in the part's own coordinates. Probe 1.2 confirms the coordinates, and the cost on a 20-part sub-assembly.
- **Per step.** Read each part's `Transform2` and place its triangles: `p · R · s + t` in the assembly, with `R`, `t` and the scale `s` as `placement.py` reads them. Probe 1.2 checks the scale is 1 on real parts; if it isn't, where it applies. The result is mm in the assembly.
- **Cheap rejection.** A part whose placed box (the 8 corners of its stored box) misses every height at this step isn't sliced. A step then costs the parts that reach the plane, not all of them.

`limits.check("...")` goes:
- before each face's tessellation read (the COM part, one call per face);
- before each part is placed at each step, and before each slice (the Python part, which dominates once the triangles are read).

Probe 1.2 measures where the time goes and may move a check.

**Alternative rejected:** exporting a temporary STL of the assembly at each step (as `check_printability` does for a part). That needs a save per step, and loses which part each triangle came from. Reading the display tessellation once and moving it is one COM pass.

### D4. Broken mates are refused

A joint stepped with a broken mate lets parts drift: hjbaard saw a concentric mate on a hole that was made again hold nothing, and a part slide off its axis. The check:
- `ForceRebuild3(False)`, then `rebuild_inspector.collect_rebuild_issues`;
- any issue with `type` starting `Mate` and `is_warning` false is a broken mate, and the call fails with `invalid_input` naming it with `rebuild_diagnostics`' description and hint.

Warnings don't refuse. Probe 1.3 confirms a deleted face's mate comes through `GetWhatsWrong` as a `Mate...` feature with an error code (`rebuild_diagnostics` already lists codes 47, 48 and the over-defined case).

### D5. Always restore, and say whether it worked

- The restore is a `finally` around the loop: write the original `SystemValue`, `ForceRebuild3(False)`, read it back.
- `OperationStopped` from `limits.check` leaves the loop through the same `finally`, so a timeout or a client that went away restores too. The restore doesn't call `limits.check` (the budget is already spent).
- The reply carries `restored` (bool). If the read-back isn't the original, `restored` is false and the message, in a success and in a failure, says the original value and the `set_dimension` call that puts it back. A failure never hides it.
- **What a `finally` can't do:** the server process killed mid-step, or SolidWorks gone. Then the dimension stays where the last step left it. Probe 1.4 checks that, and whether `undo` (`IModelDoc2.EditUndo2`) steps back a whole run in one go or one rebuild at a time, which sets what the description tells an agent to do after an interrupted run.

### D6. The frame

`frame` names a component; omitted, it is the assembly. The outline's coordinates are those of the frame as it is at the start of the run. The frame's own transform is read once, before stepping, because a frame component that the joint also moves would make the outline meaningless. If the frame component moves during the run (its transform differs from the start by more than 1e-9 rotation or 1e-6 mm), the call fails with `invalid_input`, naming it. A point goes to the frame by `(p − t_f) · R_fᵀ / s_f`.

The plane's axes are the two axes other than `axis`, in x, y, z order: (x, y) across z, (y, z) across x, (x, z) across y. Change `mesh-sections` (its D3) defines `section`'s loop points as `(u, v)` in the same order: `(y, z)` for `x`, `(x, z)` for `y`, `(x, y)` for `z`, largest loop first, the first point not repeated. This change relies on that and re-checks it against the merged module (task 2.3).

### D7. The outline

`swept_outline(sections, tolerance_mm, margin_mm)` (ported, D10):
- Every section is filled on a grid of `tolerance_mm / 2` (outer loops and holes, even-odd). The grid rows are Python ints, one bit per cell.
- The union of all sections is one grid, grown by `margin_mm` as a disc, traced into loops of grid corners, and thinned with Douglas-Peucker to within 1.5 cells.
- Outer loops are counter-clockwise and holes clockwise. Each region lists its holes and its area.

A grid that is too large is refused up front: more than `MAX_GRID_CELLS` (25 million, a starting value for probe 1.2 to confirm) is `invalid_input`, naming the extent, the cell size and a coarser `tolerance_mm` that fits. Without it, a two-metre assembly at 0.2 mm would ask for a 20 000 × 20 000 grid.

hjbaard's note: a bar swept 120° gives 31 points at `tolerance_mm` 0.2 and 138 at 0.1. The reply's point count follows the tolerance, so the tool description says a coarser tolerance gives fewer points.

### D8. The adapter interface

New abstract methods on `SolidWorksAdapter` (`adapters/base.py`):

```python
async def swept_region(
    self, component: str, dimension: str, values: list[float],
    heights_mm: list[float], axis: str = "z", margin_mm: float = 0.0,
    tolerance_mm: float = 0.2, frame: str | None = None,
) -> AdapterResult[dict[str, Any]]: ...

async def check_motion(
    self, dimension: str, values: list[float],
    distances: list[list[str]] | None = None,
) -> AdapterResult[dict[str, Any]]: ...
```

- Pure helpers in `adapters/solidworks/joint_sweep.py`, unit-tested without COM: `place_triangles`, `into_frame`, `part_box_reaches`, `wrap_mate_angle`, `moved`, `smallest_per_pair`.
- `SolidWorksJointSweepMixin` holds the COM part and is added to `PyWin32Adapter`'s bases. `connection_pool.py` and `circuit_breaker.py` forward the two methods.
- **No nested operations.** `check_motion` runs inside one `_handle_com_operation`. It can't `await` `adapter.check_interference` there. So the body of `io.py`'s `check_interference` (`_check`) moves to a module function, `interference_report(adapter, coincident, include_multibody, ignore_hidden, wanted_names)`, which the adapter method and the stepper both call. `clearance.measure_clearance(adapter, a, b)` is already a module function. Task 2.4 does the refactor first, with the existing `check_interference` tests as the guard.
- `distances` pairs are instance names as `list_components` reports them; a name not in the assembly fails before anything moves (`not_found`, listing what the assembly holds), as hjbaard does. Each pair is also checked up front for the refusals `measure_clearance` makes (the same component twice, suppressed, no solid body), so a bad pair fails before the first write and not after it.

### D9. What `check_motion` reports

Per step: the value, `rebuild_ok`, `interferences` (the pairs and volumes `check_interference` gives; `coincident` false, so touching isn't a clash) and `distances` (per pair, `clearance_mm`, `touching` and the two points). The summary:
- `clash_free`: no step has an interference;
- `smallest_distances`: per pair, the smallest `clearance_mm` and `at` (the value, in the dimension's unit);
- `moving`: the components whose transform changed at some step, a part of a sub-assembly that moved as a whole counted as the sub-assembly.

**Cost.** `measure_clearance` is exact face-pair search; for 40-face parts it needs tens of measurements (cellvis D2), per pair, per step. The description tells agents to use few values and few pairs. Probe 1.3 times a step. The tool honours the operation timeout through `limits.check` in `clearance.nearest_pair` and between steps, and then restores (D5).

### D10. The ported module and the licence

`src/solidworks_mcp/utils/swept_region.py` is `region_tools.py` nearly as written:
- the functions `swept_outline`, `_fill`, `_grow`, `_bits`, `_trace`, `_turn`, `_take`, `_corners_only`, `_smooth`, `_douglas_peucker`, `_signed_area`, `_inside`;
- its import of hjbaard's `SolidWorksError` becomes `ValueError` (the pure modules here raise `ValueError`, and the adapter reports `invalid_input`);
- the header keeps "Copyright (c) 2026 Henk Jan Baard" and the MIT permission notice in full, then says where it came from and what was changed;
- the grid-size guard (D7) is the one addition.

`THIRD_PARTY_NOTICES.md` at the repo root doesn't exist yet. Task 6.1 creates it, or extends it if change `mesh-sections` (which ports hjbaard's `mesh_tools.py` the same way) has added it first. The fork's own LICENSE is "Copyright (c) 2025 ESPO Corporation".

### D11. The mock

`mock_adapter.py` has components as boxes (`component_boxes_mm`, placed by `_component_transforms`; `measure_clearance` mock). For joints it gets a table a test sets:
- `mock_joints[dimension] = {"component": name, "axis": [x, y, z], "pivot_mm": [x, y, z]}`;
- the component's placement is its start placement turned about the axis through the pivot by the dimension's value (degrees), right-hand rule.

Then:
- **`swept_region`** tessellates the component's box into 12 triangles, places them at each value, and runs the same pure slicing and `swept_outline`. The outline is real for a box, so the spec's scenarios can be checked on the mock.
- **`check_motion`** reports `distances` from the boxes (as `measure_clearance` mock does, with the axis-aligned box of the turned component, so a distance is a lower bound) and `interferences: []` with `clash_free: null` and a note, as the mock's `check_interference` does, since it has no geometry to answer from.
- **Refusals** are the real adapter's: not an assembly, no such dimension or component, a count dimension, nothing moved.
- A dimension that no joint entry drives: "nothing moved".

This mirrors payload shapes, and for a box the sweep's geometry.

### D12. Support levels and hints

- Both tools start `experimental` (`_UNCHECKED`) and move to `stable` when `test_live_sw_joint_sweep.py` passes on SolidWorks 2025 and 2026.
- Hints: `_sets` for both, as `rotation_clearance` has (they change the model while they run and put it back).
- Family: `analysis`, so `document` is taken automatically (`tools/target_document.py`).

## Risks / Trade-offs

- **Tessellation is a display mesh.** It is within the display deviation of the true surface, so a swept outline is too. The reply says so, and the margin and tolerance are the agent's to set. (`thickness.py` states the same limit.)
- **Cost.** Steps × parts × triangles, in Python. The box rejection (D3) and `limits.check` bound it, and a stop restores the joint. Probe 1.2 and 1.3 give numbers, and set whether `MAX` values and heights need caps (open question).
- **Mates that don't move the part.** Stepping a dimension that moves nothing (suppressed mate, a face it used is gone) is an error, not an empty result. The check compares transforms with `1e-9` for rotation and `1e-6` mm for the origin.
- **Angle conventions.** Zero and the turning direction depend on how the mate was made and on `flip`. The tools step the dimension, not a geometric angle; the reply reports the dimension's values and the live test shows the direction on a known joint.
- **Leaving the model moved.** Handled by D5; the one case it can't is a killed process (probe 1.4).
- **Sub-assemblies.** Nested transforms are the least certain COM fact here (probe 1.2). A joint inside a sub-assembly (the mate lives in the sub-assembly) is a non-goal until a probe shows its dimension can be found from the top.
- **Steps are positions, not a continuous sweep.** The outline is the union of the part at each value. If two neighbouring positions don't overlap, the union has gaps the real motion doesn't. A bar 10 mm wide and 100 mm long turned in 10° steps leaves gaps from 57 mm outward and loses about 16% of the area; 2° steps lose none. The tool description tells agents to keep the step under (width / reach) in radians, and the spec's bar scenario shows the arithmetic. A warning when consecutive sections don't overlap is an open question.
- **Tool count.** Two more tools while queue items 10 to 12 work to shrink the list. They stay in `analysis`.

## Probe results

To fill in after task group 1.

## Live results

To fill in after task group 7.

## Open Questions

- Should `swept_region` and `check_motion` cap `values` and `heights_mm` (say 200 and 20)? Probes 1.2 and 1.3 give the cost to decide.
- Should `swept_region` warn when two consecutive steps' sections don't overlap (a sign the steps are too coarse)? It would need the sections kept per step and one overlap test between neighbours.
- Should `check_motion` take `coarse` or `timeout_s` per pair, as `measure_clearance` does, for long ranges?
- Is `MAX_GRID_CELLS` = 25 million right? Probe 1.2 measures the time and memory of a large grid.
- Should the joint be found from a mate's name (`Angle1`) and not only from its dimension (`D1@Angle1`)? That needs a mate lookup the fork doesn't have yet.
- A joint in a sub-assembly: findable by `Parameter("D1@Angle1@Sub-1.SLDASM")`? Probe 1.1 can try it; if it works, it isn't a non-goal.
