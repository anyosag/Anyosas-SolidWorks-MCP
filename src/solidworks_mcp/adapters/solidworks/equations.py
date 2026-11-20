"""Equations and global variables, through ``IEquationMgr`` (``parametric-equations``).

SolidWorks keeps design intent such as "every leg is ``L`` long" in equations
(``"D1@Sketch1" = "L"``) and global variables (``"L" = 40``), on
``IModelDoc2.GetEquationMgr()``. Three operations:

- :func:`list_all`: every equation with its ``index``, ``equation`` text,
  ``name`` (what it sets), ``value``, ``global`` and ``broken``.
- :func:`set_equations`: a list of equations, all or nothing (D4). Every left
  side is checked first; an equation whose name already has one replaces it
  in place, any other is added at the end; a refusal undoes the journal; one
  ``ForceRebuild3(False)`` at the end.
- :func:`delete_equation`: one equation, by the name it sets or its index.

:func:`driven_map` gives ``list_model_dimensions`` its ``driven_by`` and
``set_dimension`` its up-front refusal (D7).

What the API and the gen_py wrapper say:

- ``Equation``, ``Value`` and ``GlobalVariable`` are indexed properties.
  Late binding reads them through ``IDispatch.Invoke`` with
  ``DISPATCH_PROPERTYGET`` (``ref_plane_edit.get_indexed``); gen_py's
  ``SetEquation`` is ``Equation``'s property put (DISPID 8), written through
  ``put_indexed``. Probe 1.1 settles which call works.
- ``Add3(index, text, solve, swAllConfiguration, names)`` returns the new
  index or -1; ``Add2(index, text, solve)`` is the fallback where ``Add3``
  is missing. Both apply to every configuration (D5).
- A refused ``SetEquation`` keeps the old text without an error, so every
  replace is read back.

Credit: hjbaard's ``solidworks-mcp`` (MIT, (c) 2026 Henk Jan Baard),
``session.py`` ``set_equations``, is the reference for the call order and the
undo journal. This code is written fresh.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

from .. import sw_type_info as _sw_type_info
from .ref_plane_edit import get_indexed, put_indexed

#: ``swInConfigurationOpts_e.swAllConfiguration``.
ALL_CONFIGURATIONS = 2

#: ``swUserPreferenceIntegerValue_e.swUnitsLinear``.
_UNITS_LINEAR = 47

#: ``swLengthUnit_e`` -> the unit's short name.
_LENGTH_UNITS = {
    0: "mm",
    1: "cm",
    2: "m",
    3: "in",
    4: "ft",
    5: "ft-in",
    6: "angstrom",
    7: "nm",
    8: "um",
    9: "mil",
    10: "uin",
}

#: Quoted words that are feature states, not names (``"Cut1" = "suppressed"``).
_STATES = frozenset({"suppressed", "unsuppressed"})

_EXAMPLE = '"W" = 40'
_QUOTED = re.compile(r'"([^"]*)"')
_LEFT = re.compile(r'^\s*"([^"]+)"\s*=')

_MANAGER_METHODS = (
    "GetCount",
    "Add2",
    "Add3",
    "Delete",
    "SetEquationAndConfigurationOption",
)


# ----------------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------------


def equation_names(text: Any) -> list[str]:
    """Every quoted name in ``text``, in order: ``["D1@Boss", "D1@Sketch2"]``."""
    if not isinstance(text, str):
        return []
    return [name.strip() for name in _QUOTED.findall(text) if name.strip()]


def _suggest(text: Any) -> str:
    """``W = 40`` written the way SolidWorks takes it: ``"W" = 40``."""
    if isinstance(text, str) and "=" in text:
        left, right = text.split("=", 1)
        name = left.strip().strip('"').strip()
        if name and right.strip():
            return f'"{name}" = {right.strip()}'
    return _EXAMPLE


def left_side(text: Any) -> str:
    """The name an equation sets: ``W`` from ``"W" = 40``.

    Raises:
        ValueError: ``Invalid request``: no quoted name followed by ``=``.
    """
    match = _LEFT.match(text) if isinstance(text, str) else None
    if match is None:
        raise ValueError(
            f"Invalid request: {text!r} has no quoted name on its left side; "
            f"write it as {_suggest(text)} (names in double quotes)"
        )
    return match.group(1).strip()


def name_of(text: Any) -> str | None:
    """:func:`left_side`, or None for text without one."""
    try:
        return left_side(text)
    except ValueError:
        return None


def right_names(text: Any) -> list[str]:
    """The quoted names an equation reads (its right side)."""
    match = _LEFT.match(text) if isinstance(text, str) else None
    if match is None:
        return []
    return equation_names(text[match.end() :])


def short_name(name: str) -> str:
    """``D1@Sketch1`` from ``D1@Sketch1@Part1.Part`` (the document suffix off)."""
    parts = name.split("@")
    if len(parts) >= 3 and "." in parts[-1]:
        return "@".join(parts[:-1])
    return name


def _key(name: str) -> str:
    return short_name(name).casefold()


def same_equation(a: Any, b: Any) -> bool:
    """Whether two equation texts match, whitespace and case aside."""
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    return "".join(a.split()).casefold() == "".join(b.split()).casefold()


def is_broken(
    names: Iterable[str], variables: Iterable[str], resolves: Callable[[str], bool]
) -> bool:
    """Whether a name an equation reads is neither a variable nor a model parameter.

    Args:
        names: The quoted names on the equation's right side.
        variables: The global variables the equation list sets.
        resolves: Whether the model resolves a name (``IModelDoc2.Parameter``).
    """
    known = {_key(v) for v in variables}
    return any(
        _key(n) not in known and n.casefold() not in _STATES and not resolves(n)
        for n in names
    )


def check_equations(equations: Any) -> list[str]:
    """Every equation's left-side name, before anything changes (D2, D3).

    Raises:
        ValueError: ``Invalid request``: an empty list, a missing quoted left
            side, or two equations that set the same name.
    """
    if isinstance(equations, str):
        equations = [equations]
    if not equations:
        raise ValueError(
            f"Invalid request: give at least one equation, e.g. {_EXAMPLE}"
        )
    names: list[str] = []
    seen: dict[str, str] = {}
    for text in equations:
        name = left_side(text)
        key = _key(name)
        if key in seen:
            raise ValueError(
                f'Invalid request: {seen[key]} and {text} both set "{name}"; '
                "give one equation per name"
            )
        seen[key] = text
        names.append(name)
    return names


# ----------------------------------------------------------------------------
# COM side
# ----------------------------------------------------------------------------


def manager(adapter: Any, model: Any) -> Any:
    """``IModelDoc2.GetEquationMgr``, its methods flagged."""
    mgr = adapter._get_attr_or_call(model, "GetEquationMgr")
    if mgr is None:
        raise RuntimeError("SolidWorks gave no equation manager (GetEquationMgr)")
    _sw_type_info.flag_members(mgr, *_MANAGER_METHODS)
    return mgr


def _count(adapter: Any, mgr: Any) -> int:
    count = adapter._attempt(lambda: mgr.GetCount(), default=0)
    return int(count) if isinstance(count, int | float) else 0


def _text(adapter: Any, mgr: Any, index: int) -> str | None:
    text = adapter._attempt(lambda: get_indexed(mgr, "Equation", index))
    return text if isinstance(text, str) else None


def _texts(adapter: Any, mgr: Any) -> list[str | None]:
    return [_text(adapter, mgr, i) for i in range(_count(adapter, mgr))]


def driven_map(adapter: Any, model: Any) -> dict[str, str]:
    """Each dimension an equation sets (lower case, no document suffix) -> the text.

    On a model without equations this is one ``GetCount()`` call.
    """
    mgr = adapter._attempt(lambda: manager(adapter, model))
    if mgr is None:
        return {}
    out: dict[str, str] = {}
    for text in _texts(adapter, mgr):
        name = name_of(text)
        if name and "@" in name and text is not None:
            out.setdefault(_key(name), text)
    return out


def driven_by(driven: dict[str, str], full_name: Any) -> str | None:
    """The equation that sets ``full_name``, from :func:`driven_map`."""
    if not isinstance(full_name, str):
        return None
    return driven.get(_key(full_name))


def equation_refusal(name: str, equation: str) -> str:
    """``set_dimension``'s refusal of a dimension an equation sets (D7)."""
    return (
        f"Invalid request: {name} is set by the equation `{equation}`; change it "
        "with set_equations (nothing was written)"
    )


def document_units(adapter: Any, model: Any) -> str | None:
    """The document's length unit (``swUnitsLinear``): ``mm``, ``in``, ..."""
    ext = adapter._attempt(lambda: model.Extension)
    if ext is None:
        return None
    value = adapter._attempt(lambda: ext.GetUserPreferenceInteger(_UNITS_LINEAR, 0))
    return _LENGTH_UNITS.get(int(value)) if isinstance(value, int | float) else None


def _solve_order(adapter: Any, mgr: Any) -> bool | None:
    value = adapter._attempt(lambda: mgr.AutomaticSolveOrder)
    return bool(value) if isinstance(value, bool | int) else None


def _set_solve_order(adapter: Any, mgr: Any, on: bool) -> None:
    adapter._attempt(lambda: setattr(mgr, "AutomaticSolveOrder", on))


def read_entries(adapter: Any, model: Any, mgr: Any) -> list[dict[str, Any]]:
    """Every equation in the manager's order, each marked ``broken`` or not (D6)."""
    entries: list[dict[str, Any]] = []
    for index, text in enumerate(_texts(adapter, mgr)):
        name = name_of(text)
        is_global = adapter._attempt(
            lambda i=index: get_indexed(mgr, "GlobalVariable", i)
        )
        if not isinstance(is_global, bool | int):
            is_global = bool(name) and "@" not in (name or "")
        value = adapter._attempt(lambda i=index: get_indexed(mgr, "Value", i))
        entries.append(
            {
                "index": index,
                "equation": text,
                "name": name,
                "value": float(value) if isinstance(value, int | float) else None,
                "global": bool(is_global),
            }
        )
    variables = [e["name"] for e in entries if e["global"] and e["name"]]
    resolved: dict[str, bool] = {}

    def resolves(name: str) -> bool:
        key = short_name(name)
        if key not in resolved:
            resolved[key] = bool(adapter._attempt(lambda: model.Parameter(key)))
        return resolved[key]

    for entry in entries:
        entry["broken"] = is_broken(right_names(entry["equation"]), variables, resolves)
    return entries


def rebuild(adapter: Any, model: Any) -> None:
    """One ``ForceRebuild3(False)``."""
    adapter._attempt(lambda: model.ForceRebuild3(False))


def failing_features(adapter: Any, model: Any) -> list[dict[str, Any]]:
    """The features that fail after the rebuild, as ``rebuild_model`` explains them."""
    from .rebuild_diagnostics import explain
    from .rebuild_inspector import collect_rebuild_issues

    issues = adapter._attempt(
        lambda: collect_rebuild_issues(adapter, model), default=None
    )
    entries = [explain(issue) for issue in issues or []]
    return [e for e in entries if e.get("severity") == "error"]


def _add(adapter: Any, mgr: Any, index: int, text: str) -> int:
    """``Add3`` at ``index`` without solving, ``Add2`` where ``Add3`` is missing."""
    added, error = adapter._attempt_with_error(
        lambda: mgr.Add3(index, text, False, ALL_CONFIGURATIONS, None)
    )
    if error is not None:
        added = adapter._attempt(lambda: mgr.Add2(index, text, False), default=-1)
    return int(added) if isinstance(added, int | float) else -1


def _replace(adapter: Any, mgr: Any, index: int, text: str) -> str | None:
    """``SetEquation`` (``Equation(i) = text``), then the text read back."""
    _, error = adapter._attempt_with_error(
        lambda: put_indexed(mgr, "Equation", index, text)
    )
    if error is not None:
        adapter._attempt(
            lambda: mgr.SetEquationAndConfigurationOption(
                index, text, ALL_CONFIGURATIONS, None
            )
        )
    return _text(adapter, mgr, index)


def _undo(
    adapter: Any,
    model: Any,
    mgr: Any,
    journal: list[tuple[str, int, str | None]],
    solve_order_was: bool | None,
) -> None:
    """Undo the journal in reverse, put the solve order back, and rebuild."""
    for action, index, old in reversed(journal):
        if action == "add":
            adapter._attempt(lambda i=index: mgr.Delete(i))
        elif old is not None:
            _replace(adapter, mgr, index, old)
    if solve_order_was is not None:
        _set_solve_order(adapter, mgr, solve_order_was)
    rebuild(adapter, model)


def _refused(text: str, why: str) -> ValueError:
    return ValueError(
        f"Invalid request: SolidWorks refused the equation {text} ({why}); "
        "every equation of this call was put back as it was. Names go in "
        "double quotes and must exist: a dimension's full name "
        "(list_model_dimensions) or a global variable (list_equations)."
    )


def list_all(adapter: Any) -> dict[str, Any]:
    """Every equation of the active model, the solve order and the units."""
    model = adapter.currentModel
    mgr = manager(adapter, model)
    entries = read_entries(adapter, model, mgr)
    return {
        "equations": entries,
        "count": len(entries),
        "automatic_solve_order": _solve_order(adapter, mgr),
        "document_units": document_units(adapter, model),
    }


def set_equations(adapter: Any, equations: list[str]) -> dict[str, Any]:
    """Add or replace every equation, all or nothing, then rebuild once (D4).

    Raises:
        ValueError: ``Invalid request``: a left side is missing or repeated
            (nothing was sent), or SolidWorks refused an equation (the
            journal was undone).
    """
    if isinstance(equations, str):
        equations = [equations]
    names = check_equations(equations)
    texts = [text.strip() for text in equations]
    model = adapter.currentModel
    mgr = manager(adapter, model)
    count = _count(adapter, mgr)
    existing: dict[str, int] = {}
    for index in range(count):
        name = name_of(_text(adapter, mgr, index))
        if name:
            existing.setdefault(_key(name), index)
    solve_order_was = _solve_order(adapter, mgr)
    _set_solve_order(adapter, mgr, True)
    journal: list[tuple[str, int, str | None]] = []
    applied: list[dict[str, Any]] = []
    for text, name in zip(texts, names, strict=True):
        index = existing.get(_key(name))
        if index is not None:
            journal.append(("replace", index, _text(adapter, mgr, index)))
            now = _replace(adapter, mgr, index, text)
            if not same_equation(now, text):
                _undo(adapter, model, mgr, journal, solve_order_was)
                raise _refused(text, f"equation {index} reads back as {now}")
            applied.append({"equation": text, "index": index, "replaced": True})
            continue
        index = _add(adapter, mgr, count, text)
        if index < 0:
            status = adapter._attempt(lambda: mgr.Status)
            _undo(adapter, model, mgr, journal, solve_order_was)
            raise _refused(text, f"adding it returned {index}, Status {status}")
        journal.append(("add", index, None))
        count += 1
        applied.append({"equation": text, "index": index, "replaced": False})
    rebuild(adapter, model)
    return {
        "equations": applied,
        "automatic_solve_order_was": solve_order_was,
        "failing_features": failing_features(adapter, model),
        "document_units": document_units(adapter, model),
    }


def find(entries: list[dict[str, Any]], equation: str | int) -> dict[str, Any] | None:
    """The entry ``equation`` names: an index, or the name it sets (quotes optional)."""
    if isinstance(equation, int) and not isinstance(equation, bool):
        return next((e for e in entries if e["index"] == equation), None)
    wanted = _key(str(equation).strip().strip('"').strip())
    match = next((e for e in entries if e["name"] and _key(e["name"]) == wanted), None)
    if match is None and wanted.isdigit():
        return find(entries, int(wanted))
    return match


def not_found(entries: list[dict[str, Any]], equation: str | int) -> LookupError:
    """``not_found``, listing the equations there are."""
    there = "; ".join(f"{e['index']}: {e['equation']}" for e in entries)
    label = f"at index {equation}" if isinstance(equation, int) else repr(equation)
    return LookupError(
        f"Equation {label} not found; the equations are: {there or 'none'}"
    )


def delete_equation(adapter: Any, equation: str | int) -> dict[str, Any]:
    """Delete one equation, rebuild once, and say what is left."""
    model = adapter.currentModel
    mgr = manager(adapter, model)
    entries = read_entries(adapter, model, mgr)
    target = find(entries, equation)
    if target is None:
        raise not_found(entries, equation)
    adapter._attempt(lambda: mgr.Delete(int(target["index"])))
    if _count(adapter, mgr) != len(entries) - 1:
        raise RuntimeError(
            f"SolidWorks did not delete {target['equation']} (IEquationMgr.Delete)"
        )
    rebuild(adapter, model)
    after = read_entries(adapter, model, mgr)
    name = _key(target["name"] or "")
    return {
        "deleted": target["equation"],
        "count": len(after),
        "failing_features": failing_features(adapter, model),
        "left_broken": [
            e["equation"]
            for e in after
            if e["broken"] and name in {_key(n) for n in right_names(e["equation"])}
        ],
    }


def document_guard(adapter: Any, operation: str) -> str | None:
    """Why ``operation`` can't run on the active document, or None (``invalid_input``)."""
    from .io import _doc_type

    if not adapter.currentModel:
        return (
            f"Invalid request: {operation} takes an open part or assembly, "
            "and no document is open"
        )
    doc_type = _doc_type(adapter)
    if doc_type not in (1, 2):
        kind = {3: "a drawing"}.get(doc_type or 0, "of an unknown type")
        return (
            f"Invalid request: {operation} takes a part or an assembly; the "
            f"active document is {kind}"
        )
    return None


class SolidWorksEquationsMixin:
    """``list_equations``, ``set_equations`` and ``delete_equation`` (D8)."""

    def _equation_call(self, operation: str, work: Callable[[Any], Any]) -> Any:
        from ..base import AdapterResult, AdapterResultStatus

        refused = document_guard(self, operation)
        if refused is not None:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=refused)
        return self._handle_com_operation(operation, lambda: work(self))  # type: ignore[attr-defined]

    async def list_equations(self) -> Any:
        """Every equation and global variable of the active part or assembly.

        Returns:
            AdapterResult[dict[str, Any]]: ``equations`` (each ``index``,
            ``equation``, ``name``, ``value``, ``global``, ``broken``),
            ``count``, ``automatic_solve_order`` and ``document_units``.
        """
        return self._equation_call("list_equations", list_all)

    async def set_equations(self, equations: list[str]) -> Any:
        """Add or replace equations, all or nothing, and rebuild once.

        Returns:
            AdapterResult[dict[str, Any]]: ``equations`` (each ``equation``,
            ``index``, ``replaced``), ``automatic_solve_order_was``,
            ``failing_features`` and ``document_units``.
        """
        return self._equation_call(
            "set_equations", lambda adapter: set_equations(adapter, equations)
        )

    async def delete_equation(self, equation: str | int) -> Any:
        """Delete an equation by the name it sets or its index, and rebuild.

        Returns:
            AdapterResult[dict[str, Any]]: ``deleted``, ``count``,
            ``failing_features`` and ``left_broken``.
        """
        return self._equation_call(
            "delete_equation", lambda adapter: delete_equation(adapter, equation)
        )
