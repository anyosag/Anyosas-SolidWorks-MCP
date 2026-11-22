"""``check_interference`` intersects bodies in the kernel; SolidWorks' detection is the fallback.

The manager (``InterferenceDetectionManager``) put SolidWorks' Interference
Detection on screen at every call. It now runs only when ``coincident`` asks
for touching faces, or the kernel can't answer (a lightweight component), and
the reply says so (``method``, ``note``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import limits
from solidworks_mcp.adapters.solidworks.interference import (
    BODY_INTERSECTION,
    COINCIDENT_REASON,
    INTERFERENCE_DETECTION,
    fallback_note,
)
from solidworks_mcp.adapters.solidworks.io import SolidWorksIOMixin

from .fake_bodies import Com
from .fake_interference import Adapter, World, cube


@pytest.fixture(autouse=True)
def _wrapping_is_the_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "solidworks_mcp.adapters.solidworks.io._dynamic",
        SimpleNamespace(Dispatch=lambda obj: obj),
    )


class _Manager(Com):
    """``IInterferenceDetectionMgr``: one interference of two components, 0.5 mm3."""

    def __init__(self, names: tuple[str, str]) -> None:
        super().__init__()
        self.names = names
        self.done = False

    def GetInterferenceCount(self) -> int:  # noqa: N802 - COM name
        return 1

    def GetInterferences(self) -> tuple[Any, ...]:  # noqa: N802 - COM name
        return (
            SimpleNamespace(
                Volume=5e-10,
                GetComponentCount=lambda: 2,
                Components=[SimpleNamespace(Name2=n) for n in self.names],
            ),
        )

    def Done(self) -> None:  # noqa: N802 - COM name
        self.done = True


class _Harness(Adapter):
    """The adapter members ``check_interference`` uses, on the fake assembly."""

    def __init__(self, world: World) -> None:
        super().__init__(world)
        self.world = world
        self.manager = world.manager = _Manager(("block-1", "block-2"))
        self.currentModel = world.assembly

    @property
    def manager_asked(self) -> int:
        return self.world.manager_asked

    def _get_attr_or_call(self, obj: Any, name: str) -> Any:
        value = getattr(obj, name)
        return value() if callable(value) else value

    def _handle_com_operation(self, name: str, callback: Any) -> AdapterResult[Any]:
        try:
            return AdapterResult(status=AdapterResultStatus.SUCCESS, data=callback())
        except Exception as exc:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=f"Error in {name}: {exc}"
            )


async def _check(harness: _Harness, options: dict[str, Any] | None = None) -> Any:
    mixin = SolidWorksIOMixin()
    mixin._adapter = lambda _self: harness  # type: ignore[method-assign]
    return await mixin.check_interference(options or {})


def _overlapping() -> World:
    world = World()
    world.add("block-1", world.body(cube((0, 0, 0), 10)))
    world.add("block-2", world.body(cube((0, 0, 0), 10)), origin_mm=(5.0, 0.0, 0.0))
    return world


async def test_the_bodies_are_intersected_and_no_detection_is_shown() -> None:
    harness = _Harness(_overlapping())

    result = await _check(harness)

    assert result.is_success
    assert result.data == {
        "interference_found": True,
        "interference_count": 1,
        "interferences": [
            {
                "components": ["block-1", "block-2"],
                "component_count": 2,
                "volume_mm3": 500.0,
            }
        ],
        "coincident_treated_as_interference": False,
        "scope": "whole assembly",
        "tolerance_applied": None,
        "method": BODY_INTERSECTION,
    }
    assert harness.manager_asked == 0


async def test_the_options_reach_the_bodies_check() -> None:
    world = _overlapping()
    world.add("hidden-1", world.body(cube((0, 0, 0), 10)), visible=0)
    world.add(
        "bracket-1", world.body(cube((50, 0, 0), 10)), world.body(cube((55, 0, 0), 10))
    )
    harness = _Harness(world)

    everything = await _check(harness)
    shown_parts = await _check(
        harness, {"ignore_hidden": True, "include_multibody": False}
    )
    listed = await _check(harness, {"components": ["block-2", "block-1"]})

    assert everything.data["interference_count"] == 4  # 3 with hidden-1, 1 own
    assert [i["components"] for i in shown_parts.data["interferences"]] == [
        ["block-1", "block-2"]
    ]
    assert listed.data["scope"] == ["block-1", "block-2"]
    assert listed.data["interference_count"] == 1
    assert harness.manager_asked == 0


async def test_touching_faces_are_left_to_solidworks_detection() -> None:
    world = _overlapping()
    world.assembly.components_error = AssertionError("the bodies were read")
    harness = _Harness(world)

    result = await _check(harness, {"coincident": True})

    assert result.is_success
    assert result.data["method"] == INTERFERENCE_DETECTION
    assert result.data["note"] == fallback_note(COINCIDENT_REASON)
    assert result.data["coincident_treated_as_interference"] is True
    assert harness.manager.TreatCoincidenceAsInterference is True
    assert harness.manager.done is True


async def test_what_the_kernel_cant_answer_goes_to_solidworks_detection() -> None:
    world = _overlapping()
    world.add("nut-1", state=1)  # lightweight: its bodies aren't loaded
    harness = _Harness(world)

    result = await _check(harness, {"ignore_hidden": True})

    assert result.is_success
    assert result.data["method"] == INTERFERENCE_DETECTION
    assert result.data["note"] == fallback_note(
        "nut-1 is lightweight, so its bodies aren't loaded"
    )
    assert result.data["interference_count"] == 1
    assert result.data["interferences"][0]["volume_mm3"] == 0.5
    assert harness.manager.IgnoreHiddenBodies is True
    assert harness.manager.IncludeMultibodyPartInterferences is True
    assert harness.manager.done is True
    assert world.booleans == 0


async def test_a_stopped_check_is_not_handed_to_solidworks_detection(
    monkeypatch,
) -> None:
    def stop(doing: str) -> None:
        raise limits.OperationStopped(f"Stopped {doing}")

    monkeypatch.setattr(limits, "check", stop)
    harness = _Harness(_overlapping())

    result = await _check(harness)

    assert result.is_error
    assert "Stopped checking interference" in (result.error or "")
    assert harness.manager_asked == 0
