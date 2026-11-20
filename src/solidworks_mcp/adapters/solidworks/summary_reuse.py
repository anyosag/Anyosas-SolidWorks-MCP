"""A feature's after summary, reused as the next feature's before in one workflow.

Feature tools read the part's geometry summary (``get_model_geometry_summary``:
volume, solid bodies and their extreme-point box) before and after each
feature to build ``measured_change``; fillets and chamfers read theirs in the
adapter (``features.py``). Each read is a COM operation: its own time and the
0.25 s settle gap after it, about 0.5 s. Most workflows chain sketch, cut,
sketch, cut, and a sketch changes no solid, so one cut's after summary is the
next cut's before.

``execute_workflow`` runs its steps inside :func:`reusing_summaries`. Before
a step it offers the summary it still trusts (:meth:`SummaryReuse.start_step`);
a read marked ``before_feature`` takes it instead of reading
(:func:`read_summary`), and the step's other reads are its after summary.
After the step the runner keeps that after summary, keeps the one it had, or
drops it (:meth:`SummaryReuse.finish_step`); which steps keep it is the
runner's allowlist. Outside a workflow every read reads. The 2026-11-10 review
dropped reuse keyed on the model's update stamp, which sketches move; this
keys on the steps.
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from ..base import AdapterResult

#: A ``get_model_geometry_summary`` result.
Summary = AdapterResult[dict[str, Any]]


def reusable(summary: Summary | None) -> bool:
    """Whether a summary still holds while no step changes a solid body.

    Only a part's summary measured from its solid bodies is: a volume, their
    count and the box from their extreme points. Without a body the box is
    ``GetPartBox``'s, and an assembly's summary counts its components and
    mates; neither is reused.

    Args:
        summary: A summary read, or None.

    Returns:
        bool: True when it can stand for a later read.
    """
    if summary is None or not summary.is_success or not isinstance(summary.data, dict):
        return False
    data = summary.data
    return (
        data.get("document_type") == "part"
        and data.get("bounding_box_method") == "extreme_points"
        and isinstance(data.get("volume_mm3"), int | float)
        and isinstance(data.get("solid_body_count"), int)
    )


class SummaryReuse:
    """One workflow's last after summary, and the summaries its current step read.

    Attributes:
        kept: The last feature's after summary while no step since could have
            changed a solid, else None.
    """

    def __init__(self) -> None:
        self.kept: Summary | None = None
        self._offered: Summary | None = None
        self._read_before = False
        self._after: Summary | None = None

    def start_step(self, offer: bool) -> None:
        """Before a step: offer ``kept`` to its before-read, when ``offer``.

        Args:
            offer: The step runs on the document ``kept`` was read on: it
                gives no ``document`` of its own.
        """
        self._offered = self.kept if offer else None
        self._read_before = False
        self._after = None

    def take(self) -> Summary | None:
        """The summary offered to this step's before-read, once.

        Returns:
            Summary | None: A copy of it (so no reply shares its data), or
            None when none was offered or it was taken.
        """
        offered, self._offered = self._offered, None
        if offered is None:
            return None
        self._read_before = True
        return dataclasses.replace(offered, data=copy.deepcopy(offered.data))

    def note(self, summary: Summary, before_feature: bool) -> None:
        """A summary this step read: its before-read, or its after summary so far.

        Args:
            summary: What the read gave.
            before_feature: It was the read before the step's feature.
        """
        if before_feature:
            self._read_before = True
        else:
            self._after = summary

    def finish_step(self, keeps_solids: bool, valid: bool) -> None:
        """After a step: keep its after summary, keep ``kept``, or drop it.

        A step that changes solids leaves an after summary to keep only when
        it read the part before its feature and again after it; its last read
        after is the part as the step left it.

        Args:
            keeps_solids: The step's tool can't change a solid body (the
                runner's allowlist), so ``kept`` still holds.
            valid: The step succeeded and gave no ``document`` of its own.
        """
        if not valid:
            self.kept = None
        elif not keeps_solids:
            after = self._after if self._read_before else None
            self.kept = after if reusable(after) else None
        self._offered = None
        self._read_before = False
        self._after = None


#: The running workflow's summaries; None outside ``execute_workflow``.
_REUSE: ContextVar[SummaryReuse | None] = ContextVar("summary_reuse", default=None)


@contextmanager
def reusing_summaries() -> Iterator[SummaryReuse]:
    """Share geometry summaries between the steps run inside it.

    Yields:
        SummaryReuse: The summaries, for the runner to offer and settle.
    """
    reuse = SummaryReuse()
    token = _REUSE.set(reuse)
    try:
        yield reuse
    finally:
        _REUSE.reset(token)


async def read_summary(adapter: Any, *, before_feature: bool = False) -> Summary:
    """The part's geometry summary, or the workflow's for a feature's before-read.

    Args:
        adapter: The adapter to read with (``get_model_geometry_summary``).
        before_feature: The read a feature makes before it changes anything.
            Inside ``execute_workflow`` it takes the summary the runner
            offered the step, when there is one, and reads nothing.

    Returns:
        Summary: The summary.
    """
    reuse = _REUSE.get()
    offered = reuse.take() if reuse is not None and before_feature else None
    if offered is not None:
        return offered
    summary: Summary = await adapter.get_model_geometry_summary()
    if reuse is not None:
        reuse.note(summary, before_feature)
    return summary
