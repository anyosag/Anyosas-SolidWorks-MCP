## Context

The fork already has the pieces this change builds on:
- `FaceEdgeSelector` (`adapters/solidworks/face_selection.py`) finds the face under a `face_point` (`face_at_point`, within 0.01 mm) and knows each face's normal and whether it is flat. `side_vector` turns `side` into a direction.
- `geometry_query.py` already fires `IModelDocExtension.SelectByRay` from a point, one pick at a time. Pitfall #59 records that `SelectByRay` with `Append` false adds to the selection, so each pick must be read and cleared.
- `edge_index.py` (`PartIndex`) reads a part's bodies, faces and edges once and keeps them until the next rebuild.
- `select_edges`, `select_face_edges` and `query_geometry` find circular edges and describe them (`describe_edge` gives a circle's centre and radius).
- `check_sketch_fully_defined` reports a sketch's state; `set_dimension` writes a dimension by name.
- `measured_change` (`tools/measured-feature-change`) reports volume, box and body count before and after a feature. `emboss_text` and `create_cut_extrude` attach it.
- `delete_feature` removes a feature with its dependents; `rebuild_inspector.py` names failing features.
- `limits.check` (`adapters/solidworks/limits.py`) stops a loop when the operation's time budget runs out.
- `lookup_api_method` and `get_api_remarks` read the installed type library's signatures and help text. The implementer uses them to check `HoleWizard5` and `IThreadFeatureData` on the installed SolidWorks.
- `data/swconst_enums.json` holds the swconst enums of SOLIDWORKS 2026 SP4.1. Every enum value this design uses was read from it (D4).

hjbaard's `solidworks-mcp` (MIT, © 2026 Henk Jan Baard; `session.py`, `add_hole_wizard` at line 2438 and `add_thread` at line 2245) shows a working order on SolidWorks 2026. This change is written fresh in the fork's adapter style, with it as the reference for call order and arguments.

The order in hjbaard's code:
- **Hole:** check the arguments and that the point lies on the face; `ClearSelection2`; `SelectByRay` from 0.01 mm above the point along the face normal, straight into the face (not `SelectByID2`, which picks what the view shows first); `FeatureManager.HoleWizard5(...)` with 27 arguments; then fix the position (D3); then read the sizes back from `IWizardHoleFeatureData2`.
- **Thread:** `CreateDefinition(87)` gives an `IThreadFeatureData`; `InitializeThreadData`; `Type` takes the profile library name (`Metric Die` or `Metric Tap`) and reads back the library file; `ISldWorks.GetConfigurationNames(file)` lists the valid sizes; then `Edge`, `ThreadMethod`, `Size`, `EndCondition`, `BlindDepth`, `RightHanded`, and `CreateFeature`.
- **Findings it records:** the Hole Wizard puts the hole about 0.04 mm from where the face was picked; SolidWorks accepts any thread size string and cuts a meaningless groove for an unknown one; SolidWorks shifts an internal thread with its hole, so the hole must have the ISO basic minor diameter; each `HoleWizard5` value vector was found by trial, and a wrong one makes a wrong hole without an error.

Every COM call stays on the connect thread through `_handle_com_operation`, which also gives the settle pause. New dispatch objects get their methods flagged before use (CLAUDE.md, method flagging; pitfalls #5 and #24).

## Goals / Non-Goals

**Goals:**
- An agent can make a clearance, counterbore, countersink or tapped ISO metric hole on a flat face, from a standard size, and read back the sizes SolidWorks used.
- The hole lands on the given point and its position is a dimension the agent can change.
- An agent can make a real, printable ISO metric thread on a rod or in a hole, and the tool refuses a size or diameter that would cut garbage.
- A failed call leaves the model as it was. A SolidWorks that lacks the feature answers `not_supported` with the reason.

**Non-Goals:**
- Inch and other standards (ANSI, DIN, JIS). The tools are ISO metric only (D4).
- Fine-pitch tapped holes through the Hole Wizard. A modeled thread uses the coarse pitch; `create_thread` takes any pitch the library holds.
- Left-hand threads (`RightHanded` is always true; see Open Questions).
- Slot holes, pipe taps, legacy holes, Hole Wizard hole series and several points in one call.
- Hole tables and drawing callouts. Pitfall #18 already imports Hole Wizard location dimensions on a drawing; D3's dimensions are checked against it live.
- Editing a Hole Wizard hole after the fact (`edit_feature` stays as it is).

## Decisions

### D1. Two tools, in `tools/modeling.py`

- `create_hole_wizard_hole` and `create_thread`, with Pydantic inputs `CreateHoleWizardHoleInput` and `CreateThreadInput` (`CompatInput`, as `EmbossTextInput` and `SelectFaceEdgesInput`).
- Both go in `tools/modeling.py`, beside `emboss_text` and `create_cut_extrude`. The family is `modeling`, which is already in `DOCUMENT_FAMILIES` (`tools/target_document.py`), so both take `document` with no change there. The file is long; splitting it is a separate job (Open Questions).
- The names are provisional until queue item 11 settles the verb list.
- A tapped hole with a modeled thread calls the thread routine inside the adapter. It doesn't call the tool.

### D2. Picking the face: `face_point`, `side`, and a ray

- `face_point` is the hole's centre, `[x, y, z]` mm, and it must lie on a flat face. `FaceEdgeSelector.face_at_point` finds the face. A point on no face is `not_found`. A point on a face that isn't flat (for example, inside an earlier countersink, which is a cone) is `invalid_input` and says what the face is. Nothing is changed.
- `side` is optional. It is a check, not a search: when given, the face's outward normal must point that way (`side_vector`, dot product at least 0.999). A mismatch is `invalid_input` naming the face's actual direction. It guards a point on an edge or at a step.
- The pick is `IModelDocExtension.SelectByRay` from 0.01 mm above the point along the normal, straight into the face. `SelectByID2` is a screen pick through the point: it picks what the view shows first, so a side view hit another face (pitfall #34, hjbaard's finding).
- The selection is cleared first and its count read after the pick: it must be exactly 1 (pitfall #59). `SelectByRay`'s selection-type constant is `swSelFACES` (2) in `swSelectType_e`.
- Two flat faces can lie at one height (a slot across the top splits it in two). The point decides which; neither is refused.
- Probe 1.2 confirms the ray pick in a side view, on a split top face, and with a face that has an earlier hole in it.

### D3. Position: the hole lands on the point and is dimensioned from the origin

- SolidWorks puts the hole where the face was picked, and hjbaard measured it about 0.04 mm off, in an under-defined position sketch.
- The tool opens the hole's position sketch (a sub-feature of the hole, a `ProfileFeature` with one sketch point and no segments), and dimensions the point from the origin along the face sketch's own horizontal and vertical directions. The two dimensions carry the exact coordinates. The point's model coordinates go through the sketch's `ModelToSketchTransform` (`IMathUtility.CreatePoint` and `IMathPoint.MultiplyTransform`; both need flagging, as pitfall #42).
- The reply gives the two dimension names as `position.dimensions` (`x`, `y`), so `set_dimension` can move the hole, and `position.fully_defined` (from `check_sketch_fully_defined` on that sketch).
- If pinning fails, the hole is kept, `fully_defined` is false and the reply carries a `warning`. The hole is right to within the Hole Wizard's own placement, so this isn't a failure.
- Probe 1.3 settles three things: where SolidWorks really puts the point on the installed version; whether the position sketch can be edited through `EditSketch` and dimensioned from the origin through the fork's own sketch calls; and whether `HoleWizard5` takes a face plus pre-selected sketch points, so the position is exact from the start. If it does, the pre-made, dimensioned point replaces the pin step.

### D4. Kinds map to SolidWorks' standard indices

Every value below was read from `data/swconst_enums.json` (SOLIDWORKS 2026 SP4.1):

| kind | `GenericHoleType` (`swWzdGeneralHoleTypes_e`) | `FastenerTypeIndex` (`swWzdHoleStandardFastenerTypes_e`) |
|---|---|---|
| `clearance` | `swWzdHole` = 2 | `swStandardISOScrewClearances` = 144 (ISO 273) |
| `counterbore` | `swWzdCounterBore` = 0 | `swStandardISOSocketHeadCap` = 139 (ISO 4762) |
| `countersink` | `swWzdCounterSink` = 1 | `swStandardISOSocketCTSKFlatHead` = 140 (ISO 10642) |
| `tapped` | `swWzdTap` = 4 | `swStandardISOTappedHole` = 147 |

- Standard: `swStandardISO` = 8 (`swWzdHoleStandards_e`).
- Fit: `swScrewClearanceClose` 0, `Normal` 1, `Loose` 2 (`swWzdHoleScrewClearanceTypes_e`).
- End condition: `swEndCondBlind` 0 or `swEndCondThroughAll` 1 (`swEndConditions_e`).
- Cosmetic thread: `swCosmeticThreadWithCallout` 1; thread end: `swEndThreadTypeBLIND` 0 or `THROUGH_ALL` 1 (`swWzdHoleThreadEndCondition_e`; `THROUGH_NEXT` 2 also exists).
- `swFmHoleWzd` = 25 and `swFmSweepThread` = 87 in `swFeatureNameID_e`.
- Only ISO is exposed, so the input has no `standard`. The enums list 18 standards, and adding one later is a new input and a new probe.
- **The value vector.** `HoleWizard5` takes twelve values (`Value1`…`Value12`) after `Length`. hjbaard found by trial, with a volume test for each, that:
  - a clearance hole takes the fit in `Value1`, and `0` in the other eleven for a through hole (`-1` for a blind one);
  - a counterbore or countersink takes the fit in `Value4` and `-1` elsewhere;
  - a tapped hole takes `-1` except the cosmetic thread type in `Value7` and the thread end in `Value8`; zeros there cut garbage, and without the cosmetic thread SolidWorks mills the threaded length at the major diameter.
  - These are the starting point, held in one pure function `wizard_values(kind, fit, through)` with a unit test. Probe 1.1 re-proves each vector with a volume test on the installed SolidWorks, and the test then pins what was measured.

### D5. Sizes come from SolidWorks, not from this repo (licensing)

- The tool carries no table of ISO sizes. `HoleWizard5` takes the size string (`"M3"`) and SolidWorks fills in the diameters from its own tables. The reply reads them back from `IWizardHoleFeatureData2` (`ThruHoleDiameter` or `HoleDiameter`, `CounterBoreDiameter`, `CounterBoreDepth`, `CounterSinkDiameter`, `CounterSinkAngle`, `TapDrillDiameter`, `ThreadDiameter`, `ThreadDepth`, `CosmeticThreadType`).
- hjbaard does the same. Its constants file holds enum values and a three-entry fit map, and no size table. So there is nothing to copy and no notice to carry.
- What the code does hold is formulas, from the geometry of the ISO basic thread profile (ISO 724): the minor diameter `D1 = D − (5√3 / 8) · P`, which is `D − 1.0825318 · P`. M10x1.5 gives 8.3762 mm. A formula is not a table. It is rebuilt from the standard's geometry, not copied.
- The coarse pitch for a size (`M3` → `M3x0.5`) is the largest pitch the installed `Metric Tap` library holds for that diameter (`ISldWorks.GetConfigurationNames`). It is read, not tabulated.
- Hand-calculation values in the specs (M3 clearance 3.2 / 3.4 / 3.6 for a close, normal and loose fit; counterbore Ø3.4 with a Ø6.5 × 3.4 step; countersink Ø6.72 at 90°; M3 tap drill 2.5 with a thread depth of 6.0) come from hjbaard's guide and tests, which read them from SolidWorks 2026. They are marked "to confirm". Probe 1.4 reads them on the installed version. Any other size's values are left to that probe: nothing is taken from memory of ISO 273.
- The mock adapter needs a few sizes to return a believable payload (D13). It holds only what probe 1.4 measured, marked as mock data.

### D6. `create_thread`: the order of checks

1. Parse `size` with a pure function (`^M(\d+(\.\d+)?)x(\d+(\.\d+)?)$`) into diameter and pitch. A bad form is `invalid_input` with an example. Check `length_mm > 0`.
2. Make the thread definition. A SolidWorks that can't is `not_supported` (D8).
3. Set `Type` to `Metric Tap` (internal) or `Metric Die` (external); read it back. Empty means the library file is missing: `not_supported`, naming the library and where SolidWorks looks (File Locations, Thread Profiles).
4. `ISldWorks.GetConfigurationNames(file)` lists the valid sizes. A size not in it is `invalid_input` listing the library's sizes for that diameter. This is the check hjbaard found necessary.
5. Find the circular edge. `edge_point` is a point on the edge, not its centre: two coaxial circles (a rod's end edge and the edge of a chamfer on it) have one centre, and a point on the edge picks one of them. The tool walks the part's circular edges (through `PartIndex`, `limits.check` between edges) and keeps those whose closest point to `edge_point` is within 0.01 mm. None is `not_found`, and the message gives the nearest circular edge's centre and radius. More than one is `invalid_input` listing them.
6. Check the edge's diameter: the nominal diameter for external, the ISO basic minor diameter for internal. SolidWorks shifts an internal thread with its hole, so any other diameter would give a wrong thread. A mismatch beyond 0.01 mm is `invalid_input`: "an internal M10x1.5 thread needs a Ø8.376 hole, but the edge is Ø8.500. Drill the Ø8.376 hole first." Nothing is changed.
7. Check the turn count (D9).
8. Set `Edge`, `ThreadMethod` (`swThreadMethod_Cut` 0), `Size`, `EndCondition` (`swThreadEndCondition_Blind` 0), `BlindDepth` (`length_mm` in metres) and `RightHanded`, then `CreateFeature`. A `None` result is an error that asks whether there is `length_mm` of material behind the edge.
9. `ForceRebuild3`, `failing_features`, `measured_change`.

All of 1, 6 and 7 are pure and run before SolidWorks is touched, wherever the data allows.

### D7. A tapped hole with a modeled thread is two features, done together

- The Hole Wizard makes only a cosmetic thread. For `thread: "modeled"` the tool drills at the ISO basic minor diameter (the `Diameter` argument, in metres) and then makes a modeled internal thread from the hole's mouth edge. The mouth is found from the hole's own position, which the tool knows.
- The size must be a plain `M<d>` (not `M3x0.5`). The tool takes the coarse pitch from the library (D5).
- The thread's length is the standard thread depth read back from the hole (`ThreadDepth`; 2 × D for M3 per hjbaard) for a blind hole, or the length the hole runs through the part for a through hole. Probe 1.9 settles how to read the through length (the far edge of the bore, or the hole's own depth), and whether the cosmetic thread stays on beside the modeled one.
- `thread: "modeled"` with any kind but `tapped` is `invalid_input`. So is `fit` with `tapped`.

### D8. Minimum SolidWorks and `not_supported`

- The tools gate on capability, not on a version number. Each step asks SolidWorks and reads the answer:
  - `CreateDefinition(87)` returns nothing, or `IThreadFeatureData` is missing: the Thread feature is not there.
  - `Type` reads back empty: the thread profile library is not installed.
  - `HoleWizard5` is not a member of `IFeatureManager`: this SolidWorks is older than the call.
- Each is `not_supported`, and the message says what was missing and which SolidWorks it was: `RevisionNumber`'s major (SolidWorks' year minus 1992: 33 is 2025 and 34 is 2026, as `swconst_enums.json`'s `34.4.1.1011` shows), and the year. The message includes "does not support", which `error_classifier.py` already maps to `not_supported`.
- hjbaard's error text says the Thread feature needs SOLIDWORKS 2016 (major 24). That is the working minimum for the docs until a probe can say otherwise. It is unverified here: only 2025 and 2026 can be probed (Probe 1.7). The signed-off versions are the ones probed. Older ones get the capability checks.
- The mock never returns `not_supported` for these. It has no SolidWorks to lack.

### D9. Long rebuilds: where `limits.check` goes

- `limits.check` runs between COM calls, so it can't stop one `CreateFeature` or one rebuild already running. What it can guard:
  - the circular-edge walk in D6 step 5 (one check per edge);
  - the multi-step hole (pick, create, pin, read back, thread), one check between steps;
  - the cleanup after a failure.
- A thread's cost grows with its turns: a long, fine thread rebuilds slowly, and every later rebuild of the part pays for it again. So the tool refuses a thread of more than `MAX_THREAD_TURNS` turns (`length_mm / pitch`), before SolidWorks is touched. The refusal is `invalid_input` and says to shorten the thread or use a coarser pitch.
- Probe 1.8 times M3x0.5 and M10x1.5 at several lengths, and the limit is set from it. The value is not guessed here.
- The tool description says that a modeled thread slows later rebuilds.

### D10. All or nothing

- A hole is a Hole Wizard feature plus its position sketch (plus a thread feature for a modeled one). If any step after the first feature fails (a refused thread, a failed read-back), the tool removes what it made and rebuilds, then fails with the reason. `delete_feature` removes a feature with its dependents, so deleting the hole removes its sketch and the thread.
- Pinning the position (D3) is the one step that doesn't fail the call (D3).
- The cleanup is checked: the feature count and the volume are read again and must equal the values before the call. If they don't, the error says what is left over.
- Probe 1.10 records what `HoleWizard5` returns for an unknown size (hjbaard reads `None`), what a refused `CreateFeature` leaves in the tree, and that deleting the hole leaves nothing behind.

### D11. Replies

- Both tools measure the geometry before the first change and after the last, and attach `measured_change` (as `emboss_text` does with `_read_summary` and `_attach_measured_change`). A hole that removes no volume gets the standard `warning`.
- `create_hole_wizard_hole` returns `feature` (the Hole Wizard feature's name), `hole` (the standard's sizes, below), `position` (`dimensions`, `fully_defined`), `thread` (for a modeled one: the `create_thread` block), `face` (its centre and normal) and `measured_change`.
- `hole` holds `standard` (`"ISO"`), `kind`, `size`, and from SolidWorks' tables, in mm and degrees:
  - clearance: `fit`, `diameter_mm`;
  - counterbore: those plus `cbore_diameter_mm`, `cbore_depth_mm`;
  - countersink: `fit`, `diameter_mm`, `csink_diameter_mm`, `csink_angle_deg`;
  - tapped: `tap_drill_diameter_mm`, `thread_diameter_mm`, `thread_depth_mm`, `cosmetic_thread`.
- `create_thread` returns `feature`, `thread` (`size`, `pitch_mm`, `major_diameter_mm`, `minor_diameter_mm`, `length_mm`, `internal`, `right_handed`), `edge` (`center_mm`, `diameter_mm`) and `measured_change`.

### D12. The adapter interface

New abstract methods on `SolidWorksAdapter` (`adapters/base.py`):

```python
async def create_hole_wizard_hole(
    self,
    kind: str,
    size: str,
    face_point: list[float],
    side: str | None = None,
    depth_mm: float | None = None,
    fit: str | None = None,
    thread: str = "cosmetic",
) -> AdapterResult[dict[str, Any]]: ...

async def create_thread(
    self,
    size: str,
    edge_point: list[float],
    length_mm: float,
    internal: bool = False,
) -> AdapterResult[dict[str, Any]]: ...
```

- A `fit` of `None` means `normal` for clearance, counterbore and countersink.
- Pure helpers in `adapters/solidworks/threads.py`, unit-tested without COM: `parse_thread_size(size) -> (diameter, pitch)`, `minor_diameter(diameter, pitch)`, `coarse_size(size, library_sizes)`, `thread_turns(length, pitch)`.
- Pure helpers in `adapters/solidworks/holes.py`: `wizard_values(kind, fit, through)` (D4), `check_hole_input(...)`.
- `SolidWorksHolesMixin` and `SolidWorksThreadsMixin` hold the COM parts and are added to `PyWin32Adapter`'s bases. `connection_pool.py` and `circuit_breaker.py` forward both methods.

### D13. The mock

`mock_adapter.py` gains both methods, with the payload shapes of D11:
- **Sizes:** a small table of the sizes probe 1.4 measured, marked as mock data. Another size is `invalid_input`, as SolidWorks' would be.
- **Thread sizes:** `parse_thread_size` and `minor_diameter`, so the diameter check (D6 step 6) runs in the mock too.
- **Failures:** the text `__refuse_thread__` in `size` is refused after the hole is made, so tests can drive D10's cleanup.
- **Geometry:** `measured_change` holds null values and a note, as for other mock features. The mock doesn't compute volumes.

This mirrors the payload shapes, not SolidWorks' geometry.

### D14. Support levels and hints

- Both tools start `experimental` (`_UNCHECKED`) and move to `stable` when `test_live_sw_hole_wizard.py` passes on SolidWorks 2025 and 2026.
- Hints: both `_adds` (each call adds another feature). `create_hole_wizard_hole`: "Create Hole Wizard Hole". `create_thread`: "Create Thread".
- Family: `modeling`.

## Risks / Trade-offs

- **Silent wrong holes.** A wrong `HoleWizard5` value vector gives a wrong hole without an error. Mitigation: the reply reads the sizes back, and the live test checks each kind's volume against a hand calculation from the sizes returned, plus the "to confirm" anchors (D5).
- **Silent wrong threads.** SolidWorks cuts garbage for an unknown size, and shifts an internal thread with its hole. Mitigation: the library check and the diameter check (D6), both before anything changes.
- **The 0.04 mm.** The Hole Wizard lands off the point. If pinning fails (D3), the position is as SolidWorks left it. Mitigation: the `warning`, and probe 1.3's alternative.
- **Version coverage.** Only 2025 and 2026 can be probed, so older versions are covered by the capability checks (D8) and nothing else. The docs say which versions were signed off.
- **Thread library varies.** The profile library is a SolidWorks install setting, and a localised or customised install may name or hold sizes differently. Mitigation: the sizes are read from the library, never listed here; the library names `Metric Tap` and `Metric Die` are the one assumption. Probe 1.5 reads them.
- **Slow rebuilds.** A fine, long thread slows every rebuild after it (D9). The turn cap is the guard, and its value comes from a probe.
- **Two features, one call.** A modeled tapped hole is a hole and a thread. The cleanup (D10) makes it all or nothing, at the cost of an undo path that needs its own test.
- **Tool count.** Two more tools while queue items 10–12 work to shrink the list. They are `modeling` tools, a core family.
- **Occupying a point.** A second hole at a point already inside a hole's cone or step lands on a non-flat face; D2 refuses it instead of picking a neighbour.

## Probe results

To fill in after task group 1.

## Live results

To fill in after task group 8.

## Open Questions

- Should `modeling.py` be split now (`tools/holes.py`, a new family added to `DOCUMENT_FAMILIES`), or later with the other splits?
- Should `create_thread` take `right_handed` (left-hand threads), and should `create_hole_wizard_hole` take a fine `pitch` for modeled tapped holes? Both are cheap in the adapter and cost a probe each.
- Should `create_thread` also take an `edge_id` (the persistent ids `select_edges` can give, pitfall #59) so an agent can name an edge it already found, instead of computing a point on it?
- Should a hole be patternable straight away? `pattern_linear` and `pattern_circular` take a feature; whether they take a Hole Wizard feature is for a live check, not this change.
- Should `edit_feature` learn to change a hole's size later? It would need `IWizardHoleFeatureData2.ModifyDefinition`, and belongs in its own change.
