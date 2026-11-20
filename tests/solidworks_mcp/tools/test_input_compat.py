"""Tests for the shared input base, the removed-inputs and alias tables, and the
call shapes put right before validation."""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from pydantic import AliasChoices, BaseModel, Field, ValidationError

from solidworks_mcp.tools.input_compat import (
    INPUT_ALIASES,
    REMOVED_INPUTS,
    CompatInput,
    accepted_names,
    aliases_for,
    one_or_many,
    removed_inputs,
    rename_aliases,
    reshape_arguments,
)


class _Thing(CompatInput):
    name: str


def test_removed_inputs_name_each_field_sent_with_its_pointer(monkeypatch) -> None:
    monkeypatch.setitem(REMOVED_INPUTS, "_Thing", {"old": "use `new`", "gone": "x"})

    sent = _Thing(name="a", old=1, other=2)

    assert removed_inputs(sent) == ["`old` is no longer an input: use `new`"]
    assert removed_inputs(_Thing(name="a")) == []


def test_a_model_without_removed_inputs_is_never_refused() -> None:
    class _Free(CompatInput):
        pass

    assert removed_inputs(_Free(anything=1)) == []


def test_every_removed_field_is_really_gone_from_its_model() -> None:
    """A field still declared would never reach model_extra, so never be refused."""
    from importlib import import_module
    from pkgutil import iter_modules

    import solidworks_mcp.tools as tools_pkg

    models: dict[str, type] = {}
    for info in iter_modules(tools_pkg.__path__):
        module = import_module(f"solidworks_mcp.tools.{info.name}")
        for value in vars(module).values():
            if isinstance(value, type) and issubclass(value, CompatInput):
                models[value.__name__] = value
    for model_name, fields in REMOVED_INPUTS.items():
        assert model_name in models, model_name
        assert not set(fields) & set(models[model_name].model_fields), model_name


def _models() -> dict[str, type]:
    """Every input model of the tool modules, by name."""
    from importlib import import_module
    from pkgutil import iter_modules

    import solidworks_mcp.tools as tools_pkg

    found: dict[str, type] = {}
    for info in iter_modules(tools_pkg.__path__):
        module = import_module(f"solidworks_mcp.tools.{info.name}")
        for value in vars(module).values():
            if isinstance(value, type) and issubclass(value, BaseModel):
                found.setdefault(value.__name__, value)
    return found


# ---- aliases ------------------------------------------------------------------------


def test_every_alias_names_a_real_field_and_is_not_one() -> None:
    models = _models()
    for model_name, aliases in INPUT_ALIASES.items():
        assert model_name in models, model_name
        fields = set(models[model_name].model_fields)
        assert not set(aliases) & fields, model_name
        assert set(aliases.values()) <= fields, model_name


@pytest.mark.parametrize(
    ("model", "given", "field", "value"),
    [
        ("GetSketchInput", {"sketch_name": "Sketch3"}, "name", "Sketch3"),
        ("DescribeToolInputsInput", {"tool_names": ["save_as"]}, "tools", ["save_as"]),
        ("ActivateDocumentInput", {"name": "Lid"}, "title_or_path", "Lid"),
        (
            "ActivateDocumentInput",
            {"path": "C:/Lid.SLDPRT"},
            "title_or_path",
            "C:/Lid.SLDPRT",
        ),
        ("VerifyModelInput", {"expected_volume_mm3": 5.0}, "volume_mm3", 5.0),
        ("VerifyModelInput", {"expected_mate_count": 2}, "mate_count", 2),
    ],
)
def test_a_habitual_name_reaches_its_field(
    model: str, given: dict[str, Any], field: str, value: Any
) -> None:
    validated = _models()[model].model_validate(given)

    assert getattr(validated, field) == value
    assert not set(given) & set(validated.model_extra or {})


def test_an_alias_beside_its_field_must_agree() -> None:
    sketch = _models()["GetSketchInput"]

    same = sketch.model_validate({"name": "Sketch3", "sketch_name": "Sketch3"})
    with pytest.raises(ValidationError, match="`sketch_name` is another name for"):
        sketch.model_validate({"name": "Sketch3", "sketch_name": "Sketch4"})
    with pytest.raises(ValidationError, match="give only `title_or_path`"):
        _models()["ActivateDocumentInput"].model_validate(
            {"name": "Lid", "title": "Base"}
        )

    assert same.name == "Sketch3"


def test_one_or_many_leaves_what_is_not_a_mapping() -> None:
    assert one_or_many(["a = 1"], "equations", "equation") == ["a = 1"]
    assert one_or_many({"equation": "a = 1"}, "equations", "equation") == {
        "equations": ["a = 1"]
    }


def test_aliases_are_inherited_and_left_alone_where_not_listed(monkeypatch) -> None:
    class _Base(CompatInput):
        wanted: str = ""

    class _Child(_Base):
        pass

    monkeypatch.setitem(INPUT_ALIASES, "_Base", {"old": "wanted"})

    assert aliases_for(_Child) == {"old": "wanted"}
    assert _Child(old="x").wanted == "x"
    assert rename_aliases(_Child, ["not", "a", "dict"]) == ["not", "a", "dict"]
    data = {"wanted": "y"}
    assert rename_aliases(_Child, data) is data


# ---- flat arguments -------------------------------------------------------------


class _Open(BaseModel):
    file_path: str
    read_only: bool = False
    copy_: bool = Field(default=False, alias="copy")
    either: str = Field(default="", validation_alias=AliasChoices("either", "or_this"))


class _Nothing(BaseModel):
    detail: str = "full"


async def _open_tool(input_data: _Open, *, document: str | None = None) -> None:
    """A tool taking one model, and the boundary's ``document``."""


async def _list_tool(input_data: _Nothing, *, document: str | None = None) -> None:
    """A tool whose model needs nothing."""


async def _optional_tool(input_data: _Nothing | None = None) -> None:
    """A tool whose input is optional."""


async def _bare_tool(*, document: str | None = None) -> None:
    """A tool with no input model."""


async def _dict_tool(input_data: dict[str, Any]) -> None:
    """A tool whose input is any mapping."""


def test_accepted_names_are_fields_aliases_and_removed_inputs(monkeypatch) -> None:
    monkeypatch.setitem(REMOVED_INPUTS, "_Open", {"format": "use the extension"})
    monkeypatch.setitem(INPUT_ALIASES, "_Open", {"path": "file_path"})

    assert accepted_names(_Open) == {
        "file_path",
        "read_only",
        "copy_",
        "copy",
        "either",
        "or_this",
        "path",
        "format",
    }


def test_flat_fields_are_wrapped_and_the_tools_own_keys_stay() -> None:
    flat = {"file_path": "C:/a.SLDPRT", "document": "Lid"}

    assert reshape_arguments(_open_tool, flat) == {
        "document": "Lid",
        "input_data": {"file_path": "C:/a.SLDPRT"},
    }
    assert reshape_arguments(_open_tool, {"or_this": "x", "copy": True}) == {
        "input_data": {"or_this": "x", "copy": True}
    }


def test_an_unknown_key_leaves_the_call_for_validation_to_refuse() -> None:
    typo = {"file_pth": "C:/a.SLDPRT"}

    assert reshape_arguments(_open_tool, typo) is typo


def test_a_missing_input_is_added_only_when_nothing_is_needed() -> None:
    only_document = {"document": "Lid"}
    empty: dict[str, Any] = {}

    assert reshape_arguments(_list_tool, only_document) == {
        "document": "Lid",
        "input_data": {},
    }
    assert reshape_arguments(_open_tool, empty) is empty  # file_path is needed
    assert reshape_arguments(_optional_tool, empty) is empty  # has a default


def test_input_data_as_json_text_is_parsed() -> None:
    assert reshape_arguments(
        _open_tool, {"input_data": '{"file_path": "C:/a.SLDPRT"}'}
    ) == {"input_data": {"file_path": "C:/a.SLDPRT"}}
    text = {"input_data": "C:/a.SLDPRT"}
    assert reshape_arguments(_open_tool, text) is text
    assert reshape_arguments(_open_tool, {"input_data": "[1, 2]"}) == {
        "input_data": "[1, 2]"
    }


def test_a_field_beside_input_data_is_moved_into_it() -> None:
    # execute_workflow's `detail` beside its input_data failed validation.
    wrapped = {
        "input_data": {"file_path": "C:/a.SLDPRT"},
        "read_only": True,
        "document": "Lid",
    }

    assert reshape_arguments(_open_tool, wrapped) == {
        "document": "Lid",
        "input_data": {"file_path": "C:/a.SLDPRT", "read_only": True},
    }
    assert reshape_arguments(
        _open_tool, {"input_data": '{"file_path": "C:/a.SLDPRT"}', "copy": True}
    ) == {"input_data": {"file_path": "C:/a.SLDPRT", "copy": True}}
    same = {"input_data": {"file_path": "a", "read_only": True}, "read_only": True}
    assert reshape_arguments(_open_tool, same) == {
        "input_data": {"file_path": "a", "read_only": True}
    }


def test_a_field_beside_input_data_that_conflicts_or_is_unknown_is_left() -> None:
    conflict = {"input_data": {"file_path": "a", "read_only": False}, "read_only": True}
    unknown = {"input_data": {"file_path": "a"}, "readonly": True}
    not_a_model = {"input_data": "[1, 2]", "read_only": True}
    any_mapping = {"input_data": {"x": 1}, "y": 2}

    assert reshape_arguments(_open_tool, conflict) is conflict
    assert reshape_arguments(_open_tool, unknown) is unknown
    assert reshape_arguments(_open_tool, not_a_model) is not_a_model
    assert reshape_arguments(_dict_tool, any_mapping) is any_mapping


@pytest.mark.parametrize("empty", [{}, "{}", "", None])
def test_an_empty_input_to_a_tool_without_one_is_dropped(empty: Any) -> None:
    assert reshape_arguments(_bare_tool, {"input_data": empty, "document": "Lid"}) == {
        "document": "Lid"
    }


def test_a_tool_without_a_model_input_is_left_alone() -> None:
    given = {"input_data": {"x": 1}}
    flat = {"x": 1}

    assert reshape_arguments(_bare_tool, given) is given
    assert reshape_arguments(_dict_tool, flat) is flat
    assert "input_data" in inspect.signature(_dict_tool).parameters
