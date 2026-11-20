## ADDED Requirements

### Requirement: A part or a component can be coloured

The adapter SHALL expose `set_appearance(rgb=None, transparency=None, component=None)`, and the server SHALL register a `set_appearance` tool with the same inputs.
- `rgb` is three whole numbers 0..255.
- `transparency` is a number from 0 (solid) to 1 (clear).
- At least one of `rgb` and `transparency` SHALL be given.
- `component` is an instance name as `list_components` reports it, at any level (`frame-1/bolt-1`).

In a part, with no `component`, the colour SHALL be set on the part. In an assembly, `component` is required and the colour SHALL be set on that component in the assembly, in the active configuration. The component's part file SHALL keep its own colour and SHALL NOT become modified.

The reply's `data` SHALL hold `scope` (`part` or `component`), `component` (when given), `rgb` (three whole numbers) and `transparency`, read back from SolidWorks after the write.

#### Scenario: A part made red
- **WHEN** the active document is a part and an agent calls `set_appearance` with `rgb: [200, 30, 30]`
- **THEN** the reply has `scope: "part"`, `rgb: [200, 30, 30]` and the same `transparency` as before, and a picture of the part (`export_image`) shows red pixels where it showed grey

#### Scenario: One component in an assembly
- **WHEN** an assembly holds `base-1` and `lid-1` and an agent calls `set_appearance` with `component: "lid-1"` and `rgb: [30, 90, 200]`
- **THEN** the reply has `scope: "component"` and `component: "lid-1"`, `base-1` keeps its colour, and the part file of `lid-1` is not modified (its save flag stays false)

#### Scenario: Two instances of one part
- **WHEN** `bolt-1` and `bolt-2` are two instances of one file and an agent colours `bolt-1`
- **THEN** `bolt-2` keeps its colour

### Requirement: What is left out stays as it was

A call that gives only `rgb` SHALL keep the transparency, and a call that gives only `transparency` SHALL keep the colour. The other material values (ambient, diffuse, specular, shininess, emission) SHALL NOT change. A component with no colour of its own in the assembly SHALL start from its part's values.

#### Scenario: Only transparency
- **WHEN** a part is red (`[200, 30, 30]`) and an agent calls `set_appearance` with `transparency: 0.5`
- **THEN** the reply has `rgb: [200, 30, 30]` and `transparency` 0.5 within 1/255

#### Scenario: Only the colour
- **WHEN** a component is at transparency 0.5 and an agent calls `set_appearance` with `rgb: [0, 128, 0]`
- **THEN** the reply has `rgb: [0, 128, 0]` and `transparency` 0.5 within 1/255

#### Scenario: A component with no colour of its own
- **WHEN** a component has never been coloured in the assembly and its part is blue, and an agent sets only `transparency: 0.3`
- **THEN** the component is blue at transparency 0.3, and the reply says so

### Requirement: Wrong input is refused before SolidWorks is called

The call SHALL fail with `error_type: invalid_input` when:
- neither `rgb` nor `transparency` is given;
- `rgb` isn't three whole numbers 0..255, or `transparency` isn't 0..1;
- the active document is an assembly and no `component` is given (the message names `list_components`);
- the active document is a part and a `component` is given.

It SHALL fail with `error_type: not_found` when `component` isn't in the assembly, listing the top-level components. It SHALL fail with `error_type: wrong_document_type` for a drawing.

#### Scenario: Colour out of range
- **WHEN** an agent calls `set_appearance` with `rgb: [300, 0, 0]`
- **THEN** the call fails with `error_type: invalid_input` and nothing is sent to SolidWorks

#### Scenario: An assembly without a component
- **WHEN** the active document is an assembly and an agent calls `set_appearance` with `rgb: [0, 0, 0]`
- **THEN** the call fails with `error_type: invalid_input`

### Requirement: A colour SolidWorks didn't take is an error

After the write the adapter SHALL read the values back. If the colour or the transparency differs from the request by more than 1/255, the call SHALL fail, and the message SHALL show what SolidWorks reads.

#### Scenario: A refused write
- **WHEN** SolidWorks keeps the old values after the write
- **THEN** the call fails with a message that shows the values SolidWorks reads, and no success is reported
