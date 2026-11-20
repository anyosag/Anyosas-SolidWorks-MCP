"""Equations and global variables through a fake ``IEquationMgr`` (``parametric-equations``)."""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import equations as eq
from solidworks_mcp.adapters.solidworks import rebuild_inspector
from solidworks_mcp.adapters.solidworks.equations import SolidWorksEquationsMixin
from solidworks_mcp.adapters.solidworks.rebuild_diagnostics import RawRebuildIssue
from solidworks_mcp.tools.error_classifier import ErrorClassifier

from .fake_equations import FakeAdapter, FakeModel


class _Harness(SolidWorksEquationsMixin, FakeAdapter):
    pass


def _kind(error: str | None) -> str:
    return ErrorClassifier().classify(error or "").error_type


@pytest.fixture(autouse=True)
def _no_rebuild_issues(monkeypatch: pytest.MonkeyPatch) -> list[RawRebuildIssue]:
    issues: list[RawRebuildIssue] = []
    monkeypatch.setattr(
        rebuild_inspector, "collect_rebuild_issues", lambda _a, _m: list(issues)
    )
    return issues


# ---- pure helpers ----------------------------------------------------------


def test_names_with_at_signs_spaces_and_functions() -> None:
    text = '"D1@Boss Extrude" = iif("D1@Sketch 2" > 5mm, 2 * "W", "H" + 1in)'

    assert eq.left_side(text) == "D1@Boss Extrude"
    assert eq.equation_names(text) == ["D1@Boss Extrude", "D1@Sketch 2", "W", "H"]
    assert eq.right_names(text) == ["D1@Sketch 2", "W", "H"]
    assert eq.left_side('  "W"=25mm') == "W"
    assert eq.name_of("W = 40") is None
    assert eq.right_names("no equation") == []
    assert eq.equation_names(None) == []


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("W = 40", '"W" = 40'),
        ('W" = 40', '"W" = 40'),
        ("40", '"W" = 40'),
        ('"D1@Sketch1" 40', '"W" = 40'),
    ],
)
def test_a_missing_left_side_shows_the_form(text: str, shown: str) -> None:
    with pytest.raises(ValueError, match="Invalid request") as raised:
        eq.left_side(text)

    assert shown in str(raised.value)


def test_same_equation_ignores_whitespace_and_case() -> None:
    assert eq.same_equation('"W" = 40mm', '"w"=40MM')
    assert not eq.same_equation('"W" = 40', '"W" = 41')
    assert not eq.same_equation(None, '"W" = 40')


def test_short_names_and_the_document_suffix() -> None:
    assert eq.short_name("D1@Sketch1@Part1.Part") == "D1@Sketch1"
    assert eq.short_name("D1@Sketch1") == "D1@Sketch1"


def test_is_broken() -> None:
    def resolves(name: str) -> bool:
        return name == "D1@Sketch1"

    assert not eq.is_broken(["D1@Sketch1", "w"], ["W"], resolves)
    assert eq.is_broken(["D1@Sketch2"], ["W"], resolves)
    assert not eq.is_broken(["Suppressed"], [], resolves)
    assert not eq.is_broken([], [], resolves)


def test_check_equations() -> None:
    assert eq.check_equations('"W" = 40') == ["W"]
    assert eq.check_equations(['"W" = 40', '"D1@Sketch1" = "W"']) == [
        "W",
        "D1@Sketch1",
    ]
    with pytest.raises(ValueError, match="at least one"):
        eq.check_equations([])
    with pytest.raises(ValueError, match='both set "w"'):
        eq.check_equations(['"W" = 40', '"w" = 41'])
    with pytest.raises(ValueError, match="both set"):
        eq.check_equations(['"D1@Sketch1" = 1', '"D1@Sketch1@Part1.Part" = 2'])


# ---- list_equations --------------------------------------------------------


async def test_a_global_variable_drives_a_dimension() -> None:
    model = FakeModel(['"W" = 40', '"D1@Sketch1" = "W"'])

    result = await _Harness(model).list_equations()

    assert result.is_success, result.error
    data = result.data
    assert data["count"] == 2
    assert data["automatic_solve_order"] is False
    assert data["document_units"] == "mm"
    first, second = data["equations"]
    assert first == {
        "index": 0,
        "equation": '"W" = 40',
        "name": "W",
        "value": 40.0,
        "global": True,
        "broken": False,
    }
    assert (second["name"], second["global"], second["broken"]) == (
        "D1@Sketch1",
        False,
        False,
    )


async def test_an_equation_naming_a_deleted_sketch_is_broken() -> None:
    model = FakeModel(['"D1@Boss" = 2 * "D1@Sketch2"'], units=3)

    data = (await _Harness(model).list_equations()).data

    assert data["equations"][0]["broken"] is True
    assert data["document_units"] == "in"


async def test_a_model_without_equations() -> None:
    data = (await _Harness(FakeModel()).list_equations()).data

    assert (data["count"], data["equations"]) == (0, [])


@pytest.mark.parametrize(
    ("model", "says"),
    [(None, "no document is open"), (FakeModel(doc_type=3), "is a drawing")],
)
async def test_no_part_or_assembly_is_invalid_input(model: Any, says: str) -> None:
    result = await _Harness(model).list_equations()

    assert result.is_error
    assert says in (result.error or "")
    assert "part or an assembly" in (result.error or "") or "part or assembly" in (
        result.error or ""
    )
    assert _kind(result.error) == "invalid_input"


# ---- set_equations ---------------------------------------------------------


async def test_a_variable_and_the_dimension_it_drives_are_added() -> None:
    model = FakeModel()

    result = await _Harness(model).set_equations(['"W" = 45', '"D1@Sketch1" = "W"'])

    assert result.is_success, result.error
    assert result.data["equations"] == [
        {"equation": '"W" = 45', "index": 0, "replaced": False},
        {"equation": '"D1@Sketch1" = "W"', "index": 1, "replaced": False},
    ]
    assert result.data["automatic_solve_order_was"] is False
    assert result.data["failing_features"] == []
    assert result.data["document_units"] == "mm"
    assert model.mgr.AutomaticSolveOrder is True
    assert model.mgr.add2_calls == [
        (-1, '"W" = 45', False),
        (-1, '"D1@Sketch1" = "W"', False),
    ]
    assert model.mgr.add3_calls == []
    assert model.rebuilds == [False]


async def test_replacing_keeps_the_index() -> None:
    model = FakeModel(['"H" = 5', '"W" = 45'])

    result = await _Harness(model).set_equations(['"w" = 50'])

    assert result.data["equations"] == [
        {"equation": '"w" = 50', "index": 1, "replaced": True}
    ]
    assert model.mgr.texts == ['"H" = 5', '"w" = 50']
    assert model.mgr.add2_calls == []


async def test_a_bare_string_is_a_list_of_one() -> None:
    model = FakeModel()

    result = await _Harness(model).set_equations('"W" = 45')  # type: ignore[arg-type]

    assert result.is_success, result.error
    assert model.mgr.texts == ['"W" = 45']


async def test_add2_appends_on_a_single_configuration_part() -> None:
    """Live, ``Add3`` returned -1 on a one-configuration part (pitfall #60)."""
    model = FakeModel(['"H" = 5'])

    result = await _Harness(model).set_equations(['"W" = 45'])

    assert result.is_success, result.error
    assert result.data["equations"] == [
        {"equation": '"W" = 45', "index": 1, "replaced": False}
    ]
    assert model.mgr.texts == ['"H" = 5', '"W" = 45']
    assert model.mgr.add3_calls == []


async def test_add3_only_where_add2_is_missing() -> None:
    model = FakeModel()
    model.mgr.add2_missing = True
    model.mgr.configurations = 2

    result = await _Harness(model).set_equations(['"W" = 45'])

    assert result.is_success, result.error
    assert model.mgr.add3_calls == [
        (-1, '"W" = 45', False, eq.ALL_CONFIGURATIONS, None)
    ]
    assert model.mgr.texts == ['"W" = 45']


@pytest.mark.parametrize("refused", [0, 1, 2])
async def test_a_refusal_at_any_position_undoes_the_whole_list(refused: int) -> None:
    """0 replaces "W" (SolidWorks keeps the old text); 1 and 2 are adds."""
    model = FakeModel(['"W" = 40'])
    equations = ['"W" = 45', '"D1@Sketch1" = "W"', '"H" = 5']
    if refused == 0:
        model.mgr.keep_old.add(equations[0])
    else:
        model.mgr.refuse_add.add(equations[refused])

    result = await _Harness(model).set_equations(equations)

    assert result.is_error
    assert f"refused the equation {equations[refused]}" in (result.error or "")
    assert _kind(result.error) == "invalid_input"
    assert model.mgr.texts == ['"W" = 40']
    assert model.mgr.AutomaticSolveOrder is False
    assert model.rebuilds == [False]


async def test_the_spec_scenario_the_middle_one_is_refused() -> None:
    model = FakeModel(['"W" = 40'])
    model.mgr.refuse_add.add('"D1@NoSuchSketch" = "W"')

    result = await _Harness(model).set_equations(
        ['"W" = 45', '"D1@NoSuchSketch" = "W"', '"H" = 5']
    )

    assert 'refused the equation "D1@NoSuchSketch" = "W"' in (result.error or "")
    assert _kind(result.error) == "invalid_input"
    assert model.mgr.texts == ['"W" = 40']
    listed = (await _Harness(model).list_equations()).data
    assert [e["equation"] for e in listed["equations"]] == ['"W" = 40']


async def test_a_replace_that_reads_back_old_text_is_refused() -> None:
    model = FakeModel(['"W" = 40', '"H" = 5'])
    model.mgr.keep_old.add('"H" = 6 +')

    result = await _Harness(model).set_equations(['"W" = 45', '"H" = 6 +'])

    assert result.is_error
    assert "reads back as" in (result.error or "")
    assert model.mgr.texts == ['"W" = 40', '"H" = 5']


async def test_a_bad_left_side_sends_nothing() -> None:
    model = FakeModel()

    result = await _Harness(model).set_equations(["W = 40"])

    assert result.is_error
    assert '"W" = 40' in (result.error or "")
    assert _kind(result.error) == "invalid_input"
    assert model.mgr.reads == [] and model.mgr.counts == 0
    assert model.mgr.AutomaticSolveOrder is False


async def test_a_rebuild_that_fails_a_feature_is_still_a_success(
    _no_rebuild_issues: list[RawRebuildIssue],
) -> None:
    _no_rebuild_issues.append(
        RawRebuildIssue(feature="Cut-Extrude1", type="ICE", code=2, is_warning=False)
    )
    _no_rebuild_issues.append(
        RawRebuildIssue(feature="Fillet1", type="Fillet", code=2, is_warning=True)
    )

    result = await _Harness(FakeModel()).set_equations(['"D1@Boss" = 50'])

    assert result.is_success, result.error
    assert [f["feature"] for f in result.data["failing_features"]] == ["Cut-Extrude1"]


# ---- delete_equation -------------------------------------------------------


async def test_deleting_a_broken_equation_by_index() -> None:
    model = FakeModel(['"W" = 40', '"D1@Sketch1" = "W"', '"D1@Boss" = "D1@Gone"'])

    result = await _Harness(model).delete_equation(2)

    assert result.is_success, result.error
    assert result.data == {
        "deleted": '"D1@Boss" = "D1@Gone"',
        "count": 2,
        "failing_features": [],
        "left_broken": [],
    }
    assert model.rebuilds == [False]


@pytest.mark.parametrize("name", ['"w"', "W", "0"])
async def test_deleting_by_name(name: str) -> None:
    model = FakeModel(['"W" = 40', '"D1@Sketch1" = "W"'])

    result = await _Harness(model).delete_equation(name)

    assert result.data["deleted"] == '"W" = 40'
    assert result.data["left_broken"] == ['"D1@Sketch1" = "W"']
    assert model.mgr.texts == ['"D1@Sketch1" = "W"']


@pytest.mark.parametrize("equation", ["Q", 5])
async def test_an_unknown_equation_is_not_found(equation: Any) -> None:
    model = FakeModel(['"W" = 40'])

    result = await _Harness(model).delete_equation(equation)

    assert result.is_error
    assert '0: "W" = 40' in (result.error or "")
    assert _kind(result.error) == "not_found"
    assert model.mgr.texts == ['"W" = 40']


async def test_a_delete_solidworks_ignores_is_an_error() -> None:
    model = FakeModel(['"W" = 40'])
    model.mgr.Delete = lambda _i: -1  # type: ignore[method-assign]

    result = await _Harness(model).delete_equation("W")

    assert "did not delete" in (result.error or "")


# ---- driven_map ------------------------------------------------------------


def test_driven_map_without_equations_is_one_count() -> None:
    model = FakeModel()

    assert eq.driven_map(FakeAdapter(model), model) == {}
    assert model.mgr.counts == 1
    assert model.mgr.reads == []


def test_driven_map_maps_dimensions_only() -> None:
    model = FakeModel(['"W" = 40', '"D1@Sketch1@Part1.Part" = "W"'])

    driven = eq.driven_map(FakeAdapter(model), model)

    assert driven == {"d1@sketch1": '"D1@Sketch1@Part1.Part" = "W"'}
    assert eq.driven_by(driven, "D1@SKETCH1") == '"D1@Sketch1@Part1.Part" = "W"'
    assert eq.driven_by(driven, "D2@Sketch1") is None
    assert eq.driven_by(driven, None) is None


def test_driven_map_without_a_manager() -> None:
    model = FakeModel()
    model.GetEquationMgr = lambda: None  # type: ignore[method-assign]

    assert eq.driven_map(FakeAdapter(model), model) == {}
