## Why

An agent can drill a plain hole (a sketch circle and `create_cut_extrude`), but it can't make the holes a real part needs. A bolted plate wants an M5 clearance hole, a counterbore for a socket head screw, a countersink, or a tapped hole. Today the agent has to look up the ISO size in its head, draw the circle, and do the same again for the counterbore step. It also can't make a printable thread at all:

- Clearance, counterbore and tap-drill sizes live in SolidWorks' Hole Wizard tables, which this server never calls.
- A thread that prints (a real helix, not a cosmetic line) is SolidWorks' Thread feature, which this server never calls either.
- No existing tool takes a standard size and returns what SolidWorks used, so an agent can't check a hole against the standard.

The comparison with hjbaard's `solidworks-mcp` (v0.21.0) found `add_hole_wizard` and `add_thread` there and nothing like them here. This change brings both into the fork, built the fork's way. No GitHub issue: issues are off on the fork `anyosag/SolidworksMCP-python`, and upstream has none for holes or threads.

## What Changes

- **New tool `create_hole_wizard_hole`:**
  - Takes `kind` (`clearance`, `counterbore`, `countersink` or `tapped`), `size` (`M3`, `M5`, …), a `face_point` (mm) that is the hole's centre on a flat face, and an optional `side` that is checked against that face.
  - Takes `depth_mm` (blind) or leaves it out for through all, `fit` (`close`, `normal`, `loose`; not for tapped) and `thread` (`cosmetic` or `modeled`, tapped only).
  - The sizes come from SolidWorks' own ISO tables. The tool carries no size table.
  - The hole lands on `face_point` and its position is dimensioned from the origin, so it can be moved later with `set_dimension`.
  - It returns the standard's sizes it used (`hole`), the position dimensions, `thread` for a modeled one, and `measured_change`.
- **New tool `create_thread`:**
  - Takes `size` (`M10x1.5`), `edge_point` (a point on the circular edge where the thread starts, mm), `length_mm` and `internal`.
  - The size is checked against SolidWorks' thread-profile library, because SolidWorks cuts a meaningless groove for a size it doesn't know.
  - It refuses an edge of the wrong diameter and says which diameter is needed.
  - It returns the pitch and the diameters, and `measured_change`.
- **Not supported on a SolidWorks that lacks the feature.** The tools return `not_supported` with the reason (the revision, and what was missing) instead of a COM error.

## Capabilities

### New Capabilities
- `tools/hole-wizard`: ISO holes from the Hole Wizard: kinds, sizes, fits, depth, position, cosmetic and modeled threads, refusals and cleanup.
- `tools/threads`: modeled ISO metric threads on a circular edge: size checking, diameter checking, the reply and the `not_supported` rule.

### Modified Capabilities
None.

## Impact

- **COM/adapter code (extra risk per CLAUDE.md):**
  - New `adapters/solidworks/holes.py` (`SolidWorksHolesMixin`) on `IFeatureManager.HoleWizard5(...)`, `IModelDocExtension.SelectByRay` and `IWizardHoleFeatureData2`.
  - New `adapters/solidworks/threads.py` (`SolidWorksThreadsMixin`) on `IFeatureManager.CreateDefinition(swFmSweepThread)` → `IThreadFeatureData` and `ISldWorks.GetConfigurationNames`.
  - `HoleWizard5` takes 27 arguments, and a wrong vector makes a wrong hole without an error. Probes settle each one.
  - The new dispatch objects (`IWizardHoleFeatureData2`, `IThreadFeatureData`, `IEdge`, `ICurve`, `ISketch`, `ISketchPoint`) must have their methods flagged before use (pitfalls #5 and #24).
  - `adapters/base.py`, `connection_pool.py`, `circuit_breaker.py` forward the two new methods; `pywin32_adapter.py` adds the two mixins.
  - `mock_adapter.py` gains both methods.
- **Tools:**
  - `tools/modeling.py` (beside `emboss_text` and `create_cut_extrude`), so no new family.
  - `support_levels.py` (experimental until the live test passes), `tool_hints.py`, `error_classifier.py` for the `not_supported` and diameter messages.
  - The tool catalog, the progress files, and the agent skill's `references/workflows.md` and `errors-and-limits.md`.
- **Docs:** pitfall entries for what the probes find (the `HoleWizard5` vectors, where the hole point lands, the thread library, rebuild time).
- **Tests:**
  - unit tests beside each module;
  - a probe script `tests/scripts/probe_hole_wizard.py` (scratch documents);
  - a live test `tests/live/test_live_sw_hole_wizard.py`.
- **Credit and licence:** hjbaard's `add_hole_wizard` and `add_thread` (MIT, © 2026 Henk Jan Baard) are the reference for the COM call order and arguments. The code is written fresh, so no copied code needs a notice; `design.md` credits it. hjbaard carries no size tables either: it reads SolidWorks' own. This change does the same, so no standard's table is copied (design D5).
