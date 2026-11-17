"""Edge ids that last across rebuilds (``persist_ids``).

The fake ``IModelDocExtension`` hands out a persist reference per edge
(bytes, or a tuple of numbers as pywin32 may give an array of bytes) and
resolves them back, the edge by its reference, as SolidWorks would after a
rebuild; an edge deleted since resolves to nothing.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import persist_ids as pid


class _Edge:
    def __init__(self, name: str) -> None:
        self.name = name

    def GetCurve(self) -> object:  # noqa: N802
        return object()


class _Face:
    def GetCurve(self) -> None:  # noqa: N802
        return None


def _value(variant: Any) -> Any:
    return getattr(variant, "value", variant)


class _Extension:
    def __init__(self, entities: dict[bytes, Any], as_tuple: bool = False) -> None:
        self.entities = entities
        self._as_tuple = as_tuple
        self.codes: list[Any] = []

    def GetPersistReference3(self, entity: Any) -> Any:  # noqa: N802
        key = next(k for k, v in self.entities.items() if v is entity)
        return tuple(key) if self._as_tuple else key

    def GetObjectFromPersistReference3(self, ref: Any, code: Any) -> Any:  # noqa: N802
        self.codes.append(code)
        found = self.entities.get(bytes(_value(ref)))
        return (found, 0) if self._as_tuple and found is not None else found


class _Adapter:
    def _attempt(self, operation: Any, default: Any = None) -> Any:
        try:
            return operation()
        except Exception:  # noqa: BLE001
            return default


def _model(extension: _Extension) -> Any:
    return SimpleNamespace(Extension=extension)


@pytest.mark.parametrize("as_tuple", [False, True])
def test_ids_go_out_and_come_back(as_tuple: bool) -> None:
    a, b = _Edge("a"), _Edge("b")
    extension = _Extension({b"\x01ref-a": a, b"\x02ref-b": b}, as_tuple)
    model = _model(extension)

    ids = pid.persist_ids(_Adapter(), model, [b, a])
    found, missing = pid.resolve(_Adapter(), model, ids)

    assert ids == [
        base64.b64encode(b"\x02ref-b").decode(),
        base64.b64encode(b"\x01ref-a").decode(),
    ]
    assert found == [b, a] and missing == []
    assert len(extension.codes) == 2  # an out-parameter for each


def test_ids_that_name_nothing_or_a_face_are_missing() -> None:
    extension = _Extension({b"edge": _Edge("a"), b"face": _Face()})
    ids = [base64.b64encode(t).decode() for t in (b"edge", b"gone", b"face")]

    found, missing = pid.resolve(_Adapter(), _model(extension), ids)

    assert len(found) == 1
    assert missing == [1, 2]
    assert pid.missing_ids_message(missing).startswith(
        "edge_ids 1, 2 no longer name an edge of the part"
    )


def test_an_unreadable_reference_has_no_id() -> None:
    extension = _Extension({})

    assert pid.persist_ids(_Adapter(), _model(extension), [_Edge("x")]) == [None]


@pytest.mark.parametrize(
    ("raw", "text"),
    [
        (b"\x00\xff", "AP8="),
        (bytearray(b"ab"), "YWI="),
        (memoryview(b"ab"), "YWI="),
        ((97, 98), "YWI="),
        ((-1,), "/w=="),
        (None, None),
        (b"", None),
        (("x",), None),
    ],
)
def test_references_as_text(raw: Any, text: str | None) -> None:
    assert pid.encode(raw) == text


@pytest.mark.parametrize(
    ("ids", "fragment"),
    [
        ([], "edge_ids must be a list of 1 to 500"),
        ("AP8=", "edge_ids must be a list"),
        (["AP8=", 5], "edge_ids[1] must be an id string"),
        (["not base64!"], "edge_ids[0]: 'not base64!' is not an edge id"),
        ([""], "edge_ids[0]: '' is not an edge id"),
    ],
)
def test_bad_ids_are_explained(ids: Any, fragment: str) -> None:
    assert fragment in (pid.ids_problem(ids) or "")


def test_good_ids_pass() -> None:
    assert pid.ids_problem(["AP8=", " YWI= "]) is None
    assert pid.decode(" YWI= ") == b"ab"
