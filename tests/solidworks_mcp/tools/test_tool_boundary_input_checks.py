"""Checking a tool's input without running it (``input_model``, ``input_problems``).

``execute_workflow`` checks every step's arguments before any step runs: 17
runs in the 2026-11-20 sample stopped part-way on ``invalid_input`` or
``not_found``. The check is the boundary's own, so a step it passes is one the
tool takes; a ``$steps`` reference, known only when the step runs, stands in
for any value.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from solidworks_mcp.tools.modeling import CreateAssemblyInput
from solidworks_mcp.tools.tool_boundary import (
    input_model,
    input_problems,
    tool_boundary,
)


class _Payload(BaseModel):
    depth: float
    name: str = "x"


class _Range(BaseModel):
    low: int = 0
    high: int = 10

    def model_post_init(self, _context: Any) -> None:
        if self.high < self.low:
            raise ValueError("high must not be below low")


# ---- input_model ---------------------------------------------------------------------


def test_the_input_model_is_the_first_parameters() -> None:
    async def body(input_data: _Payload, ctx: Any = None) -> dict[str, Any]:
        return {}

    assert input_model(body) is _Payload
    assert input_model(tool_boundary(body)) is _Payload


def test_a_tool_without_an_input_model_has_none() -> None:
    async def no_input(*, document: str | None = None) -> dict[str, Any]:
        return {}

    async def a_mapping(input_data: dict[str, Any]) -> dict[str, Any]:
        return {}

    assert input_model(no_input) is None
    assert input_model(a_mapping) is None


# ---- input_problems -------------------------------------------------------------------


def test_good_arguments_have_no_problem() -> None:
    assert input_problems(_Payload, {"depth": 5}) is None


def test_the_problems_name_their_fields() -> None:
    assert input_problems(_Payload, {"depth": "deep", "name": 3}) == (
        "depth: Input should be a valid number, unable to parse string as a "
        "number; name: Input should be a valid string"
    )


def test_a_field_whose_value_comes_later_is_not_checked() -> None:
    later = {"depth": "$steps.0.extrusion.depth"}

    assert input_problems(_Payload, later, ["depth"]) is None
    # The other fields still are.
    assert input_problems(_Payload, {**later, "name": 3}, ["depth"]) == (
        "name: Input should be a valid string"
    )


def test_a_whole_input_check_is_left_to_the_run_when_a_value_comes_later() -> None:
    assert input_problems(_Range, {"low": 50}) == (
        "input: Value error, high must not be below low"
    )
    # `high` comes from an earlier step's reply: the check can't tell yet.
    assert (
        input_problems(_Range, {"low": 50, "high": "$steps.0.high"}, ["high"]) is None
    )


def test_a_removed_input_is_a_problem() -> None:
    problem = input_problems(CreateAssemblyInput, {"name": "A", "components": ["p"]})

    assert problem is not None
    assert problem.startswith("`components` is no longer an input")
