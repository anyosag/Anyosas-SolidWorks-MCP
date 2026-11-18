"""Global variables and equations through a fake ``IEquationMgr``.

The fake answers the indexed properties (``Equation``, ``Value``,
``GlobalVariable``, ``Suppression``) only through ``IDispatch.Invoke``, as
late binding does, and solves ``"name" = number[unit]`` and ``"a" * k``
equations against a part's dimensions when the part rebuilds.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import equations as eq

_DISPIDS = {"Equation": 8, "Value": 7, "GlobalVariable": 22, "Suppression": 1}
_GET, _PUT = 2, 4


class FakeOle:
    def __init__(self, mgr: FakeManager) -> None:
        self.mgr = mgr

    def GetIDsOfNames(self, _lcid: int, name: str) -> int:
        return _DISPIDS[name]

    def Invoke(self, dispid: int, _lcid: int, flags: int, _ret: bool, *args: Any):
        name = next(k for k, v in _DISPIDS.items() if v == dispid)
        if flags == _PUT:
            return self.mgr.put(name, *args)
        return self.mgr.get(name, *args)


class FakeDim:
    def __init__(self, metres: float, kind: int = 0) -> None:
        self.SystemValue = metres
        self._kind = kind

    def GetType(self) -> int:
        return self._kind


class FakeManager:
    """Equations as text; ``broken`` names make an equation fail to solve."""

    def __init__(self, model: FakeModel) -> None:
        self.model = model
        self.texts: list[str] = []
        self.Status = 0
        self.global_flag_raises = False
        self.put_raises = False
        self.broken_added = False
        self.deleted: list[int] = []
        self._oleobj_ = FakeOle(self)
        self.add2_calls: list[str] = []
        self.add3_calls: list[str] = []

    # -- indexed properties
    def get(self, name: str, index: int) -> Any:
        text = self.texts[index]
        if name == "Equation":
            return text
        if name == "GlobalVariable":
            if self.global_flag_raises:
                raise AttributeError("GlobalVariable")
            return "@" not in (eq.left_side(text) or "")
        if name == "Suppression":
            return False
        return self.model.solve(eq.right_side(text))

    def put(self, name: str, index: int, text: str) -> None:
        if self.put_raises:
            raise AttributeError("Equation is read-only here")
        self.texts[index] = text
        self.Status = index if self.model.can_solve(eq.right_side(text)) else -1

    # -- methods
    def GetCount(self) -> int:
        return len(self.texts)

    def _add(self, text: str) -> int:
        lhs = eq.left_side(text)
        if any(eq.left_side(t) == lhs for t in self.texts):
            return -1
        if not self.model.can_solve(eq.right_side(text)) and not self.broken_added:
            return -1
        self.texts.append(text)
        self.Status = -1 if self.broken_added else len(self.texts) - 1
        return len(self.texts) - 1

    def Add2(self, _index: int, text: str, _solve: bool) -> int:
        self.add2_calls.append(text)
        return self._add(text)

    def Add3(self, _index: int, text: str, _solve: bool, which: int, _names: Any):
        self.add3_calls.append(text)
        assert which == eq.ALL_CONFIGURATIONS
        return self._add(text)

    def Delete(self, index: int) -> int:
        self.deleted.append(index)
        if self.model.refuse_delete:
            return -1
        del self.texts[index]
        return 0

    def SetEquationAndConfigurationOption(self, index: int, text: str, _w, _n) -> int:
        self.texts[index] = text
        self.Status = index
        return index


class FakeModel:
    def __init__(self) -> None:
        self.dims = {"D1@Sketch3": FakeDim(0.001), "D2@Sketch5": FakeDim(0.002)}
        self.mgr = FakeManager(self)
        self.configurations = 1
        self.refuse_delete = False
        self.rebuilds = 0

    def GetEquationMgr(self) -> FakeManager:
        return self.mgr

    def Parameter(self, name: str) -> FakeDim | None:
        return self.dims.get(name)

    def GetConfigurationCount(self) -> int:
        return self.configurations

    def EditRebuild3(self) -> bool:
        self.rebuilds += 1
        for text in self.mgr.texts:
            lhs = eq.left_side(text) or ""
            if lhs in self.dims and self.can_solve(eq.right_side(text)):
                self.dims[lhs].SystemValue = self.solve(eq.right_side(text)) / 1000
        return True

    def _values(self) -> dict[str, float]:
        values = {k: d.SystemValue * 1000 for k, d in self.dims.items()}
        for text in self.mgr.texts:
            lhs = eq.left_side(text) or ""
            if "@" not in lhs:
                values[lhs] = self.solve(eq.right_side(text))
        return values

    def can_solve(self, rhs: str) -> bool:
        try:
            self.solve(rhs)
        except Exception:
            return False
        return True

    def solve(self, rhs: str) -> float:
        values = self._values() if "@" in rhs or '"' in rhs else {}
        text = re.sub(r'"([^"]+)"', lambda m: repr(values[m.group(1)]), rhs)
        text = re.sub(r"(\d+(?:\.\d+)?)mm", r"\1", text)
        if not re.fullmatch(r"[\d.\s*+/()-]+", text):
            raise ValueError(text)
        return float(eval(text))  # noqa: S307 - digits and operators only


def _attempt(fn: Any, default: Any = None) -> Any:
    try:
        return fn()
    except Exception:
        return default


def _attempt_with_error(fn: Any) -> tuple[Any, Exception | None]:
    try:
        return fn(), None
    except Exception as exc:
        return None, exc


def _adapter(model: FakeModel | None = None) -> SimpleNamespace:
    model = model or FakeModel()

    def _handle(_name: str, fn: Any) -> AdapterResult[Any]:
        try:
            return AdapterResult(status=AdapterResultStatus.SUCCESS, data=fn())
        except Exception as exc:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))

    def _get_attr_or_call(obj: Any, name: str) -> Any:
        attr = getattr(obj, name, None)
        return attr() if callable(attr) else attr

    adapter = SimpleNamespace(
        currentModel=model,
        _attempt=_attempt,
        _attempt_with_error=_attempt_with_error,
        _get_attr_or_call=_get_attr_or_call,
        _handle_com_operation=_handle,
        _require_doc_type=lambda _allowed, _op: None,
    )
    return adapter


def test_text_helpers() -> None:
    assert eq.left_side('"well_d" = 1.2mm') == "well_d"
    assert eq.left_side("no quotes = 1") is None
    assert eq.left_side(None) is None
    assert eq.right_side('"a" = "b" * 2') == '"b" * 2'
    assert eq.right_side("nothing") == ""
    assert eq.unquote(' "D1@Sketch3" ') == "D1@Sketch3"
    assert eq.references('"D1@S" = "Well_D" * 2', "well_d")
    assert not eq.references('"well_d" = 1mm', "well_d")
    assert eq.format_number(1.2, "mm") == "1.2mm"
    assert eq.format_number(8.0, "") == "8"


def test_a_global_variable_drives_two_dimensions() -> None:
    adapter = _adapter()
    model = adapter.currentModel

    added = eq.set_global_variable(adapter, "well_d", 1.2, "mm")
    eq.add_equation(adapter, "D1@Sketch3", "well_d")
    second = eq.add_equation(adapter, '"D2@Sketch5"', '= "well_d" * 2')

    assert added["action"] == "added"
    assert added["global_variable"]["equation"] == '"well_d" = 1.2mm'
    assert second["equation"]["equation"] == '"D2@Sketch5" = "well_d" * 2'
    assert second["dimension"] == {
        "name": "D2@Sketch5",
        "value": 2.4,
        "units": "mm",
        "kind": "length",
    }
    assert second["value_before"] == 2.0

    changed = eq.set_global_variable(adapter, "well_d", 1.5, "mm")

    assert changed["action"] == "changed"
    assert changed["previous"] == '"well_d" = 1.2mm'
    assert [d["dimension"]["value"] for d in changed["driven"]] == [1.5, 3.0]
    assert model.mgr.add2_calls[0] == '"well_d" = 1.2mm'
    assert len(model.mgr.texts) == 3


def test_listing_splits_variables_from_equations() -> None:
    adapter = _adapter()
    eq.set_global_variable(adapter, "n", 8, "")
    eq.add_equation(adapter, "D1@Sketch3", '"n" * 0.5')

    listed = eq.list_all(adapter)

    assert listed["count"] == 2
    assert [v["name"] for v in listed["global_variables"]] == ["n"]
    only = listed["equations"][0]
    assert only["kind"] == "dimension"
    assert only["dimension"]["value"] == 4.0
    assert only["suppressed"] is False


def test_without_the_global_flag_a_name_without_at_is_a_variable() -> None:
    adapter = _adapter()
    eq.set_global_variable(adapter, "w", 2, "mm")
    adapter.currentModel.mgr.global_flag_raises = True

    listed = eq.list_all(adapter)

    assert listed["global_variables"][0]["name"] == "w"


def test_several_configurations_add_with_add3() -> None:
    model = FakeModel()
    model.configurations = 2
    adapter = _adapter(model)

    eq.set_global_variable(adapter, "w", 2, "mm")

    assert model.mgr.add3_calls == ['"w" = 2mm']
    assert model.mgr.add2_calls == []


def test_an_unknown_name_is_refused_and_nothing_changes() -> None:
    adapter = _adapter()

    with pytest.raises(ValueError, match="Invalid request: SolidWorks refused"):
        eq.add_equation(adapter, "D1@Sketch3", '"nope" * 2')

    assert adapter.currentModel.mgr.texts == []


def test_an_equation_added_broken_is_deleted_again() -> None:
    adapter = _adapter()
    adapter.currentModel.mgr.broken_added = True

    with pytest.raises(ValueError, match="Status is -1"):
        eq.add_equation(adapter, "D1@Sketch3", '"nope"')

    assert adapter.currentModel.mgr.deleted == [0]
    assert adapter.currentModel.mgr.texts == []


def test_a_change_that_fails_puts_the_old_text_back() -> None:
    adapter = _adapter()
    eq.set_global_variable(adapter, "w", 2, "mm")
    eq.add_equation(adapter, "D1@Sketch3", "w")

    with pytest.raises(ValueError, match="changing equation 1"):
        eq.add_equation(adapter, "D1@Sketch3", '"missing" + 1')

    assert adapter.currentModel.mgr.texts[1] == '"D1@Sketch3" = "w"'


def test_a_read_only_equation_is_changed_through_the_configured_setter() -> None:
    adapter = _adapter()
    eq.set_global_variable(adapter, "w", 2, "mm")
    adapter.currentModel.mgr.put_raises = True

    changed = eq.set_global_variable(adapter, "w", 3, "mm")

    assert changed["global_variable"]["equation"] == '"w" = 3mm'


def test_the_configured_setter_failing_is_refused() -> None:
    adapter = _adapter()
    eq.set_global_variable(adapter, "w", 2, "mm")
    mgr = adapter.currentModel.mgr
    mgr.put_raises = True
    mgr.SetEquationAndConfigurationOption = lambda *_a: -1

    with pytest.raises(ValueError, match="Invalid request"):
        eq.set_global_variable(adapter, "w", 3, "mm")


@pytest.mark.parametrize(
    ("name", "units", "fragment"),
    [
        ("", "mm", "can't name a global variable"),
        ("D1@Sketch3", "mm", "can't name a global variable"),
        ("w", "furlong", "isn't one of"),
    ],
)
def test_bad_variables_are_refused(name: str, units: str, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        eq.set_global_variable(_adapter(), name, 1, units)


def test_a_variable_named_like_a_dimension_equation_is_refused() -> None:
    adapter = _adapter()
    adapter.currentModel.mgr.texts.append('"odd" = 1')
    adapter.currentModel.mgr.global_flag_raises = True
    adapter.currentModel.dims["odd"] = FakeDim(0.001)
    # A left side without "@" that the manager calls no variable.
    adapter.currentModel.mgr.get = lambda name, i, real=adapter.currentModel.mgr.get: (
        False if name == "GlobalVariable" else real(name, i)
    )

    with pytest.raises(ValueError, match="isn't a global variable"):
        eq.set_global_variable(adapter, "odd", 2, "mm")


def test_an_unreadable_equation_is_skipped() -> None:
    adapter = _adapter()
    eq.set_global_variable(adapter, "w", 2, "mm")
    mgr = adapter.currentModel.mgr
    mgr.get = lambda name, i: None if name == "Equation" else 0

    assert eq.list_all(adapter)["count"] == 0


@pytest.mark.parametrize(
    ("dimension", "expression", "error", "fragment"),
    [
        ("Sketch3", "1", ValueError, "isn't a dimension's full name"),
        ("D1@Sketch3", " = ", ValueError, "expression is empty"),
        ("D9@Sketch9", "1", LookupError, "not found"),
    ],
)
def test_bad_equations_are_refused(
    dimension: str, expression: str, error: type, fragment: str
) -> None:
    with pytest.raises(error, match=fragment):
        eq.add_equation(_adapter(), dimension, expression)


def test_a_variable_in_use_is_kept_unless_forced() -> None:
    adapter = _adapter()
    eq.set_global_variable(adapter, "w", 2, "mm")
    eq.add_equation(adapter, "D1@Sketch3", "w")

    with pytest.raises(ValueError, match="is used by 1 equation"):
        eq.delete_equation(adapter, "w", None, False)
    forced = eq.delete_equation(adapter, '"w"', None, True)

    assert forced["deleted"]["name"] == "w"
    assert forced["left_using_it"] == ['"D1@Sketch3" = "w"']


def test_delete_by_index_and_the_errors() -> None:
    adapter = _adapter()
    eq.set_global_variable(adapter, "w", 2, "mm")

    with pytest.raises(ValueError, match="name or its index"):
        eq.delete_equation(adapter, None, None, False)
    with pytest.raises(LookupError, match="at index 4 not found"):
        eq.delete_equation(adapter, None, 4, False)
    adapter.currentModel.refuse_delete = True
    with pytest.raises(RuntimeError, match="returned -1"):
        eq.delete_equation(adapter, None, 0, False)
    adapter.currentModel.refuse_delete = False

    done = eq.delete_equation(adapter, None, 0, False)

    assert done["count"] == 0


def test_no_equation_manager_is_an_error() -> None:
    adapter = _adapter()
    adapter.currentModel.GetEquationMgr = lambda: None

    with pytest.raises(RuntimeError, match="no equation manager"):
        eq.list_all(adapter)


def test_rebuild_falls_back_to_force_rebuild() -> None:
    calls: list[bool] = []
    model = SimpleNamespace(EditRebuild3=None, ForceRebuild3=calls.append)
    adapter = _adapter()

    eq.rebuild(adapter, model)

    assert calls == [False]


@pytest.mark.asyncio
async def test_the_mixin_runs_each_call_inside_the_guard() -> None:
    adapter = _adapter()
    mixin = eq.SolidWorksEquationsMixin
    for name in (
        "list_equations",
        "set_global_variable",
        "add_equation",
        "delete_equation",
    ):
        setattr(adapter, name, getattr(mixin, name).__get__(adapter))
    adapter._equation_call = mixin._equation_call.__get__(adapter)

    added = await adapter.set_global_variable("w", 2.0)
    linked = await adapter.add_equation("D1@Sketch3", "w")
    listed = await adapter.list_equations()
    removed = await adapter.delete_equation(index=1)

    assert added.is_success and linked.is_success and removed.is_success
    assert listed.data["count"] == 2

    refused = AdapterResult(status=AdapterResultStatus.ERROR, error="needs a part")
    adapter._require_doc_type = lambda _allowed, _op: refused
    assert await adapter.list_equations() is refused


@pytest.mark.parametrize(
    "name", ["list_equations", "set_global_variable", "add_equation", "delete_equation"]
)
def test_the_real_adapter_takes_them_from_the_mixin(name: str) -> None:
    from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter

    owner = next(
        klass.__name__ for klass in PyWin32Adapter.__mro__ if name in vars(klass)
    )
    assert owner == "SolidWorksEquationsMixin"
