"""The analysis mixin: each method runs for the right document types only."""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import analysis_methods as am
from solidworks_mcp.adapters.solidworks import density, plane_volume
from solidworks_mcp.adapters.solidworks import rotation_clearance as rc


class _Model:
    def __init__(self, doc_type: int) -> None:
        self.doc_type = doc_type

    def GetType(self) -> int:  # noqa: N802
        return self.doc_type


class _Harness(am.SolidWorksAnalysisMixin):
    def __init__(self, doc_type: int | None) -> None:
        self.currentModel = _Model(doc_type) if doc_type else None
        self.operations: list[str] = []

    def _handle_com_operation(self, name: str, operation: Any) -> AdapterResult[Any]:
        self.operations.append(name)
        try:
            data = operation()
        except Exception as exc:  # noqa: BLE001 - mirrors PyWin32Adapter
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=f"Error in {name}: {exc}"
            )
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=data)

    def _attempt(self, operation: Any, default: Any = None) -> Any:
        try:
            return operation()
        except Exception:  # noqa: BLE001
            return default

    def _get_attr_or_call(self, obj: Any, name: str) -> Any:
        value = getattr(obj, name)
        return value() if callable(value) else value


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[Any, ...]]]:
    seen: list[tuple[str, tuple[Any, ...]]] = []

    def record(name: str) -> Any:
        def work(adapter: Any, *args: Any) -> dict[str, Any]:
            seen.append((name, args))
            return {"ran": name}

        return work

    monkeypatch.setattr(rc, "body_rotation_clearance", record("bodies"))
    monkeypatch.setattr(rc, "component_rotation_clearance", record("components"))
    monkeypatch.setattr(plane_volume, "volume_by_plane", record("volume"))
    monkeypatch.setattr(density, "set_density", record("density"))
    return seen


async def test_a_part_turns_a_body_with_the_defaults(calls: list[Any]) -> None:
    harness = _Harness(1)

    result = await harness.rotation_clearance("Lid", "Base", [0.0, 0.0, 1.0])

    assert result.data == {"ran": "bodies"}
    assert calls == [
        (
            "bodies",
            (
                "Lid",
                "Base",
                [0.0, 0.0, 1.0],
                [0.0, 0.0, 0.0],
                [-180.0, 180.0],
                5.0,
                0.1,
            ),
        )
    ]
    assert harness.operations == ["rotation_clearance"]


async def test_an_assembly_turns_a_component(calls: list[Any]) -> None:
    result = await _Harness(2).rotation_clearance(
        "lid-1", "base-1", [1, 0, 0], [0, 5, 0], [0, 170], 2.0, 0.05
    )

    assert result.data == {"ran": "components"}
    assert calls[0][1][3:] == ([0, 5, 0], [0, 170], 2.0, 0.05)


async def test_the_volume_and_the_density_run_on_a_part(calls: list[Any]) -> None:
    harness = _Harness(1)

    volume = await harness.volume_by_plane("Top Plane", None, None, ["A"], True, 1.15)
    dense = await harness.set_density(1.15, True)

    assert volume.data == {"ran": "volume"} and dense.data == {"ran": "density"}
    assert calls == [
        ("volume", ("Top Plane", None, None, ["A"], True, 1.15)),
        ("density", (1.15, True)),
    ]


@pytest.mark.parametrize(
    ("doc_type", "method", "error"),
    [
        (None, "volume_by_plane", "No active model"),
        (3, "rotation_clearance", "needs an active part or assembly document"),
        (
            2,
            "volume_by_plane",
            "needs an active part document (active document is assembly)",
        ),
        (3, "set_density", "(active document is drawing)"),
    ],
)
async def test_the_wrong_document_is_refused(
    calls: list[Any], doc_type: int | None, method: str, error: str
) -> None:
    args: dict[str, tuple[Any, ...]] = {
        "rotation_clearance": ("a", "b", [0, 0, 1]),
        "volume_by_plane": ("Top Plane",),
        "set_density": (1.15,),
    }

    result = await getattr(_Harness(doc_type), method)(*args[method])

    assert result.status == AdapterResultStatus.ERROR
    assert error in (result.error or "")
    assert calls == []


async def test_an_expected_failure_reads_as_a_sentence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: Any) -> dict[str, Any]:
        raise LookupError("Plane not found: Fill line.")

    monkeypatch.setattr(plane_volume, "volume_by_plane", fail)

    result = await _Harness(1).volume_by_plane("Fill line")

    assert result.error == "Plane not found: Fill line."
