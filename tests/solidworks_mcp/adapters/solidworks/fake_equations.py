"""A fake ``IEquationMgr`` and the model and adapter around it.

The indexed properties (``Equation``, ``Value``, ``GlobalVariable``) answer
only through ``IDispatch.Invoke``, as late binding reaches them; ``Equation``'s
put is gen_py's ``SetEquation``. The manager refuses what tests tell it to:
``Add3``/``Add2`` return -1 for a text in ``refuse_add``, and a put of a text
in ``keep_old`` keeps the old text without an error (hjbaard's finding).
As on SOLIDWORKS 2026 SP4.1, ``Add3`` returns -1 on a part with one
configuration (pitfall #60), and ``Add2(-1, ...)`` appends.
"""

from __future__ import annotations

import re
from typing import Any

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus

_DISPIDS = {"Equation": 8, "Value": 7, "GlobalVariable": 22}
_GET, _PUT = 2, 4
_NUMBER = re.compile(r"=\s*(-?\d+(?:\.\d+)?)\s*(mm|in)?\s*$")


class FakeOle:
    def __init__(self, mgr: FakeManager) -> None:
        self.mgr = mgr

    def GetIDsOfNames(self, _lcid: int, name: str) -> int:  # noqa: N802 - COM name
        return _DISPIDS[name]

    def Invoke(self, dispid: int, _lcid: int, flags: int, _ret: bool, *args: Any):  # noqa: N802
        name = next(k for k, v in _DISPIDS.items() if v == dispid)
        self.mgr.reads.append(name)
        if flags == _PUT:
            return self.mgr.put(*args)
        return self.mgr.get(name, *args)


class FakeManager:
    """Equations as text, in order."""

    def __init__(self, texts: list[str] | None = None) -> None:
        self.texts = list(texts or [])
        self.AutomaticSolveOrder = False
        self.Status = 0
        self.refuse_add: set[str] = set()
        self.keep_old: set[str] = set()
        self.add3_missing = False
        self.add2_missing = False
        self.configurations = 1
        self.add3_calls: list[tuple[Any, ...]] = []
        self.add2_calls: list[tuple[Any, ...]] = []
        self.counts = 0
        self.reads: list[str] = []
        self._oleobj_ = FakeOle(self)

    def get(self, name: str, index: int) -> Any:
        text = self.texts[index]
        if name == "Equation":
            return text
        if name == "GlobalVariable":
            left = text.split("=", 1)[0]
            return "@" not in left
        match = _NUMBER.search(text)
        return float(match.group(1)) if match else 0.0

    def put(self, index: int, text: str) -> None:
        if text not in self.keep_old:
            self.texts[index] = text

    def GetCount(self) -> int:  # noqa: N802 - COM name
        self.counts += 1
        return len(self.texts)

    def _add(self, index: int, text: str) -> int:
        if text in self.refuse_add:
            self.Status = -1
            return -1
        if index < 0:
            index = len(self.texts)
        self.texts.insert(index, text)
        return index

    def Add3(self, index: int, text: str, solve: bool, which: int, names: Any):  # noqa: N802
        if self.add3_missing:
            raise AttributeError("Add3")
        self.add3_calls.append((index, text, solve, which, names))
        if self.configurations < 2:
            self.Status = -1
            return -1
        return self._add(index, text)

    def Add2(self, index: int, text: str, solve: bool) -> int:  # noqa: N802
        if self.add2_missing:
            raise AttributeError("Add2")
        self.add2_calls.append((index, text, solve))
        return self._add(index, text)

    def Delete(self, index: int) -> int:  # noqa: N802 - COM name
        del self.texts[index]
        return 0


class FakeExtension:
    def __init__(self, units: int) -> None:
        self.units = units

    def GetUserPreferenceInteger(self, pref: int, _option: int) -> int:  # noqa: N802
        assert pref == 47
        return self.units


class FakeModel:
    """A part with dimensions (names), an equation manager and a units preference."""

    def __init__(
        self,
        texts: list[str] | None = None,
        dimensions: tuple[str, ...] = ("D1@Sketch1", "D2@Sketch1", "D1@Boss"),
        doc_type: int = 1,
        units: int = 0,
    ) -> None:
        self.mgr = FakeManager(texts)
        self.dimensions = set(dimensions)
        self.doc_type = doc_type
        self.Extension = FakeExtension(units)
        self.rebuilds: list[bool] = []

    def GetEquationMgr(self) -> FakeManager:  # noqa: N802 - COM name
        return self.mgr

    def GetType(self) -> int:  # noqa: N802 - COM name
        return self.doc_type

    def Parameter(self, name: str) -> Any:  # noqa: N802 - COM name
        return object() if name in self.dimensions else None

    def ForceRebuild3(self, top_only: bool) -> bool:  # noqa: N802 - COM name
        self.rebuilds.append(top_only)
        return True


class FakeAdapter:
    """The ``PyWin32Adapter`` members the equation code uses."""

    def __init__(self, model: Any = None) -> None:
        self.currentModel = model

    def _attempt(self, operation: Any, default: Any = None) -> Any:
        try:
            return operation()
        except Exception:
            return default

    def _attempt_with_error(self, operation: Any) -> tuple[Any, Exception | None]:
        try:
            return operation(), None
        except Exception as exc:
            return None, exc

    def _get_attr_or_call(self, obj: Any, name: str) -> Any:
        value = getattr(obj, name, None)
        return value() if callable(value) else value

    def _handle_com_operation(self, name: str, operation: Any) -> AdapterResult[Any]:
        try:
            return AdapterResult(status=AdapterResultStatus.SUCCESS, data=operation())
        except Exception as exc:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=f"Error in {name}: {exc}"
            )
