"""Edge ids that last: persist references, base64-encoded.

An ``IEdge`` dispatch is good only until the next rebuild. A persist
reference (``IModelDocExtension.GetPersistReference3``, an array of bytes)
names the same edge after rebuilds, while the feature that made it stays.
``select_edges`` with ``with_ids`` gives one per selected edge, as base64
text; ``edge_ids`` on ``select_edges`` (and through it ``add_fillet`` and
``add_chamfer``) takes them back with
``IModelDocExtension.GetObjectFromPersistReference3``, whose error code is a
by-reference out-parameter (a ``VT_BYREF | VT_I4`` VARIANT, pitfall #25).

Not run on SolidWorks yet: ``tests/scripts/probe_dipper_edges.py`` (step
``persist``) reads ids, rebuilds and changes the part, and resolves them.
"""

from __future__ import annotations

import base64
from typing import Any

from .com_utils import as_list
from .com_utils import attempt as _attempt
from .com_utils import flag as _flag

#: The most ids ``with_ids`` gives in one reply (each is a few hundred
#: characters); a larger selection is refused before anything is selected.
MAX_IDS_OUT = 100

#: The most ids one ``edge_ids`` takes.
MAX_IDS_IN = 500

#: ``pythoncom.VT_UI1``: an id goes back to SolidWorks as an array of bytes.
_VT_UI1 = 17


def encode(raw: Any) -> str | None:
    """A persist reference as base64 text, or None when it is empty or unreadable.

    pywin32 gives an array of bytes as ``bytes``/``memoryview`` or as a tuple
    of numbers.
    """
    if raw is None:
        return None
    if isinstance(raw, bytes | bytearray | memoryview):
        data = bytes(raw)
    else:
        try:
            data = bytes(int(b) & 0xFF for b in as_list(raw))
        except (TypeError, ValueError):
            return None
    return base64.b64encode(data).decode("ascii") if data else None


def decode(text: str) -> bytes:
    """The bytes of an id.

    Raises:
        ValueError: The text is not base64, or is empty.
    """
    try:
        data = base64.b64decode(text.strip(), validate=True)
    except ValueError:  # binascii.Error is one
        raise ValueError(f"{text!r} is not an edge id") from None
    if not data:
        raise ValueError(f"{text!r} is not an edge id")
    return data


def ids_problem(ids: Any) -> str | None:
    """What is wrong with an ``edge_ids`` list, or None."""
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_IDS_IN:
        return f"edge_ids must be a list of 1 to {MAX_IDS_IN} ids from select_edges."
    for index, text in enumerate(ids):
        if not isinstance(text, str):
            return f"edge_ids[{index}] must be an id string from select_edges."
        try:
            decode(text)
        except ValueError as exc:
            return f"edge_ids[{index}]: {exc}; use the ids select_edges gave."
    return None


def missing_ids_message(positions: list[int]) -> str:
    """The error for ids that no longer name an edge of the part."""
    listed = ", ".join(str(p) for p in positions)
    return (
        f"edge_ids {listed} no longer name an edge of the part: the feature that "
        "made it was deleted or suppressed, or an edit replaced the edge. Select "
        "the edges again with select_edges (with_ids for new ids)."
    )


def _extension(adapter: Any, model: Any) -> Any:
    extension = _attempt(adapter, lambda: model.Extension)
    _flag(
        adapter,
        extension,
        "GetPersistReference3",
        "GetObjectFromPersistReference3",
    )
    return extension


def persist_ids(adapter: Any, model: Any, entities: list[Any]) -> list[str | None]:
    """An id for each entity (``GetPersistReference3``); None where it doesn't read."""
    extension = _extension(adapter, model)
    return [
        encode(_attempt(adapter, lambda e=entity: extension.GetPersistReference3(e)))
        for entity in entities
    ]


def _bytes_variant(data: bytes) -> Any:
    """``VARIANT(VT_ARRAY | VT_UI1, data)``; the bytes as a list without pywin32."""
    from .io import _variant_array

    return _variant_array(_VT_UI1, list(data))


def _byref_code() -> Any:
    from .io import _byref_int

    return _byref_int()


def resolve(adapter: Any, model: Any, ids: list[str]) -> tuple[list[Any], list[int]]:
    """The edges ``ids`` name now, and the positions of those that name none.

    An id resolves when ``GetObjectFromPersistReference3`` gives an object
    with a curve (``IEdge.GetCurve``): a face's id is not an edge.
    """
    extension = _extension(adapter, model)
    found: list[Any] = []
    missing: list[int] = []
    for position, text in enumerate(ids):
        code = _byref_code()
        raw = _attempt(
            adapter,
            lambda t=text, c=code: extension.GetObjectFromPersistReference3(
                _bytes_variant(decode(t)), c
            ),
        )
        # With the out-parameter given back, pywin32 may return (object, code).
        if isinstance(raw, tuple | list):
            raw = raw[0] if raw else None
        _flag(adapter, raw, "GetCurve")
        if raw is None or _attempt(adapter, lambda r=raw: r.GetCurve()) is None:
            missing.append(position)
        else:
            found.append(raw)
    return found, missing
