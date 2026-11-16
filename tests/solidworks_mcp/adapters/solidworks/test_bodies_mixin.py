"""``SolidWorksBodiesMixin``: the part check, the COM boundary, and each method's call."""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import bodies as bd
from solidworks_mcp.adapters.solidworks.clearance import InvalidRequest

from .fake_bodies import Harness, World

_CALLS = [
    ("list_bodies", "list_bodies", (True,), (True,)),
    (
        "insert_part",
        "insert_part",
        ("C:/h.sldprt", "Default", [0, 1, 0], 90.0, "y", [0, 0, 0], True),
        ("C:/h.sldprt", "Default", [0, 1, 0], 90.0, "y", [0, 0, 0], True),
    ),
    ("combine_bodies", "combine_bodies", ("add", "A", ["B"]), ("add", "A", ["B"])),
    (
        "move_copy_body",
        "move_bodies",
        (["A"], [1, 0, 0], 0.0, "z", None, True, 2),
        (["A"], [1, 0, 0], 0.0, "z", None, True, 2),
    ),
    ("delete_keep_body", "delete_keep_body", (["A"], True), (["A"], True)),
    ("split_body", "split_body", ("Top", None, "A"), ("Top", None, "A")),
    ("body_clearance", "body_clearance", ("A", "B"), ("A", "B")),
    (
        "make_mating_negative",
        "make_mating_negative",
        ("Lid", ["L1"], 0.15, 16, "z", "indent"),
        ("Lid", ["L1"], 0.15, 16, "z", "indent"),
    ),
]


@pytest.mark.parametrize(("method", "function", "given", "passed"), _CALLS)
async def test_each_method_runs_its_operation_on_the_part(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    function: str,
    given: tuple[Any, ...],
    passed: tuple[Any, ...],
) -> None:
    seen: list[tuple[Any, ...]] = []

    def operation(adapter: Any, *args: Any) -> dict[str, Any]:
        seen.append(args)
        return {"ran": function}

    monkeypatch.setattr(bd, function, operation)
    harness = Harness(World())

    result = await getattr(harness, method)(*given)

    assert result.is_success, result.error
    assert result.data == {"ran": function}
    # move_bodies takes the model first.
    assert seen == [
        ((harness.currentModel, *passed) if function == "move_bodies" else passed)
    ]


async def test_errors_read_without_the_operation_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_args: Any) -> dict[str, Any]:
        raise InvalidRequest("body_a and body_b are both 'A'")

    monkeypatch.setattr(bd, "body_clearance", refuse)

    result = await Harness(World()).body_clearance("A", "A")

    assert result.error == "Invalid request: body_a and body_b are both 'A'"


@pytest.mark.parametrize(
    ("doc_type", "kind"), [(2, "assembly"), (3, "drawing"), (None, "unknown")]
)
async def test_another_document_is_the_wrong_type(doc_type: Any, kind: str) -> None:
    world = World()
    world.doc_type = doc_type

    result = await Harness(world).list_bodies()

    assert result.error == (
        f"list_bodies needs an active part document (active document is {kind})."
    )


async def test_it_needs_an_active_model() -> None:
    result = await Harness(None).split_body("Top")

    assert result.error == "No active model"


async def test_the_whole_chain_on_the_fake_part() -> None:
    world = World({"A": [0, 0, 0, 10, 10, 10], "B": [20, 0, 0, 30, 10, 10]})

    result = await Harness(world).list_bodies()

    assert result.is_success and result.data["count"] == 2
