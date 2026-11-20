## ADDED Requirements

### Requirement: A component can be replaced by another file

The adapter SHALL expose `replace_component(component, path, reattach_mates=True, all_instances=False)`, and the server SHALL register a `replace_component` tool with the same inputs. `component` is the instance name of a top-level component as `list_components` reports it. `path` is a `.sldprt` or `.sldasm` file.

On an active assembly the new file SHALL take the named component's place and keep its position. The reply's `data` SHALL hold:
- `replaced` (the instance names that went);
- `component` (the new component that took the named one's place), with `name`, `path`, `placement` (`origin_mm`, `rotation` as rows, `fixed`, read back from SolidWorks) and `bounding_box_mm` (`[xmin, ymin, zmin, xmax, ymax, zmax]` in assembly coordinates, or null when unreadable);
- `bounding_box_before_mm` (the same for the old component);
- `components` (one entry like `component` for every new component; one entry unless `all_instances`);
- `mate_errors` (see below);
- `saved` (false);
- `note` (that `list_references` and `pack_and_go_assembly` read the saved file).

Success SHALL be decided by reading the assembly's components after the call, not by SolidWorks' return value.

#### Scenario: A block replaced by a longer block
- **WHEN** an assembly holds `plate-1`, a 40 × 20 × 10 mm block with its origin at a corner, placed at the assembly origin, and an agent calls `replace_component` with `path` of a 50 × 20 × 10 mm block with its origin at a corner
- **THEN** `replaced` is `["plate-1"]`, `component.path` is the new file, `component.placement.origin_mm` is `[0, 0, 0]`, `bounding_box_before_mm` is `[0, 0, 0, 40, 20, 10]` and `bounding_box_mm` is `[0, 0, 0, 50, 20, 10]`, each within 1e-6 mm; `list_components` no longer lists `plate-1`

#### Scenario: A mated component keeps its mates
- **WHEN** a block is mated coincident to the top face of a base, and an agent replaces the block with a taller block that has the same bottom face
- **THEN** `mate_errors` is empty, and the new component's `bounding_box_mm` starts at the base's top face

#### Scenario: Every instance of a part
- **WHEN** `bracket-1` and `bracket-2` are two instances of one file and an agent calls `replace_component` on `bracket-1` with `all_instances: true`
- **THEN** `replaced` lists both names, and `components` holds two entries whose `path` is the new file

#### Scenario: A component whose file was made anew
- **WHEN** a part was made anew and saved under the path an assembly uses, so its component shows no geometry, and an agent replaces that component with the same path
- **THEN** the call either succeeds with a component whose `bounding_box_mm` is not null, or fails with a message that says SolidWorks refused the same file

### Requirement: Mates that fail are reported

After a replace, `mate_errors` SHALL list each mate of the replaced components that is in error. Each entry SHALL have `mate` (the feature name), `type`, `error` (the cause) and `new` (false when the mate was already in error before the call). A mate in error SHALL NOT fail the call. When `mate_errors` is not empty, the reply SHALL also carry a `warning` that names the mates.

With `reattach_mates` false, the mates SHALL still be listed as they read afterwards.

#### Scenario: A mate loses its face
- **WHEN** a block is mated coincident by its top face, and an agent replaces it with a part that has no face where that mate looked
- **THEN** the call succeeds, `mate_errors` lists that mate with `new: true` and a cause, and a `warning` names it

#### Scenario: A mate that was already broken
- **WHEN** an unrelated mate of the replaced component was in error before the call and still is
- **THEN** it is listed with `new: false`

### Requirement: A refused replace changes nothing the agent must undo

The adapter SHALL refuse a replace before SolidWorks is called when:
- the active document is not an assembly (`error_type: wrong_document_type`);
- `component` names a part inside a sub-assembly (a name with `/`), where SolidWorks replaces top-level components only (`error_type: invalid_input`, naming the sub-assembly);
- `component` isn't in the assembly (`error_type: not_found`, listing the assembly's components);
- `path` doesn't exist (`error_type: not_found`), or isn't a `.sldprt` or `.sldasm` file (`error_type: invalid_input`).

If SolidWorks leaves no component with the new file, the call SHALL fail naming the component and the path, and the message SHALL list the components the assembly now holds.

#### Scenario: A part inside a sub-assembly
- **WHEN** an agent calls `replace_component` with `component: "frame-1/bolt-1"`
- **THEN** the call fails with `error_type: invalid_input` naming `frame-1`, and nothing is sent to SolidWorks

#### Scenario: A file that isn't there
- **WHEN** an agent calls `replace_component` with a `path` that doesn't exist
- **THEN** the call fails with `error_type: not_found` naming the path, and the assembly is as it was

#### Scenario: Not an assembly
- **WHEN** the active document is a part
- **THEN** the call fails with `error_type: wrong_document_type`

### Requirement: Replacing changes the assembly in memory only

`replace_component` SHALL NOT save any document. After a replace and a `save_assembly`, the assembly's references (`list_references`) SHALL name the new file and not the old one, and the save guard SHALL treat the new file as a referenced file (it is saved or asks for confirmation by the rules of `adapters/save-prompts`).

#### Scenario: The saved assembly points to the new file
- **WHEN** an agent replaces `plate-1` with another file, calls `save_assembly`, and then `list_references` on the assembly
- **THEN** the references include the new file's path and not the old one's

#### Scenario: Before the save
- **WHEN** an agent calls `list_references` on the assembly's file right after the replace and before saving
- **THEN** the references are the saved ones, and the replace reply's `saved` was false

### Requirement: A replace respects the dialog watchdog and a minimized window

`replace_component` SHALL run through the adapter's dialog watchdog. A dialog the watchdog cancels SHALL fail the call with `error_type: solidworks_dialog`, and the message SHALL say whether the replace happened. When SolidWorks is minimized as the call starts, it SHALL be minimized when the call ends.

#### Scenario: SolidWorks is minimized
- **WHEN** SolidWorks is minimized and an agent calls `replace_component` on a valid component and file
- **THEN** the replace succeeds and `set_solidworks_window` reports the window minimized afterwards

#### Scenario: A dialog blocks the replace
- **WHEN** SolidWorks opens a modal dialog during the call and the watchdog cancels it
- **THEN** the call fails with `error_type: solidworks_dialog`, with the dialog's text in `data.dialog`
