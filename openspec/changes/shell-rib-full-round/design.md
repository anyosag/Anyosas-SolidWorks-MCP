## Context

The fork already has the pieces this change builds on:
- `FaceEdgeSelector` (`adapters/solidworks/face_selection.py`) finds a face by a point (`face_at_point`, within 0.01 mm), by a side (`pick_side_face`, the flat face furthest that way) or by a plane (`faces_on_plane`). `resolve(point, side, plane)` wraps the three.
- `IEntity.Select4` needs a real `SelectData`; `None` is a type mismatch (header of `face_selection.py`). `_select_face_at_point` in `features.py` shows the call with a mark.
- `create_cut_extrude` (`adapters/solidworks/features.py`) tries one side, reads the part's volume (`_part_volume`), deletes the cut (`_delete_feature_impl`), tries the other side, and keeps the one that removes more. It reports `direction_flipped` and `direction_choice`.
- `emboss_text` (`tools/modeling.py`) sketches through `create_sketch`, adds geometry and closes the sketch, then calls a feature method with `sketch_name`.
- `plane_frame.py` (`Frame`, `project`) turns a model point into sketch coordinates and refuses a point off the plane (`OffPlaneError`). It serves the `coordinates: "model"` input of the sketch tools.
- `measured_change` (`tools/measured-feature-change`) is attached by the tool: `_read_summary` before and after the feature, then `_attach_measured_change` in `tools/modeling.py`.
- `get_feature_definition` reads a fillet's `fillet_type`, and `FILLET_TYPES` in `inspection.py` maps 3 to `full_round`. It has no reader for a shell or a rib (`READERS`).
- `add_fillet` (`features.py`) calls `IModelDoc2.FeatureFillet3` (9 arguments) on SolidWorks 2025+ and `IFeatureManager.FeatureFillet3` (15 arguments) before that. The first returns a number, so the new feature is found in the tree afterwards.

hjbaard's `solidworks-mcp` (MIT, © 2026 Henk Jan Baard; `session.py`: `add_shell` 3533, `add_rib` 2045, `add_full_round` 3392, `_select_face_through` 653, `_side_faces`, `_full_round_sides`, `_rib_material_reversed`) shows a working order:
- **Shell:** select the faces to open, then `model.InsertFeatureShell(thickness_m, outward)`. It returns no feature; the volume is read afterwards and a zero volume is an error.
- **Rib:** sketch an open line, close the sketch (it stays selected), then `FeatureManager.InsertRib(True, False, thickness_m, 0, reverse, False, False, 0.0, False, False)`. The new feature is found in the tree as type `Rib`. If none appears, the line meets no material on that side.
- **Full round:** select three faces with marks (side 1 = 2, the top = 512, side 2 = 4), then `FeatureManager.FeatureFillet3(0, 0.0, 0.0, 0.0, 3, 0, 0, None, …)`. The two sides are the opposite flat faces along the top that lie closest together.

It is a reference only. Its selection has no `SelectData`, and it works on one body in Front-plane coordinates. This change is written fresh in the fork's adapter style, with the fork's face picking.

Every COM call stays on the connect thread through `_handle_com_operation`, which also gives the settle pause. New dispatch objects get their methods flagged before use (CLAUDE.md, method flagging; pitfalls #41, #42).

## Goals / Non-Goals

**Goals:**
- An agent can hollow a part, with some faces open or none, in one call.
- An agent can add a rib from a line on a plane, without having to know which side to fill.
- An agent can round a rib's or wall's top off completely, between its two sides.
- Each reply carries `measured_change`, so a feature SolidWorks ignored shows at once. A refused call leaves the part as it was.

**Non-Goals:**
- A shell with different thicknesses on some faces.
- A rib with draft, one-sided, or "to next" (natural) type. The rib is centred on its plane and parallel to the sketch.
- A full round on several ribs in one call, or from edges.
- Editing these features with `edit_feature`, and reading them with `get_feature_definition` (open questions).
- A sheet-metal rib or gusset (`swSheetMetalRibGussetType_e` is another feature).

## Decisions

### D1. Three tools, in `tools/modeling.py`

- Names are provisional: `create_shell`, `create_rib`, `add_full_round`. `create_` is for a feature made from a sketch or a selection and `add_` follows `add_fillet` and `add_chamfer`. Final names are an open question.
- Inputs are Pydantic models on `CompatInput`, as the other modeling tools.
- `modeling` is in `DOCUMENT_FAMILIES` (`tools/target_document.py`), so the three tools take `document` without extra code.
- Errors go through the tool boundary: `normalize_input`, `require_success`, `ToolOperationError`.

### D2. A face is `face_point` or `side`

- One shared input model, `FaceRef`: `face_point` (`[x, y, z]` mm) or `side` (`top`, `bottom`, `front`, `back`, `left`, `right`, `+X`…`-Z`), exactly one. The same two as `select_edges`; `plane` is left out, since a plane can hold several faces.
- The adapter takes a face as `{"point": [x, y, z]}` or `{"side": "+z"}` and resolves it with `FaceEdgeSelector.resolve`.
- A point that is on no face is `not_found` and names the point. A side with no flat face is `not_found`. Nothing is changed.
- `side` picks the furthest flat face that way. On a shelled part that is the outer skin; to reach an inner face (a pocket floor) use `face_point`.
- Probe 1.4 checks that a point on a shell's inner face resolves to that face, not the outer one.

### D3. `create_shell`: select the faces, then insert

- **Selection:** the open faces are selected with `Select4(append, SelectData)` after `ClearSelection2`. The first is not appended, the rest are. The mark value is settled by probe 1.1; hjbaard passes none.
- **Call:** hjbaard calls `IModelDoc2.InsertFeatureShell(thickness_m, outward)`. The fork's stopgap macro uses `IFeatureManager.FeatureShell2`, and nothing in the repo has run either on SolidWorks. Probe 1.1 settles which exists and works, and the order of its arguments, before the adapter is written.
- **Feature:** the call returns no feature on hjbaard's SolidWorks, so the new feature is the one the tree gained, by `GetTypeName2` (probe 1.1 records the name; hjbaard's `_NON_SEED_TYPES` lists `Shell`).
- **No open faces:** a closed hollow. Whether SolidWorks needs a selected body or face for that is probe 1.1. If it needs one, the adapter selects one face of the single solid body.
- **Several solid bodies:** with open faces, every face must be on one body, and that body is shelled; faces on two bodies are `invalid_input`. A closed hollow on a part with more than one solid body is `invalid_input` naming the bodies (`list_bodies`). A `body` input could remove that limit; it is an open question.
- **Failure:** the call fails when the feature tree didn't grow, or the volume didn't drop, or it is zero. The reply is `invalid_input` with the thickness and the part's smallest wall if it can be read. SolidWorks may leave a failed shell in the tree with an error flag; probe 1.1 checks, and the adapter deletes it if so.
- `thickness_mm` must be above 0 (`invalid_input`, before SolidWorks is called).
- Shell is best done before cutting openings. The tool description says so (hjbaard's finding).

### D4. `create_rib`: a sketch, then the rib

- **Sketch:** the tool follows `emboss_text`. It calls `create_sketch(plane)`, reads the sketch's frame, projects `start_mm` and `end_mm` with `plane_frame.project`, calls `add_line`, then `exit_sketch`. A point off the plane is `invalid_input` (`OffPlaneError`), naming how far off it is. A zero-length line is `invalid_input`.
- **Adapter method:** `create_rib(sketch_name, thickness_mm, reverse)` takes the sketch that was made, like `create_extrusion`. It selects the sketch and calls `IFeatureManager.InsertRib`. hjbaard passes `(True, False, thickness_m, 0, reverse, False, False, 0.0, False, False)`. Probe 1.2 names each argument on SolidWorks 2026, checks the two-sided flag (the rib is centred on the plane), and maps `swRibType_e` (linear, natural) and `swRibExtrusionDirection_e` (parallel, normal to sketch), which are in `data/swconst_enums.json`.
- **Feature:** found in the tree by type (`Rib` per hjbaard; probe 1.2 confirms). None new means the line meets no material on that side.
- **Failure cleanup:** on any failure the tool deletes the sketch it made, so the part is as it was. `emboss_text` leaves its sketch; a rib sketch with no rib is of no use.
- **Probe 1.2 also checks** a sketch on a plane made from a face (`create_reference_plane` with `face_point`), whose axes are not the world's.

### D5. Which side a rib fills

- **With `toward_mm`:** the side is read from the point. A pure helper `rib_reversed(start, end, toward)` works in the sketch's 2D coordinates: the sign of the cross product of the line and the vector to the point. It refuses a point on the line (`invalid_input`). Which sign means `reverse=True` is probe 1.2 (hjbaard found the rib grows to the right of start→end, seen from +Z). No second try is made.
- **Without `toward_mm`:** the choice follows `create_cut_extrude`.
  - Make the rib one way, read the part's volume, delete it, make it the other way, read again, keep the one that added more.
  - A side that builds nothing has no material. If neither side builds, the tool fails with `invalid_input`: the line doesn't meet the part.
  - `direction_flipped` is true when the kept side is not the default; `direction_choice` says how it was chosen (`requested`, `more_material`, `only_side_with_material`, `equal_material`), as for the cut.
  - Equal volumes keep the default, with a `warning`: the agent should give `toward_mm`.
- The two builds are two rebuilds. Giving `toward_mm` costs one.

### D6. `add_full_round`: three face sets, one call

- **Faces:** `top` is resolved by D2. The sides are `side_faces` (two `FaceRef`s) when given. Otherwise they are the flat faces along `top`'s edges that stand square to it (`GetTwoAdjacentFaces2` on each edge of `top`, planar, normal perpendicular to `top`'s), each once. This is hjbaard's `_side_faces`. The fork identifies a face once with `IModelDocExtension.GetPersistReference3`, as `persist_ids.py` does for edges; probe 1.3 checks it on faces.
- **Pure helper `full_round_sides(sides)`:** from `[(normal, point on it)]`, the opposite pair (normals opposite within 1e-6) that lies closest together: `(i, j, distance)`. No pair is `invalid_input`: a full round needs two opposite flat faces along the top. Two pairs equally far apart (within 1e-6 mm) is `invalid_input`: it can't tell which way to run, and the message says to give `side_faces`.
- **Width:** the distance between the sides. The radius is half of it, so the reply says `width_mm` and no radius.
- **Call:** `FeatureFillet3` with `swFeatureFilletType_FullRound` (3). Selection: side 1, the top and side 2, each with a `SelectData` mark. hjbaard's marks are 2, 512 and 4; the fork has no constant for them, so probe 1.3 checks them against SolidWorks 2026 and records where they come from.
- **Which `FeatureFillet3`:** the 9-argument `IModelDoc2` form has no slot for face sets. The adapter uses the `IFeatureManager` form (15 arguments, returns a feature) if probe 1.3 shows it builds a full round on SolidWorks 2026. If it returns a number, the feature is found by the tree, as `add_fillet` does.
- **Failure:** `None` or no new feature is `invalid_input` naming the top face, with the width. The selection is cleared either way.
- `get_feature_definition` on the result gives `fillet_type: "full_round"` (existing reader). Probe 1.3 checks it.

### D7. The adapter interface

New abstract methods on `SolidWorksAdapter` (`adapters/base.py`):

```python
async def create_shell(
    self, thickness_mm: float, open_faces: list[dict[str, Any]], outward: bool = False
) -> AdapterResult[dict[str, Any]]: ...
async def create_rib(
    self, sketch_name: str, thickness_mm: float, reverse: bool | None = None
) -> AdapterResult[dict[str, Any]]: ...
async def add_full_round(
    self, top: dict[str, Any], side_faces: list[dict[str, Any]] | None = None
) -> AdapterResult[dict[str, Any]]: ...
```

- A face is `{"point": [x, y, z]}` or `{"side": "+z"}`.
- `reverse=None` for `create_rib` means "try both" (D5).
- Each result's `data` holds `feature` (`name`, `type`) and the settings used. `measured_change` is added by the tool, as for the other solid features, not by the adapter.
- Pure helpers, unit-tested without COM: in `shell_rib.py`, `rib_reversed`; in `full_round.py`, `full_round_sides` and a face-reference validator shared with the mock.
- `SolidWorksShellRibMixin` and `SolidWorksFullRoundMixin` are added to `PyWin32Adapter`'s bases. `connection_pool.py` and `circuit_breaker.py` forward the three methods.
- `create_rib`'s choice between sides reuses `_part_volume` and `_delete_feature_impl` from `features.py`. They are private to that module today; the task either imports them or moves them to a shared helper.

### D8. The mock

`mock_adapter.py` mirrors the payloads, not SolidWorks' geometry:
- **Shell:** validates thickness and faces like the real adapter. A face point is checked with `_mock_face_at` (the mock's 10 mm block), so a point on no face is `not_found`. A thickness of 5 mm or more (half the block) is refused. It records a `Shell` feature.
- **Rib:** records a `Rib` feature on the given sketch; `direction_flipped` is false.
- **Full round:** records a `Fillet` feature with type full round; `width_mm` is null with a note, since the mock block has no ribs. Two sides that aren't two faces are refused as the real adapter does.
- `measured_change` holds null values and a note, as for other mock features.

### D9. Support levels and hints

- All three tools start `experimental` (`_UNCHECKED` in `support_levels.py`) and move to `stable` when `test_live_sw_shell_rib_full_round.py` passes on SolidWorks 2025 and 2026.
- Hints (`tool_hints.py`): `_adds` for each (a second call adds a second feature).
- Family: `modeling`.

### D10. Capabilities

- `tools/shell`, `tools/rib` and `tools/full-round` are new.
- `tools/edge-treatment` is not modified: its requirements are about `edge_names` entries. A full round selects faces and has no edge entries, so adding it there would stretch the capability's purpose. If a later change decides full round belongs in `add_fillet`, it moves.

### D11. `generate_vba_part_modeling`

- Its description drops "which no direct tool makes yet" and says `create_shell` makes a shell directly, keeping the macro as a fallback.
- Its "shell" macro uses `FeatureShell2`. If probe 1.1 shows that member doesn't exist, the macro is fixed to the call that works, or the shell operation is removed. That is an open question for the user, since the tool is offline and stable.

## Risks / Trade-offs

- **The call signatures are unverified.** Nothing here has run on SolidWorks 2026. `InsertFeatureShell`, `InsertRib` (ten arguments) and `FeatureFillet3` for a full round are known only from hjbaard. Mitigation: probe group 1 runs before any adapter code. The design's decisions that depend on a probe say so.
- **Marks.** A wrong mark on a full round's faces fails silently or builds the wrong fillet. Mitigation: probe 1.3, and live scenarios whose volumes tell a right round from a wrong one.
- **A silent no-op.** `InsertFeatureShell` returns nothing, and `FeatureFillet3` may return success and build nothing (as for a face in `add_fillet`). Mitigation: the tree count and the volume are checked, and `measured_change` is in the reply.
- **Rib sides cost a rebuild.** Without `toward_mm`, two builds and a delete. A slow model could pass `SOLIDWORKS_MCP_OPERATION_TIMEOUT_SECONDS`. `toward_mm` avoids it, and the description says so.
- **Full round ties.** A square post has two equal pairs of sides. The tool refuses rather than guess, and `side_faces` resolves it.
- **Tool count.** Three more tools while queue items 10–12 work to shrink the list. They belong to `modeling`, which stays a core family.

## Probe results

To fill in after task group 1.

## Live results

To fill in after task group 6.

## Open Questions

- Final tool names (`create_shell` / `add_shell`, `create_rib` / `add_rib`, `add_full_round`)?
- Should `create_shell` take a `body` for a closed hollow on a multibody part (D3)? It depends on probe 1.1.
- Should `edit_feature` learn a shell's thickness and a rib's thickness? And `get_feature_definition` a shell and a rib reader (`READERS` in `inspection.py`)? Probe 1.5 finds the data interfaces; the change could take them or leave them.
- Should `create_rib` accept an existing `sketch_name` instead of `plane` and a line, so a rib can use a sketch with several segments?
- Should the stopgap shell macro in `generate_vba_part_modeling` be kept once `create_shell` exists (D11)?
