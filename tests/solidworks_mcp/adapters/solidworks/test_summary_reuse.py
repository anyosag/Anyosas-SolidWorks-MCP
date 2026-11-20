"""A feature's after summary as the next feature's before (``summary_reuse``).

Inside ``execute_workflow`` a feature's before-read takes the summary the
runner offered its step; every other read reads, and the step's last one is
what the runner may keep. Outside a workflow every read reads.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import summary_reuse
from solidworks_mcp.adapters.solidworks.summary_reuse import (
    SummaryReuse,
    read_summary,
    reusable,
    reusing_summaries,
)

_BUSY = AdapterResult(status=AdapterResultStatus.ERROR, error="SolidWorks is busy")


def _measured(
    volume: float | None = 4800.0, **changes: Any
) -> AdapterResult[dict[str, Any]]:
    """A part's summary, measured from its one solid body."""
    data = {
        "document_type": "part",
        "volume_mm3": volume,
        "solid_body_count": 1,
        "bounding_box_method": "extreme_points",
        "bounding_box_mm": {"min": [0, 0, 0], "max": [40, 30, 4], "size": [40, 30, 4]},
    }
    return AdapterResult(status=AdapterResultStatus.SUCCESS, data={**data, **changes})


def _reader(*summaries: AdapterResult[Any]) -> Any:
    """An adapter whose summary reads give ``summaries`` in turn."""
    return SimpleNamespace(
        get_model_geometry_summary=AsyncMock(side_effect=list(summaries))
    )


def test_a_parts_summary_measured_from_its_bodies_can_be_reused() -> None:
    assert reusable(_measured())


@pytest.mark.parametrize(
    "summary",
    [
        None,
        _BUSY,
        AdapterResult(status=AdapterResultStatus.SUCCESS, data="Part1"),
        _measured(document_type="assembly"),
        _measured(bounding_box_method="part_box"),  # no body: GetPartBox's box
        _measured(volume=None),
        _measured(solid_body_count=None),
    ],
)
def test_any_other_summary_is_read_again(summary: Any) -> None:
    assert not reusable(summary)


async def test_outside_a_workflow_every_read_reads() -> None:
    adapter = _reader(_measured(4800.0), _measured(4700.0))

    before = await read_summary(adapter, before_feature=True)
    after = await read_summary(adapter)

    assert [before.data["volume_mm3"], after.data["volume_mm3"]] == [4800.0, 4700.0]
    assert adapter.get_model_geometry_summary.await_count == 2


async def test_a_before_read_takes_the_offered_summary_once() -> None:
    adapter = _reader(_measured(4790.0), _measured(4780.0))

    with reusing_summaries() as reuse:
        reuse.kept = _measured(4800.0)
        reuse.start_step(offer=True)
        before = await read_summary(adapter, before_feature=True)
        again = await read_summary(adapter, before_feature=True)
        after = await read_summary(adapter)
        reuse.finish_step(keeps_solids=False, valid=True)

    assert before.data["volume_mm3"] == 4800.0
    assert again.data["volume_mm3"] == 4790.0  # the offer was taken
    assert adapter.get_model_geometry_summary.await_count == 2
    # The feature read the part before and after: its after summary is kept.
    assert reuse.kept is after


async def test_the_summary_taken_is_a_copy() -> None:
    kept = _measured(4800.0)

    with reusing_summaries() as reuse:
        reuse.kept = kept
        reuse.start_step(offer=True)
        taken = await read_summary(_reader(), before_feature=True)

    taken.data["bounding_box_mm"]["size"][2] = 99
    assert kept.data["bounding_box_mm"]["size"] == [40, 30, 4]


async def test_a_step_offered_nothing_reads_its_before() -> None:
    """A step on its own document: the summary kept is another document's."""
    adapter = _reader(_measured(100.0))

    with reusing_summaries() as reuse:
        reuse.kept = _measured(4800.0)
        reuse.start_step(offer=False)
        before = await read_summary(adapter, before_feature=True)

    assert before.data["volume_mm3"] == 100.0


@pytest.mark.parametrize(
    ("keeps_solids", "valid", "kept"),
    [
        (True, True, 4800.0),  # a sketch: the summary still holds
        (True, False, None),  # failed, or ran on its own document
        (False, False, None),
        (False, True, None),  # changed solids without reading the part
    ],
)
def test_what_a_step_leaves_kept(keeps_solids: bool, valid: bool, kept: Any) -> None:
    reuse = SummaryReuse()
    reuse.kept = _measured(4800.0)
    reuse.start_step(offer=True)

    reuse.finish_step(keeps_solids=keeps_solids, valid=valid)

    assert (reuse.kept.data["volume_mm3"] if reuse.kept else None) == kept


@pytest.mark.parametrize(
    "reads",
    [
        # An after read with no before-read (an expect check after the feature).
        [(False, _measured(4700.0))],
        # A before-read, then an after read that failed.
        [(True, None), (False, _BUSY)],
        # A before-read and no read after it.
        [(True, None)],
    ],
)
async def test_a_feature_without_a_good_read_before_and_after_drops_it(
    reads: list[tuple[bool, Any]],
) -> None:
    adapter = _reader(*(summary for _, summary in reads if summary is not None))

    with reusing_summaries() as reuse:
        reuse.kept = _measured(4800.0)
        reuse.start_step(offer=True)
        for before_feature, _summary in reads:
            await read_summary(adapter, before_feature=before_feature)
        reuse.finish_step(keeps_solids=False, valid=True)

    assert reuse.kept is None


async def test_the_summaries_end_with_their_block_even_on_an_error() -> None:
    with pytest.raises(RuntimeError), reusing_summaries() as reuse:
        reuse.kept = _measured(4800.0)
        reuse.start_step(offer=True)
        raise RuntimeError("a step blew up")

    adapter = _reader(_measured(4700.0))
    before = await read_summary(adapter, before_feature=True)

    assert summary_reuse._REUSE.get() is None
    assert before.data["volume_mm3"] == 4700.0
