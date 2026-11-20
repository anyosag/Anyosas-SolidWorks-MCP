"""A fillet or chamfer inside a workflow takes the last feature's after summary.

The adapter reads the part before and after a fillet or chamfer, without a
rebuild so the selection survives. Inside ``execute_workflow`` the read
before is the summary the runner offered (``summary_reuse``), so only the
read after reaches SolidWorks.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import features
from solidworks_mcp.adapters.solidworks.summary_reuse import reusing_summaries

from .test_features import _build_adapter
from .test_features_edge_facts import _feature


def _measured(volume: float) -> AdapterResult[dict[str, Any]]:
    """A part's summary, measured from its one solid body."""
    return AdapterResult(
        status=AdapterResultStatus.SUCCESS,
        data={
            "document_type": "part",
            "volume_mm3": volume,
            "solid_body_count": 1,
            "bounding_box_method": "extreme_points",
            "bounding_box_mm": {"size": [40.0, 30.0, 4.0]},
        },
    )


@pytest.mark.parametrize(
    ("method", "build", "kind", "arguments"),
    [
        ("add_fillet", "_add_fillet_impl", "Fillet", (0.8, [])),
        ("add_chamfer", "_add_chamfer_impl", "Chamfer", (0.5, [], 45.0, False)),
    ],
)
async def test_an_edge_feature_in_a_workflow_reads_the_part_only_after(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    build: str,
    kind: str,
    arguments: tuple[Any, ...],
) -> None:
    adapter = _build_adapter(monkeypatch)
    adapter.get_model_geometry_summary = AsyncMock(return_value=_measured(4797.4))
    monkeypatch.setattr(features, build, lambda *_a: _feature(kind))

    with reusing_summaries() as reuse:
        reuse.kept = _measured(4800.0)
        reuse.start_step(offer=True)
        result = await getattr(adapter, method)(*arguments)
        reuse.finish_step(keeps_solids=False, valid=True)

    assert result.is_success, result.error
    adapter.get_model_geometry_summary.assert_awaited_once()
    assert result.data.properties["geometry_before"]["volume_mm3"] == 4800.0
    assert result.data.properties["geometry_after"]["volume_mm3"] == 4797.4
    # Its after summary is the next feature's before.
    assert reuse.kept.data["volume_mm3"] == 4797.4
