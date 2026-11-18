"""Global variables and equations, through ``IEquationMgr``.

The MMPLA well-plate build (SOLIDWORKS 2026 SP4.1) used one well diameter,
one well depth and one start offset in five sketches, and had to change each
of them in five places. SolidWorks' own answer is a global variable
(``"well_d" = 1.2mm``) and dimension equations that read it
(``"D1@Sketch3" = "well_d"``). This module reads and writes them through
``IModelDoc2.GetEquationMgr``.

What the API help says, and what the code does about it:

- ``Equation``, ``Value``, ``GlobalVariable`` and ``Suppression`` are
  *indexed properties*; late binding reads them only through
  ``IDispatch.Invoke`` with ``DISPATCH_PROPERTYGET`` (``ref_plane_edit``'s
  ``get_indexed``), and ``Equation(i) = text`` through ``put_indexed``.
- Names go in double quotes: ``"D1@Sketch3" = "well_d" * 2``.
- ``Add2`` (one configuration) and ``Add3`` (several, made in 2014 or later;
  a global variable needs ``swAllConfiguration``) return the new index or
  -1. ``Add2`` refuses a left side that already has an equation, so an
  existing one is changed in place instead.
- ``Status`` is the index of the last equation that ran, or -1 when it
  failed: a refused expression is undone (deleted, or its old text put
  back) and returned as an ``Invalid request`` (``invalid_input``).
- "Do not add an equation while traversing the equations": every list is
  read whole before anything is added or deleted.

After a change the model is rebuilt and the equations read again, so the
reply gives what each driven dimension now is.
"""

from __future__ import annotations

import re
from typing import Any

from .. import sw_type_info as _sw_type_info
from .io import dimension_kind, from_system_value
from .ref_plane_edit import get_indexed, put_indexed

#: ``swInConfigurationOpts_e.swAllConfiguration`` (SolidWorks 2026 swconst).
ALL_CONFIGURATIONS = 2

#: Units ``set_global_variable`` writes after the number, by the name given.
UNIT_SUFFIXES = {
    "mm": "mm",
    "cm": "cm",
    "m": "m",
    "in": "in",
    "ft": "ft",
    "deg": "deg",
    "rad": "rad",
    "": "",
    "none": "",
}

_LEFT_SIDE = re.compile(r'^\s*"([^"]+)"\s*=')
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_MANAGER_METHODS = (
    "GetCount",
    "Add2",
    "Add3",
    "Delete",
    "EvaluateAll",
    "SetEquationAndConfigurationOption",
)

_HOW_TO_WRITE = (
    "Names go in double quotes and must exist: a dimension's full name such "
    'as "D1@Sketch1" (list_model_dimensions) or a global variable '
    "(list_equations); a left side carries one equation."
)


def left_side(equation: Any) -> str | None:
    """The name an equation sets: ``well_d`` from ``"well_d" = 1.2mm``."""
    if not isinstance(equation, str):
        return None
    match = _LEFT_SIDE.match(equation)
    return match.group(1).strip() if match else None


def right_side(equation: str) -> str:
    """What an equation sets its name to (everything after the first ``=``)."""
    return equation.split("=", 1)[1].strip() if "=" in equation else ""


def unquote(name: str) -> str:
    """A name without spaces or the double quotes around it."""
    return (name or "").strip().strip('"').strip()


def references(equation: str, name: str) -> bool:
    """Whether ``equation``'s right side reads ``name`` (``"name"``)."""
    return f'"{name.lower()}"' in right_side(equation).lower()


def format_number(value: float, units: str) -> str:
    """``1.2mm`` for 1.2 and ``mm``; a whole number keeps no decimals."""
    return f"{value:.12g}{UNIT_SUFFIXES[units]}"


def invalid(equation: str, how: str) -> ValueError:
    """The error for an equation SolidWorks wouldn't take (``invalid_input``)."""
    return ValueError(
        f"Invalid request: SolidWorks refused the equation {equation} ({how}); "
        f"nothing was changed. {_HOW_TO_WRITE}"
    )


def manager(adapter: Any, model: Any) -> Any:
    """``IModelDoc2.GetEquationMgr``, its methods flagged."""
    mgr = adapter._get_attr_or_call(model, "GetEquationMgr")
    if mgr is None:
        raise RuntimeError("SolidWorks gave no equation manager (GetEquationMgr)")
    _sw_type_info.flag_members(mgr, *_MANAGER_METHODS)
    return mgr


def read_dimension(adapter: Any, model: Any, name: str) -> dict[str, Any] | None:
    """A dimension's value in the tools' unit, or None when it doesn't exist."""
    dim = adapter._attempt(lambda: model.Parameter(name), default=None)
    if not dim:
        return None
    kind, unit, factor = dimension_kind(adapter, dim)
    raw = adapter._attempt(lambda: dim.SystemValue, default=None)
    value = (
        from_system_value(float(raw), kind, factor)
        if isinstance(raw, int | float)
        else None
    )
    return {"name": name, "value": value, "units": unit, "kind": kind}


def read_entries(adapter: Any, model: Any, mgr: Any) -> list[dict[str, Any]]:
    """Every equation and global variable, in the manager's order."""
    count = adapter._attempt(lambda: int(mgr.GetCount()), default=0) or 0
    entries: list[dict[str, Any]] = []
    for index in range(count):
        text = adapter._attempt(lambda i=index: get_indexed(mgr, "Equation", i))
        if not isinstance(text, str):
            continue
        name = left_side(text) or ""
        is_global = adapter._attempt(
            lambda i=index: bool(get_indexed(mgr, "GlobalVariable", i)),
            default=None,
        )
        if is_global is None:  # before SOLIDWORKS 2016 SP4
            is_global = bool(name) and "@" not in name
        kind = (
            "global_variable"
            if is_global
            else ("dimension" if "@" in name else "other")
        )
        entry: dict[str, Any] = {
            "index": index,
            "name": name,
            "equation": text,
            "kind": kind,
            "value": adapter._attempt(lambda i=index: get_indexed(mgr, "Value", i)),
            "suppressed": adapter._attempt(
                lambda i=index: bool(get_indexed(mgr, "Suppression", i)),
                default=None,
            ),
        }
        if kind == "dimension":
            entry["dimension"] = read_dimension(adapter, model, name)
        entries.append(entry)
    return entries


def summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """``global_variables``, ``equations`` (the rest) and ``count``."""
    return {
        "global_variables": [e for e in entries if e["kind"] == "global_variable"],
        "equations": [e for e in entries if e["kind"] != "global_variable"],
        "count": len(entries),
    }


def find(entries: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """The entry that sets ``name`` (case-insensitive), if any."""
    wanted = name.lower()
    return next((e for e in entries if e["name"].lower() == wanted), None)


def _status(adapter: Any, mgr: Any) -> Any:
    return adapter._attempt(lambda: mgr.Status, default=None)


def _add(adapter: Any, model: Any, mgr: Any, equation: str) -> int:
    """Add at the end with ``Add3`` (several configurations) or ``Add2``; the index."""
    configurations = adapter._attempt(
        lambda: int(model.GetConfigurationCount()), default=1
    )

    def add2() -> Any:
        return mgr.Add2(-1, equation, True)

    def add3() -> Any:
        return mgr.Add3(-1, equation, True, ALL_CONFIGURATIONS, None)

    tries = (add3, add2) if (configurations or 1) > 1 else (add2, add3)
    for attempt_add in tries:
        index = adapter._attempt(attempt_add, default=None)
        if isinstance(index, int) and index >= 0:
            return index
    return -1


def _set_text(adapter: Any, mgr: Any, index: int, equation: str) -> bool:
    """Put ``equation`` at ``index``: ``Equation(i) =``, else the configured setter."""
    _, error = adapter._attempt_with_error(
        lambda: put_indexed(mgr, "Equation", index, equation)
    )
    if error is not None:
        done = adapter._attempt(
            lambda: mgr.SetEquationAndConfigurationOption(
                index, equation, ALL_CONFIGURATIONS, None
            ),
            default=-1,
        )
        if done != index:
            return False
    now = adapter._attempt(lambda: get_indexed(mgr, "Equation", index))
    return left_side(now) == left_side(equation)


def apply(
    adapter: Any,
    model: Any,
    mgr: Any,
    equation: str,
    existing: dict[str, Any] | None,
) -> int:
    """Add ``equation``, or change ``existing`` to it; undo it if SolidWorks refuses.

    Returns:
        int: The equation's index.

    Raises:
        ValueError: ``Invalid request``: SolidWorks refused it; the model is
            as it was.
    """
    if existing is not None:
        index = int(existing["index"])
        if not _set_text(adapter, mgr, index, equation) or _status(adapter, mgr) == -1:
            _set_text(adapter, mgr, index, existing["equation"])
            raise invalid(equation, f"changing equation {index}; Status -1")
        return index
    index = _add(adapter, model, mgr, equation)
    if index < 0:
        raise invalid(equation, "Add2 and Add3 returned -1")
    if _status(adapter, mgr) == -1:
        adapter._attempt(lambda: mgr.Delete(index))
        raise invalid(equation, f"it was added at {index} but Status is -1")
    return index


def rebuild(adapter: Any, model: Any) -> None:
    """``EditRebuild3``, else ``ForceRebuild3``, so driven dimensions update."""
    rebuilt = adapter._attempt(
        lambda: adapter._get_attr_or_call(model, "EditRebuild3"), default=None
    )
    if rebuilt is None:
        adapter._attempt(lambda: model.ForceRebuild3(False))


def list_all(adapter: Any) -> dict[str, Any]:
    """Every global variable and equation of the active model."""
    model = adapter.currentModel
    return summary(read_entries(adapter, model, manager(adapter, model)))


def set_global_variable(
    adapter: Any, name: str, value: float, units: str
) -> dict[str, Any]:
    """Add or change ``"name" = value units``, rebuild, and read what it drives."""
    name = unquote(name)
    units = (units or "").strip().lower()
    if not name or "@" in name or '"' in name or "=" in name:
        raise ValueError(
            f"Invalid request: {name!r} can't name a global variable: give a "
            'plain name such as "well_d", without quotes, "@" or "="'
        )
    if units not in UNIT_SUFFIXES:
        raise ValueError(
            f"Invalid request: units {units!r} isn't one of "
            + ", ".join(sorted(u for u in UNIT_SUFFIXES if u))
            + ' (or "" for a plain number)'
        )
    model = adapter.currentModel
    mgr = manager(adapter, model)
    entries = read_entries(adapter, model, mgr)
    existing = find(entries, name)
    if existing is not None and existing["kind"] != "global_variable":
        raise ValueError(
            f"Invalid request: {existing['name']!r} is set by an equation that "
            f"isn't a global variable ({existing['equation']})"
        )
    equation = f'"{existing["name"] if existing else name}" = ' + format_number(
        value, units
    )
    apply(adapter, model, mgr, equation, existing)
    rebuild(adapter, model)
    after = read_entries(adapter, model, mgr)
    variable = find(after, name)
    return {
        "action": "changed" if existing else "added",
        "global_variable": variable,
        "previous": existing["equation"] if existing else None,
        "driven": [
            e
            for e in after
            if e["kind"] != "global_variable" and references(e["equation"], name)
        ],
        **summary(after),
    }


def add_equation(adapter: Any, dimension: str, expression: str) -> dict[str, Any]:
    """Set ``"dimension" = expression``, rebuild, and read the dimension back."""
    target = unquote(dimension)
    if "@" not in target:
        raise ValueError(
            f"Invalid request: {dimension!r} isn't a dimension's full name; give "
            'one such as "D1@Sketch3" (list_model_dimensions lists them)'
        )
    text = (expression or "").strip()
    if text.startswith("="):
        text = text[1:].strip()
    if not text:
        raise ValueError("Invalid request: expression is empty")
    model = adapter.currentModel
    before_value = read_dimension(adapter, model, target)
    if before_value is None:
        raise LookupError(
            f"Dimension {target!r} not found; list_model_dimensions gives the "
            "full names"
        )
    mgr = manager(adapter, model)
    entries = read_entries(adapter, model, mgr)
    if _IDENTIFIER.match(text):
        variable = find(entries, text)
        if variable is not None and variable["kind"] == "global_variable":
            text = f'"{variable["name"]}"'
    existing = find(entries, target)
    equation = f'"{existing["name"] if existing else target}" = {text}'
    apply(adapter, model, mgr, equation, existing)
    rebuild(adapter, model)
    after = read_entries(adapter, model, mgr)
    return {
        "action": "changed" if existing else "added",
        "equation": find(after, target),
        "previous": existing["equation"] if existing else None,
        "dimension": read_dimension(adapter, model, target),
        "value_before": before_value["value"],
        **summary(after),
    }


def delete_equation(
    adapter: Any, name: str | None, index: int | None, force: bool
) -> dict[str, Any]:
    """Delete the equation that sets ``name`` (or the one at ``index``)."""
    if not (name or "").strip() and index is None:
        raise ValueError("Invalid request: give the equation's name or its index")
    model = adapter.currentModel
    mgr = manager(adapter, model)
    entries = read_entries(adapter, model, mgr)
    if (name or "").strip():
        target = find(entries, unquote(name or ""))
        label = repr(unquote(name or ""))
    else:
        target = next((e for e in entries if e["index"] == index), None)
        label = f"at index {index}"
    if target is None:
        raise LookupError(f"Equation {label} not found; list_equations lists them")
    users = [
        e
        for e in entries
        if e is not target and references(e["equation"], target["name"])
    ]
    if users and not force:
        raise ValueError(
            f'Invalid request: "{target["name"]}" is used by {len(users)} '
            "equation(s): "
            + "; ".join(e["equation"] for e in users)
            + ". Delete those first, or pass force: true (they would then fail "
            "to solve)."
        )
    done = adapter._attempt(lambda: mgr.Delete(int(target["index"])), default=None)
    if done != 0:
        raise RuntimeError(
            f"SolidWorks did not delete {target['equation']} "
            f"(IEquationMgr.Delete returned {done})"
        )
    rebuild(adapter, model)
    after = read_entries(adapter, model, mgr)
    return {
        "deleted": target,
        "left_using_it": [e["equation"] for e in users],
        **summary(after),
    }


class SolidWorksEquationsMixin:
    """``list_equations``, ``set_global_variable``, ``add_equation``, ``delete_equation``."""

    def _equation_call(self, operation: str, work: Any) -> Any:
        guard = self._require_doc_type({1, 2}, operation)  # type: ignore[attr-defined]
        if guard is not None:
            return guard
        return self._handle_com_operation(operation, lambda: work(self))  # type: ignore[attr-defined]

    async def list_equations(self) -> Any:
        """Every global variable and equation of the active part or assembly.

        Returns:
            AdapterResult[dict[str, Any]]: ``global_variables``, ``equations``
            (each ``index``, ``name``, ``equation``, ``kind``, ``value``,
            ``suppressed``; a dimension's ``dimension`` read back) and ``count``.
        """
        return self._equation_call("list_equations", list_all)

    async def set_global_variable(
        self, name: str, value: float, units: str = "mm"
    ) -> Any:
        """Add or change a global variable, rebuild, and report what it drives.

        Returns:
            AdapterResult[dict[str, Any]]: ``action``, ``global_variable``,
            ``previous``, ``driven`` and the lists of :func:`summary`.
        """
        return self._equation_call(
            "set_global_variable",
            lambda adapter: set_global_variable(adapter, name, value, units),
        )

    async def add_equation(self, dimension: str, expression: str) -> Any:
        """Drive a dimension by an equation, rebuild, and read the dimension back.

        Returns:
            AdapterResult[dict[str, Any]]: ``action``, ``equation``,
            ``previous``, ``dimension``, ``value_before`` and the lists.
        """
        return self._equation_call(
            "add_equation",
            lambda adapter: add_equation(adapter, dimension, expression),
        )

    async def delete_equation(
        self, name: str | None = None, index: int | None = None, force: bool = False
    ) -> Any:
        """Delete an equation or global variable and rebuild.

        Returns:
            AdapterResult[dict[str, Any]]: ``deleted``, ``left_using_it`` and
            the lists.
        """
        return self._equation_call(
            "delete_equation",
            lambda adapter: delete_equation(adapter, name, index, force),
        )
