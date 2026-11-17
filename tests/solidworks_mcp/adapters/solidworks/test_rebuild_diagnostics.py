"""Tests for the rebuild-error explanation layer (pure, no COM)."""

from __future__ import annotations

from solidworks_mcp.adapters.solidworks import rebuild_diagnostics as rd
from solidworks_mcp.adapters.sw_feature_errors import SW_FEATURE_ERRORS


def test_every_curated_code_exists_in_generated_enum() -> None:
    """Curated hints only key real swFeatureError_e values."""
    assert set(rd._CODE_HINTS) <= set(SW_FEATURE_ERRORS)


def test_generated_enum_has_known_anchor_values() -> None:
    """Spot-check values read from swconst.tlb (R2026x)."""
    assert SW_FEATURE_ERRORS[0] == "swFeatureErrorNone"
    assert SW_FEATURE_ERRORS[1] == "swFeatureErrorUnknown"
    assert SW_FEATURE_ERRORS[13] == "swFeatureErrorFilletNoEdge"
    assert SW_FEATURE_ERRORS[71] == "swFeatureErrorCutNotIntersectModel"


def test_code_name_falls_back_for_unknown_code() -> None:
    """An unmapped code is reported as 'code <n>'."""
    assert rd.code_name(9999) == "code 9999"


def test_exact_code_hint_wins() -> None:
    """A curated code gives its own description and hint."""
    entry = rd.explain(
        rd.RawRebuildIssue(
            "Fillet1",
            "Fillet",
            13,
            False,
            [rd.ParentInfo("Boss-Extrude1", "Extrusion")],
        )
    )
    assert entry["code_name"] == "swFeatureErrorFilletNoEdge"
    assert entry["severity"] == "error"
    assert "edge" in entry["description"].lower()
    assert "select_face_edges" in entry["hint"]
    assert entry["parents"] == [{"name": "Boss-Extrude1", "type": "Extrusion"}]


def test_unknown_code_uses_feature_type_hint() -> None:
    """Code 1 on an extrusion falls back to the extrusion hint."""
    entry = rd.explain(rd.RawRebuildIssue("Boss-Extrude2", "Extrusion", 1, False))
    assert entry["code_name"] == "swFeatureErrorUnknown"
    assert "sketch" in entry["hint"].lower()
    assert "Boss-Extrude2" in entry["description"]


def test_empty_parent_sketch_overrides_other_hints() -> None:
    """A parent sketch with zero segments is named in the hint."""
    entry = rd.explain(
        rd.RawRebuildIssue(
            "Boss-Extrude2",
            "Extrusion",
            1,
            False,
            [rd.ParentInfo("Sketch2", "ProfileFeature", 0)],
        )
    )
    assert "Sketch2" in entry["hint"]
    assert "no geometry" in entry["hint"]
    assert entry["parents"] == [
        {"name": "Sketch2", "type": "ProfileFeature", "segment_count": 0}
    ]


def test_unknown_type_and_code_gets_generic_hint() -> None:
    """Nothing specific known -> generic hint."""
    entry = rd.explain(rd.RawRebuildIssue("Thing1", "Mystery", 4242, True))
    assert entry["hint"] == rd.GENERIC_HINT
    assert entry["severity"] == "warning"
    assert entry["code_name"] == "code 4242"


def test_summarize_errors_warnings_and_clean() -> None:
    """Message and counts reflect severities."""
    err = {"feature": "A", "severity": "error"}
    warn = {"feature": "B", "severity": "warning"}
    assert rd.summarize([err, warn])[:2] == (1, 1)
    assert "A" in rd.summarize([err, warn])[2]
    n_err, n_warn, msg = rd.summarize([warn])
    assert (n_err, n_warn) == (0, 1) and "warning" in msg
    assert rd.summarize([]) == (0, 0, "Rebuilt cleanly; no rebuild errors.")


def test_ice_type_gets_neutral_extrude_hint_and_readable_label() -> None:
    """GetTypeName2 'ICE' (live: a boss extrude) is described as an Extrude,
    and its hint covers boss and cut alike."""
    entry = rd.explain(rd.RawRebuildIssue("Boss-Extrude2", "ICE", 1, False))
    assert entry["description"].startswith("Extrude 'Boss-Extrude2'")
    assert "extrude" in entry["hint"].lower()
    assert "cut" in entry["hint"].lower()


def test_a_warning_reads_as_rebuilt_not_failed() -> None:
    """A feature that only warns did rebuild; its description says so."""
    warned = rd.explain(rd.RawRebuildIssue("Chamfer2", "Chamfer", 9999, True))
    failed = rd.explain(rd.RawRebuildIssue("Chamfer2", "Chamfer", 9999, False))

    assert warned["description"] == (
        "Chamfer 'Chamfer2' rebuilt with a warning (code 9999)."
    )
    assert failed["description"].endswith("failed to rebuild (code 9999).")


def test_a_fillet_that_lost_edges_is_repaired_in_place() -> None:
    """The Dipper's Fillet3: swFeatureErrorFilletNoEdge as a warning."""
    entry = rd.explain(rd.RawRebuildIssue("Fillet3", "Fillet", 13, True))

    assert "rounds only the edges it still finds" in entry["description"]
    assert "edit_feature with edges: 'selection'" in entry["hint"]


def test_the_warning_names_each_flagged_feature() -> None:
    """Warnings only by default; errors too when asked."""
    warned = rd.explain(rd.RawRebuildIssue("Fillet3", "Fillet", 13, True))
    failed = rd.explain(rd.RawRebuildIssue("Cut2", "Cut", 71, False))

    only = rd.issues_warning([warned, failed])
    both = rd.issues_warning([warned, failed], errors_too=True)

    assert only == (
        "SolidWorks reports: Fillet3 warns swFeatureErrorFilletNoEdge: "
        f"{warned['description']} {warned['hint']}"
    )
    assert both is not None and both.endswith(
        f"Cut2 fails {failed['code_name']}: {failed['description']} {failed['hint']}"
    )
    assert rd.issues_warning([failed]) is None
    assert rd.issues_warning([]) is None


def test_body_counts_need_both_reads_and_name_the_bodies_when_they_rise() -> None:
    assert rd.body_counts(None, ["A"]) == {}
    assert rd.body_counts(["A"], None) == {}
    assert rd.body_counts(["A", "B"], ["A"]) == {
        "solid_body_count_before": 2,
        "solid_body_count_after": 1,
    }
    assert rd.body_counts(["A"], ["A", "B"]) == {
        "solid_body_count_before": 1,
        "solid_body_count_after": 2,
        "solid_bodies_after": ["A", "B"],
    }


def test_a_body_count_that_rose_is_a_warning() -> None:
    risen = rd.body_counts(["Strip"], ["Strip", ""])

    warning = rd.body_count_warning(risen, "The sketch edit")

    assert warning is not None
    assert warning.startswith(
        "The sketch edit left the part 2 solid bodies where it had 1 "
        "(Strip, (unnamed)): a feature no longer meets the body"
    )
    assert "list_bodies" in warning
    assert rd.body_count_warning({**risen, "solid_bodies_after": []}).startswith(
        "The rebuild left the part 2 solid bodies where it had 1: "
    )
    assert rd.body_count_warning(rd.body_counts(["A"], ["A"])) is None
    assert rd.body_count_warning(rd.body_counts(["A", "B"], ["A"])) is None
    assert rd.body_count_warning({}) is None


def test_a_cut_that_meets_nothing_says_what_it_acts_on() -> None:
    scope = rd.BodyScope(True, False, ["Strip"], ["Strip", "Boss-Extrude5"])

    entry = rd.explain(rd.RawRebuildIssue("Cut7", "Cut", 71, False, scope=scope))

    assert entry["scope"] == {
        "feature_scope": True,
        "auto_select": False,
        "bodies": ["Strip"],
        "part_bodies": ["Strip", "Boss-Extrude5"],
    }
    assert entry["hint"].startswith("The cut acts only on Strip. The part has 2")
    assert entry["hint"].endswith(rd._CODE_HINTS[71][1])


def test_the_scope_hint_says_only_what_was_read() -> None:
    hint = rd.scope_hint

    assert hint(rd.BodyScope()) == ""
    assert hint(rd.BodyScope(feature_scope=False, part_bodies=["A"])) == (
        "The cut acts on every body. The part has 1 solid body: A (list_bodies)."
    )
    assert hint(rd.BodyScope(feature_scope=True, auto_select=True)) == (
        "The cut acts only on the bodies it selected itself, which can't be read."
    )
    assert hint(rd.BodyScope(feature_scope=True)).startswith(
        "The cut acts only on the bodies it selected, which"
    )
    assert hint(rd.BodyScope(part_bodies=[])) == (
        "The part has 0 solid bodies: none (list_bodies)."
    )
    # Nothing read: the issue's own hint stands alone.
    plain = rd.explain(
        rd.RawRebuildIssue("Cut7", "Cut", 71, False, scope=rd.BodyScope())
    )
    assert plain["hint"] == rd._CODE_HINTS[71][1]
