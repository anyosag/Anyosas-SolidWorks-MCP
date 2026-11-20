"""The tool list stays within its size budget, so growing it is a deliberate choice.

Every tool's description and input schema goes into the agent's context on
each turn a client sends the whole list. The budgets sit just above the size
measured when they were set (``src/utils/measure_tool_cost.py``); when a change
needs more, raise them here in the same pull request, and say why.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "src" / "utils" / "measure_tool_cost.py"

#: Characters of every listed description and input schema (all groups),
#: measured at 240,653 (109,603 + 131,050) on 2026-11-19.
TOTAL_BUDGET = 245_000
#: Characters of the input schemas alone, measured at 131,050.
SCHEMA_BUDGET = 133_500
#: Characters of the core group, measured at 65,755.
CORE_BUDGET = 67_000


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("measure_tool_cost", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def measure_tool_cost() -> ModuleType:
    return _script()


@pytest.mark.asyncio
async def test_the_tool_list_stays_within_budget(measure_tool_cost) -> None:
    report = await measure_tool_cost.measure(replies=False)

    assert report.total <= TOTAL_BUDGET, measure_tool_cost.render(report, top=10)
    assert report.schemas <= SCHEMA_BUDGET, measure_tool_cost.render(report, top=10)
    assert report.by("group")["core"] <= CORE_BUDGET, measure_tool_cost.render(report)


@pytest.mark.asyncio
async def test_core_alone_lists_only_core(measure_tool_cost) -> None:
    report = await measure_tool_cost.measure("core", replies=False)

    assert {tool.group for tool in report.tools} == {"core"}
    assert report.total <= CORE_BUDGET


@pytest.mark.asyncio
async def test_typical_replies_are_measured(measure_tool_cost) -> None:
    report = await measure_tool_cost.measure("core,features,assembly")

    assert set(report.replies) >= {"create_part", "create_extrusion", "list_features"}
    assert all(size > 0 for size in report.replies.values())
    assert "typical replies" in measure_tool_cost.render(report)


def test_the_script_prints_text_and_json(measure_tool_cost, capsys) -> None:
    assert measure_tool_cost.main(["--no-replies", "--top", "3"]) == 0
    text = capsys.readouterr().out
    assert "schemas:" in text and "largest 3 tools" in text

    assert measure_tool_cost.main(["--no-replies", "--json", "--groups", "core"]) == 0
    assert '"groups"' in capsys.readouterr().out


def test_tokens_and_tags(measure_tool_cost) -> None:
    assert measure_tool_cost.tokens(400) == 100
    assert measure_tool_cost.family_and_group({"bodies", "group:bodies"}) == (
        "bodies",
        "bodies",
    )
    assert measure_tool_cost.family_and_group(set()) == ("unknown", "unknown")
