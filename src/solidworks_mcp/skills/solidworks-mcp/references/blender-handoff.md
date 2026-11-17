# SolidWorks to Blender and back

Model in SolidWorks; render, inspect or animate in Blender through the
Blender MCP (`mcp__Blender__*`). Blender never edits the part: changes go
back to SolidWorks, and the model is exported again.

## The round trip

1. In SolidWorks, activate the part or assembly (or pass `document`).
2. `export_for_blender`. Defaults: GLB, `up_axis: "y"`, a folder under the
   system temp folder, the document's name. Give `up_axis: "z"` for a part
   modelled Z-up (the Dipper), so it stands up in Blender.
3. Run `data.blender_code` as it is with `execute_blender_code`. It imports
   into collection `SW <name>` under an empty `SW <name> root`, and returns
   `objects`, `size_mm` (measured in Blender, along its X, Y, Z),
   `expected_size_mm` and `size_matches`.
4. If `size_matches` is not `true`, stop: the units or the turn are wrong.
   Report both sizes rather than scaling by hand.
5. Light, add a camera and render with the Blender MCP:
   `render_viewport_to_path` (the scene's render settings) or
   `render_thumbnail_to_path` (quick and small). Read the PNG back to look
   at it.
6. After a design change, export again with the same `name` and run the new
   `blender_code`: it removes the earlier import's objects, meshes and
   materials (nothing else in the scene) and imports the new one. Lights,
   cameras and materials you set up outside the collection stay. Materials
   you put on the imported objects are lost: put them back from your own
   script, by object name.

## What each format keeps

| | GLB (default) | STL |
|---|---|---|
| Parts | One object per component, in place | One mesh for the whole model |
| Colours | SolidWorks' appearances, as materials | None |
| Units | Metres (checked against the model's size) | mm, imported with `global_scale` 0.001 |
| Placement | Model coordinates | Model coordinates (SolidWorks' "move to positive space" is turned off for the export) |
| Mesh | SolidWorks' own tessellation | `resolution` coarse or fine, or the user's setting |

Use STL when SolidWorks' GLB is wrong (the tool warns when its size doesn't
match the model) or when only the shape matters. `export_stl` is still the
tool for printing.

## Why not by hand

- **Units.** An STL has no unit. SolidWorks writes the user's STL unit (mm
  here); Blender reads metres, so a 90 mm lid comes in 90 m wide.
- **Up axis.** SolidWorks' views are +Y up, Blender's +Z. Blender's glTF
  importer turns +Y up into +Z up; its STL importer turns nothing. So the
  same part stands in one format and lies on its back in the other, and a
  Z-up part the other way round. The manifest's `blender_matrix` is the one
  turn on the root that fixes both.
- **Placement.** By default SolidWorks moves an STL so every coordinate is
  positive; two parts exported that way don't line up.
- **Paths.** `"C:\Users\..."` in Python code is a `SyntaxError` (`\U` is an
  escape). `blender_code` passes the manifest path with forward slashes.

## The manifest

`<name>.blender.json`, next to the mesh: the source document, the file and
its units, `import` (the Blender operator, and `global_scale` for an STL),
`up_axis`, `blender_matrix` (row-major 4 x 4 for the root), `expected_size_mm`
(along Blender's X, Y, Z), and for a GLB its `parts`, `materials` and
`units_check`. Read it to script more in Blender (a turntable, an exploded
view by part name) without asking SolidWorks again.

## Known limits

- Experimental: not yet checked on real SolidWorks. Whether SolidWorks 2026
  writes GLB in metres and in its own axes, and with appearances, is what
  `tests/scripts/probe_blender_handoff.py` checks.
- Hidden and suppressed components: what SolidWorks' GLB exporter does with
  them is not known yet. Check `parts` in the reply.
- One manifest per name: a GLB and an STL export with the same name share
  `<name>.blender.json`, and the last export wins.
