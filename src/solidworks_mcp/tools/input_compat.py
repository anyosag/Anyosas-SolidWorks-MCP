"""Shared input model compatibility helpers for tool schemas.

This module centralizes test/backward-compat parsing behavior so individual tool modules
stay focused on domain fields. It also accepts the call shapes agents get wrong
when a tool's schema isn't in their prompt (speed report, section 13): field
names they use by habit (:data:`INPUT_ALIASES`) and a model's fields sent
without their ``input_data`` wrapper (:func:`reshape_arguments`).
"""

import inspect
import json
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, model_validator


class CompatInput(BaseModel):
    """Base schema allowing legacy/extra fields used by existing tests.

    Attributes:
        model_config (Any): The model config value.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _input_aliases(cls, data: Any) -> Any:
        """Rename the field names clients send by habit (:data:`INPUT_ALIASES`)."""
        return rename_aliases(cls, data)


#: Field names clients send by habit, by input model name: each maps to the
#: field it means. Applied before validation (:func:`rename_aliases`), for a
#: model and the models it inherits from; not advertised in the schemas.
INPUT_ALIASES: dict[str, dict[str, str]] = {
    "GetSketchInput": {"sketch_name": "name"},
    "DescribeToolInputsInput": {"tool_names": "tools"},
    "ActivateDocumentInput": {
        "name": "title_or_path",
        "title": "title_or_path",
        "path": "title_or_path",
        "file_path": "title_or_path",
    },
    "GeometryExpectations": {
        "expected_volume_mm3": "volume_mm3",
        "expected_bounding_box_mm": "bounding_box_mm",
        "expected_solid_body_count": "solid_body_count",
    },
    "VerifyModelInput": {
        "expected_mass_kg": "mass_kg",
        "expected_feature_count": "feature_count",
        "expected_component_count": "component_count",
        "expected_mate_count": "mate_count",
    },
}


def aliases_for(model: type) -> dict[str, str]:
    """The input aliases of ``model`` and of the models it inherits from."""
    merged: dict[str, str] = {}
    for klass in reversed(model.__mro__):
        merged.update(INPUT_ALIASES.get(klass.__name__, {}))
    return merged


def rename_aliases(model: type, data: Any) -> Any:
    """``data`` with the aliases :data:`INPUT_ALIASES` lists for ``model`` renamed.

    For a ``mode="before"`` model validator. An alias given beside its field
    with the same value is dropped; with another value the input is
    ambiguous, and it is refused.

    Args:
        model: The input model being validated.
        data: The raw input.

    Returns:
        Any: ``data``, renamed where an alias was used.

    Raises:
        ValueError: An alias and its field (or two aliases of one field) give
            different values.
    """
    if not isinstance(data, dict):
        return data
    aliases = aliases_for(model)
    if aliases.keys().isdisjoint(data):
        return data
    data = dict(data)
    for alias, field in aliases.items():
        if alias not in data:
            continue
        value = data.pop(alias)
        if field in data and data[field] != value:
            raise ValueError(
                f"`{alias}` is another name for `{field}`, and the two were given "
                f"different values; give only `{field}`"
            )
        data[field] = value
    return data


def one_or_many(data: Any, plural: str, singular: str) -> Any:
    """Let a list field take one item: ``{singular: x}`` or ``{plural: x}``.

    For a ``mode="before"`` model validator. A bare value under either key
    becomes ``{plural: [x]}``; anything else passes through unchanged.

    Args:
        data: The raw input.
        plural: The list field's name (``equations``).
        singular: The name clients send by habit (``equation``).

    Returns:
        Any: ``data`` with ``plural`` holding a list where it could.
    """
    if not isinstance(data, dict):
        return data
    data = dict(data)
    if plural not in data and singular in data:
        data[plural] = data.pop(singular)
    value = data.get(plural)
    if value is not None and not isinstance(value, list | tuple):
        data[plural] = [value]
    return data


_DRAWING_PACKAGE = "use `generate_drawing_package` for a complete drawing"
_FILE_EXTENSION = "the file extension sets the format and its options"
_ACTIVE_MODEL = "it measures the active model; open and activate the one you want"

#: Inputs a tool used to advertise but never applied, by input model name.
#: Each maps to what to use instead. A call that still sends one is refused
#: (``removed_inputs``) rather than silently ignored.
REMOVED_INPUTS: dict[str, dict[str, str]] = {
    "CreateExtrusionInput": {"direction": "use `end_condition`"},
    "CreateRevolveInput": {"direction": "use `both_directions`"},
    "CreateAssemblyInput": {
        "components": "call `insert_component` for each part",
    },
    "CreateDrawingInput": {
        "model_path": "place views with `create_drawing_view`, or " + _DRAWING_PACKAGE,
        "sheet_format": _DRAWING_PACKAGE,
    },
    "DrawingCreationInput": {
        "template": _DRAWING_PACKAGE,
        "sheet_size": _DRAWING_PACKAGE,
        "sheet_format": _DRAWING_PACKAGE,
        "title": _DRAWING_PACKAGE,
        "output_path": "save the drawing with `save_file`",
        "scale": "set it afterwards with `set_sheet_scale`",
        "auto_populate_views": "the tool always places the three standard views",
    },
    "SaveFileInput": {"force_save": "the tool always saves"},
    "WorkflowInput": {
        "parallel_execution": (
            "steps run one at a time, in order: SolidWorks takes one call at a time"
        ),
        "error_handling": (
            "it stops at the first step that fails; `rollback_on_error` also "
            "removes what the earlier steps added"
        ),
    },
    "PatternCircularInput": {
        "equal_spacing": (
            "give `angle` for the total spread (evenly spaced) or `step_angle` "
            "for the degrees between instances"
        ),
    },
    "ExportFileInput": {"options": _FILE_EXTENSION},
    "InterferenceCheckInput": {
        "tolerance": "the check has no tolerance",
        "check_all_components": "it checks the whole assembly, or `components`",
    },
    "MacroPlaybackInput": {
        "parameters": "the macro takes no arguments; have it read a file you write",
        "execution_parameters": "the macro takes no arguments; have it read a file",
        "target_file": "open the document first with `open_model`",
        "execution_mode": "the macro runs in the running SolidWorks session",
        "pause_on_error": "it stops at the first run that fails",
        "log_execution": "the reply reports every run",
    },
    "MassPropertiesInput": {
        "model_path": _ACTIVE_MODEL,
        "units": "volumes are mm³, masses kg and inertia kg·mm²",
        "include_hidden": _ACTIVE_MODEL,
        "reference_coordinate_system": "it reports about the centre of mass",
    },
}


def removed_inputs(model: BaseModel) -> list[str]:
    """The removed inputs ``model`` was given, each with what to use instead.

    Args:
        model: A validated tool input.

    Returns:
        list[str]: One sentence per removed field present, e.g.
        "`components` is no longer an input: call `insert_component` for each
        part". Empty when none was sent.
    """
    removed = REMOVED_INPUTS.get(type(model).__name__, {})
    extras = model.model_extra or {}
    return [
        f"`{field}` is no longer an input: {pointer}"
        for field, pointer in removed.items()
        if field in extras
    ]


#: The parameter a tool's single input model comes in.
_INPUT_DATA = "input_data"


def accepted_names(model: type[BaseModel]) -> set[str]:
    """Every name ``model`` takes: its fields, their aliases and its removed inputs.

    A removed input counts so that the tool, not validation, refuses it, with
    what to use instead (:func:`removed_inputs`).
    """
    names = set(model.model_fields)
    for field in model.model_fields.values():
        if field.alias:
            names.add(field.alias)
        choices = field.validation_alias
        if isinstance(choices, str):
            names.add(choices)
        elif isinstance(choices, AliasChoices):
            names.update(c for c in choices.choices if isinstance(c, str))
    names.update(aliases_for(model))
    names.update(REMOVED_INPUTS.get(model.__name__, {}))
    return names


def _json_object(value: Any) -> dict[str, Any] | None:
    """``value`` as a dict when it is one, or a JSON text of one; else None."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _fold_fields_beside(
    fn: Callable[..., Any],
    arguments: dict[str, Any],
    parameters: Mapping[str, inspect.Parameter],
) -> dict[str, Any]:
    """Model fields sent beside ``input_data``, moved into it (see :func:`reshape_arguments`).

    ``execute_workflow`` with ``"detail"`` beside its ``input_data`` failed
    validation ("unexpected keyword argument") although ``detail`` is one of
    its fields; the agent had to retry with it inside.
    """
    from .tool_boundary import model_parameters  # tool_boundary imports this module

    inner = arguments[_INPUT_DATA]
    beside = {
        k: v for k, v in arguments.items() if k != _INPUT_DATA and k not in parameters
    }
    model = model_parameters(fn).get(_INPUT_DATA)
    if not beside or not isinstance(inner, dict) or model is None:
        return arguments
    if not set(beside) <= accepted_names(model):
        return arguments
    if any(key in inner and inner[key] != value for key, value in beside.items()):
        return arguments
    own = {k: v for k, v in arguments.items() if k in parameters and k != _INPUT_DATA}
    return {**own, _INPUT_DATA: {**inner, **beside}}


def reshape_arguments(
    fn: Callable[..., Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    """A tool call's arguments with the common shape mistakes put right.

    For a tool whose input is one ``input_data`` model (FastMCP validates
    the call against the tool function's signature):

    * its fields sent at the top level are wrapped, ``{"file_path": p}``
      becoming ``{"input_data": {"file_path": p}}``, but only when every
      other key is a name the model takes (:func:`accepted_names`): a key it
      doesn't know leaves the call as it was, for validation to refuse;
    * a missing ``input_data`` becomes ``{}`` when the model needs nothing;
    * ``input_data`` sent as the JSON text of an object is parsed.

    * a model field sent beside ``input_data`` (``{"input_data": {"steps":
      [...]}, "detail": "full"}``) is moved into it, unless ``input_data``
      already holds a different value for it or another key beside it is
      one the model doesn't take: those calls are left for validation.

    A tool that takes no ``input_data`` loses an empty one (``{}``, ``"{}"``,
    ``""`` or null). Keys the tool takes itself (``document``) stay at the
    top level.

    Args:
        fn: The tool function.
        arguments: The call's arguments.

    Returns:
        dict[str, Any]: ``arguments`` itself when nothing applies, else a new
        dict.
    """
    from .tool_boundary import model_parameters  # tool_boundary imports this module

    parameters = inspect.signature(fn).parameters
    if _INPUT_DATA in arguments:
        value = arguments[_INPUT_DATA]
        parsed = _json_object(value)
        if _INPUT_DATA not in parameters:
            if parsed == {} or value is None or value == "":
                return {k: v for k, v in arguments.items() if k != _INPUT_DATA}
            return arguments
        if isinstance(value, str) and parsed is not None:
            arguments = {**arguments, _INPUT_DATA: parsed}
        return _fold_fields_beside(fn, arguments, parameters)
    model = model_parameters(fn).get(_INPUT_DATA)
    if model is None:
        return arguments
    own = {k: v for k, v in arguments.items() if k in parameters}
    flat = {k: v for k, v in arguments.items() if k not in parameters}
    if flat:
        if not set(flat) <= accepted_names(model):
            return arguments
        return {**own, _INPUT_DATA: flat}
    required = any(field.is_required() for field in model.model_fields.values())
    if required or parameters[_INPUT_DATA].default is not inspect.Parameter.empty:
        return arguments
    return {**own, _INPUT_DATA: {}}
