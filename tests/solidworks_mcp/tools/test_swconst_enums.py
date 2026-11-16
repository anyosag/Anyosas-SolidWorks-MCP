"""Searching the swconst enum index: by name, part of a name, Enum.Member or value."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from solidworks_mcp.tools import swconst_enums
from solidworks_mcp.tools.swconst_enums import load_enum_index, search_enums

INDEX: dict[str, Any] = {
    "source": {"solidworks_version": "SOLIDWORKS 2026 SP4.1"},
    "enums": {
        "swBodyOperationType_e": {"SWBODYINTERSECT": 15901, "SWBODYCUT": 15902},
        "swInsertPartOptions_e": {
            "swInsertPartImportSolids": 1,
            "swInsertPartImportSurfaces": 2,
        },
        "swDocumentTypes_e": {"swDocNONE": 0, "swDocPART": 1},
        "swOtherCut_e": {"swCutThrough": 1},
    },
}


def test_the_committed_index_has_the_values_the_session_needed() -> None:
    index = load_enum_index()

    assert index["source"]["solidworks_version"].startswith("SOLIDWORKS ")
    enums = index["enums"]
    assert enums["swBodyOperationType_e"] == {
        "SWBODYINTERSECT": 15901,
        "SWBODYCUT": 15902,
        "SWBODYADD": 15903,
    }
    assert enums["swInsertPartOptions_e"]["swInsertPartImportSolids"] == 1


def test_a_changed_file_is_read_again(tmp_path: Path) -> None:
    path = tmp_path / "enums.json"
    path.write_text(json.dumps({"source": {}, "enums": {"a_e": {"x": 1}}}))
    assert load_enum_index(path)["enums"] == {"a_e": {"x": 1}}

    path.write_text(json.dumps({"source": {}, "enums": {"b_e": {"yy": 22}}}))
    assert load_enum_index(path)["enums"] == {"b_e": {"yy": 22}}


def test_a_file_that_is_not_an_index_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "enums.json"
    path.write_text("[1, 2]")

    with pytest.raises(ValueError, match="not a swconst enum index"):
        load_enum_index(path)


def test_an_exact_enum_name_lists_its_members_and_not_them_again() -> None:
    found = search_enums(INDEX, "swbodyoperationtype_e")

    assert [e.enum for e in found.enums] == ["swBodyOperationType_e"]
    assert found.enums[0].members == {"SWBODYINTERSECT": 15901, "SWBODYCUT": 15902}
    assert found.members == []


def test_the_enum_name_without_its_suffix_or_with_a_namespace() -> None:
    assert search_enums(INDEX, "swBodyOperationType").enums[0].member_count == 2
    found = search_enums(INDEX, "SolidWorks.Interop.swconst.swDocumentTypes_e")
    assert [e.enum for e in found.enums] == ["swDocumentTypes_e"]


def test_part_of_a_name_finds_enums_and_members() -> None:
    found = search_enums(INDEX, "cut")

    assert [e.enum for e in found.enums] == ["swOtherCut_e"]
    # swOtherCut_e is listed in full, so its swCutThrough isn't repeated.
    assert [(m.enum, m.member, m.value) for m in found.members] == [
        ("swBodyOperationType_e", "SWBODYCUT", 15902)
    ]


def test_an_exact_member_ranks_before_one_that_contains_it() -> None:
    index = {"enums": {"a_e": {"xSWBODYCUTx": 7}, "b_e": {"SWBODYCUT": 3}}}

    found = search_enums(index, "SWBODYCUT")

    assert [m.member for m in found.members] == ["SWBODYCUT", "xSWBODYCUTx"]


def test_enum_dot_member_finds_one_member() -> None:
    found = search_enums(INDEX, "swBodyOperationType_e.SWBODYCUT")
    assert [(m.member, m.value) for m in found.members] == [("SWBODYCUT", 15902)]

    by_short_name = search_enums(INDEX, "swBodyOperationType.swbodycut")
    assert [m.value for m in by_short_name.members] == [15902]


def test_enum_dot_member_falls_back_to_the_member_name() -> None:
    found = search_enums(INDEX, "swNoSuchEnum_e.swDocPART")

    assert [(m.enum, m.member) for m in found.members] == [
        ("swDocumentTypes_e", "swDocPART")
    ]


def test_a_number_finds_the_members_with_that_value() -> None:
    assert [m.member for m in search_enums(INDEX, "15902").members] == ["SWBODYCUT"]
    assert [m.member for m in search_enums(INDEX, "0x3E1D").members] == [
        "SWBODYINTERSECT"
    ]


def test_a_common_value_is_cut_with_a_note() -> None:
    found = search_enums(INDEX, "1", max_results=2)

    assert len(found.members) == 2
    assert found.note == (
        "3 members have the value 1; showing 2. Search the enum's name for its "
        "members instead."
    )


def test_several_words_must_all_appear() -> None:
    found = search_enums(INDEX, "insert part solids")

    assert [m.member for m in found.members] == ["swInsertPartImportSolids"]
    assert search_enums(INDEX, "insert options").enums[0].enum == (
        "swInsertPartOptions_e"
    )


def test_only_the_best_enums_list_their_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(swconst_enums, "_ENUMS_WITH_MEMBERS", 1)
    monkeypatch.setattr(swconst_enums, "_MEMBERS_PER_ENUM", 1)

    found = search_enums(INDEX, "sw")

    assert found.enums[0].members is not None
    assert found.enums[0].members_truncated is True
    assert all(e.members is None for e in found.enums[1:])
    # The truncated enum's own members are still searched.
    assert any(m.enum == found.enums[0].enum for m in found.members)


def test_too_many_matches_are_cut_with_a_note() -> None:
    found = search_enums(INDEX, "sw", max_results=2)

    assert len(found.enums) == 2
    assert found.note is not None
    assert found.note.startswith("Matched 4 enums and ")


def test_nothing_matches() -> None:
    found = search_enums(INDEX, "xyzzy")

    assert (found.enums, found.members, found.note) == ([], [], None)
