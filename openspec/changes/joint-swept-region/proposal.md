## Why

An agent can check one pose of an assembly (`check_interference`, `measure_clearance`) and turn one component about a free axis (`rotation_clearance`). It can't ask the questions a moving joint raises:

- **What space does this part sweep as the joint moves?** A lid, a latch arm or a lever needs a keep-out zone: the outline of everything the part covers over its travel, in a plane, to cut a pocket for it or leave room around it. Today the agent steps the joint by hand with `set_dimension`, reads positions, and builds the outline itself, if at all.
- **Does the joint clear its neighbours over its whole range?** `rotation_clearance` moves `Transform2` directly and doesn't rebuild mates, so it ignores what the mates do (its own docs say a coincident mate "did not hold it"). A joint is driven by a mate dimension, and only stepping that dimension moves the parts the way the mates say.

The comparison with hjbaard's `solidworks-mcp` (v0.21.0) found `swept_region` and `check_motion` there. Nothing here does what `swept_region` does, and `rotation_clearance` covers only part of `check_motion`. This change brings both into the fork, built the fork's way. No GitHub issue: issues are off on the fork `anyosag/SolidworksMCP-python`, and upstream has none for this.

## What Changes

- **New tool `swept_region`:**
  - Inputs: `component`, `dimension` (a joint's mate dimension), `values`, `heights_mm`, `axis` (`x`, `y` or `z`; default `z`), `margin_mm` (0), `tolerance_mm` (0.2) and `frame` (a component; default the assembly).
  - It steps `dimension` through `values`. At each step it cuts the component's tessellated solid at `axis` = each of `heights_mm`.
  - It joins all the sections, grows them by `margin_mm`, and traces the outlines within `tolerance_mm`, in the axes of `frame`.
  - It returns the regions, largest first: `outline_mm` (counter-clockwise), `holes_mm` and `area_mm2`.
  - It always puts the dimension back, in a `finally`, and says so in `restored`.
- **New tool `check_motion` (in this change; see design D1):**
  - Inputs: `dimension`, `values` and `distances` (pairs of components).
  - The same stepping. At each step it runs `check_interference` and the fork's `measure_clearance` for each pair.
  - It returns every step, and a summary: `clash_free`, the smallest distance per pair with the value where it occurs, and the components that moved.
  - It differs from `rotation_clearance`: that turns a body or component about an axis and doesn't drive a mate dimension, so mates are ignored. `check_motion` drives the mate, so the mates solve.
- **Shared stepping rules (spec `tools/joint-stepping`):**
  - The dimension must be a length or an angle. The write is read back.
  - A step the dimension refuses stops the run.
  - Broken mates are refused up front.
  - Stepping that moves nothing is an error.
  - The dimension is restored whatever happens.
- **Pure geometry module `utils/swept_region.py`**, ported from hjbaard's `region_tools.py` (grid fill, grow, trace, Douglas-Peucker; MIT), with his copyright and permission notice in its header and an entry in `THIRD_PARTY_NOTICES.md`.

## Capabilities

### New Capabilities
- `tools/joint-stepping`: how a joint's mate dimension is stepped, read back and restored, and when stepping is refused.
- `tools/swept-region`: the outline of what a component covers in a plane while a joint moves.
- `tools/motion-check`: interference and clearance at each step of a joint.

### Modified Capabilities
None.

## Impact

- **Depends on change `mesh-sections` (item 20):** `swept_region` slices triangles with `src/solidworks_mcp/utils/mesh_sections.py`, `section(triangles, axis, height)`, which that change adds. This change doesn't re-implement it. It can be specified and unit-tested up to the slicing call without it, but tasks 2.3 onward need it merged (or on the branch).
- **COM/adapter code (extra risk per CLAUDE.md):**
  - New `adapters/solidworks/joint_sweep.py` (`SolidWorksJointSweepMixin`): the stepper, face tessellation (`IFace2.GetTessTriangles`, as `thickness.py` reads it), component transforms (`IComponent2.Transform2`), the mate dimension (`IModelDoc2.Parameter`, `IDimension.SystemValue`, `ForceRebuild3`), and the broken-mate check (`rebuild_inspector.collect_rebuild_issues`).
  - `adapters/solidworks/io.py`: the body of `check_interference` becomes a module function so the stepper can call it without nesting an adapter operation (design D8). No behaviour change.
  - `adapters/base.py`, `connection_pool.py`, `circuit_breaker.py` forward the two new methods; `pywin32_adapter.py` adds the mixin.
  - `mock_adapter.py` gains a joint table and the two methods.
  - New dispatch objects get their methods flagged before use (pitfalls #41, #42). Loops call `limits.check` between COM calls (CLAUDE.md, `operation_stopped`).
- **Tools:** `tools/analysis.py` (beside `check_interference` and `rotation_clearance`): `SweptRegionInput`, `CheckMotionInput`, both tools. `support_levels.py` (experimental until the live test passes), `tool_hints.py`, the tool catalog, the progress files, and the agent skill's `references/workflows.md` and `errors-and-limits.md`.
- **Pure modules:** `utils/swept_region.py`, and the placement and summary helpers in `joint_sweep.py`.
- **Docs:** pitfall entries for what the probes find (angle-mate units, nested transforms, interrupted runs).
- **Tests:**
  - unit tests beside each module;
  - a probe script `tests/scripts/probe_joint_sweep.py` (scratch assemblies);
  - a live test `tests/live/test_live_sw_joint_sweep.py`.
- **Licence:** `utils/swept_region.py` is hjbaard's code, nearly as written. It keeps his MIT notice, and `THIRD_PARTY_NOTICES.md` (new at the repo root, or extended if change `mesh-sections` has added it) records it. The COM code is written fresh with his as the reference, credited in `design.md`.
