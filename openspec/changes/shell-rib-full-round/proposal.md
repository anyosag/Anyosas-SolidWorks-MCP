## Why

Three common solid features can't be made with a tool today:

- **Shell.** Nothing hollows a part. `generate_vba_part_modeling` writes a VBA macro for one, and `execute_macro` can't run it (it runs only binary `.swp` files). The macro's `FeatureShell2` call is also unchecked against SolidWorks.
- **Rib.** Nothing adds a gusset or stiffener. An agent can only sketch and extrude one by hand, and that doesn't stop at the part's walls the way SolidWorks' rib does.
- **Full round.** `add_fillet` makes a constant-radius fillet on edges. It can't round the top of a rib or wall off completely, where the radius is half the width and follows it. SolidWorks makes this fillet type from three faces, not edges. `get_feature_definition` already reads one back as `full_round` (`adapters/solidworks/inspection.py`), but nothing here can make one.

The comparison with hjbaard's `solidworks-mcp` (v0.21.0) found `add_shell`, `add_rib` and `add_full_round` there and none here. This change brings them in, built the fork's way. No GitHub issue: issues are off on the fork `anyosag/SolidworksMCP-python`, and upstream has none for these.

## What Changes

- **New tool `create_shell`:**
  - Takes `thickness_mm`, `open_faces` (a list of faces to remove; empty makes a closed hollow) and `outward` (the wall grows outward instead of inward).
  - A face is named by `face_point` (a point on it, mm) or `side` (`top`, `+Z`, …), as `select_edges` does.
  - Returns the feature, the settings used and `measured_change`.
  - Refuses a thickness SolidWorks can't build, and leaves the part as it was.
- **New tool `create_rib`:**
  - Takes `plane`, a line `start_mm` / `end_mm` (model points on that plane), `thickness_mm`, and optionally `toward_mm`, a point on the side to fill.
  - It sketches the line, then makes the rib centred on the plane, grown until it meets the part.
  - Without `toward_mm` it tries both sides and keeps the one that adds more material, and says so in `direction_flipped`, as `create_cut_extrude` does.
  - Returns the feature, the sketch, `direction_flipped` and `measured_change`.
- **New tool `add_full_round`:**
  - Takes `top` (the face to round off, by `face_point` or `side`) and optionally `side_faces`, the two faces it runs between.
  - Without `side_faces` it takes the two opposite flat faces along `top` that lie closest together, and refuses a tie.
  - Returns the feature, `width_mm` (the two faces' distance; the radius is half of it) and `measured_change`.
- **`generate_vba_part_modeling`'s description changes.** It no longer says no direct tool makes a shell. The "shell" macro stays; the description points to `create_shell`.
- **Not in this change:** a rib's draft, one-sided or "to next" ribs, a shell with several thicknesses, a full round on more than one rib at once, and editing these features with `edit_feature`. They are in design.md's Open Questions.

## Capabilities

### New Capabilities
- `tools/shell`: hollowing a part, with open faces or none.
- `tools/rib`: a rib from a sketched line on a plane.
- `tools/full-round`: a full-round fillet across a face, between two side faces.

### Modified Capabilities
None. `tools/edge-treatment` is about what an `edge_names` entry treats (`add_chamfer`, `add_fillet`). A full round takes faces, not edges, so it gets its own capability.

## Impact

- **COM/adapter code (extra risk per CLAUDE.md):**
  - New `adapters/solidworks/shell_rib.py` (`SolidWorksShellRibMixin`): shell through `InsertFeatureShell`, rib through `IFeatureManager.InsertRib`.
  - New `adapters/solidworks/full_round.py` (`SolidWorksFullRoundMixin`): `FeatureFillet3` with the full-round type (`swFeatureFilletType_FullRound = 3`, in `data/swconst_enums.json`), with the three face sets selected through `ISelectData` marks.
  - Face selection needs a real `SelectData`, not `None` (`face_selection.py` header). Every new dispatch is method-flagged before use (pitfalls #41, #42).
  - `FeatureFillet3` has two forms on different SolidWorks versions (`IModelDoc2`, 9 arguments, returns a number; `IFeatureManager`, 15 arguments, returns a feature; see `add_fillet` in `features.py`). A probe settles which one builds a full round on SolidWorks 2026.
  - Abstract methods in `adapters/base.py`, forwarded by `connection_pool.py` and `circuit_breaker.py`; `pywin32_adapter.py` adds both mixins.
  - `mock_adapter.py` gains the three methods.
- **Tools:**
  - `tools/modeling.py` (beside `create_cut_extrude` and `add_fillet`): three inputs and tools.
  - `support_levels.py` (all three experimental until the live test passes), `tool_hints.py`, `error_classifier.py` only if a refusal doesn't already classify.
  - `tools/vba_generation.py`: the description of `generate_vba_part_modeling`.
  - The tool catalog and progress files; the agent skill's `references/workflows.md` and `references/vba-fallback.md` (which names the shell macro as the way to make a shell); `docs/user-guide/tool-catalog/vba-generation.md`.
- **Docs:** pitfall entries for what the probes find (the shell and rib call signatures, the full-round marks, the `FeatureFillet3` form).
- **Tests:**
  - unit tests beside each module;
  - a probe script `tests/scripts/probe_shell_rib_full_round.py` (scratch documents);
  - a live test `tests/live/test_live_sw_shell_rib_full_round.py`.
- **Credit:** hjbaard's `add_shell`, `add_rib` and `add_full_round` (MIT, © 2026 Henk Jan Baard; `session.py`) are the reference for the call order and arguments. The code is written fresh, so no copied code needs a notice; `design.md` credits it.
