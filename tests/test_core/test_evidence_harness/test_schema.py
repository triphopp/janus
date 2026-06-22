"""Tests for evidence harness schema contracts."""

import json
import math
import pytest

from core.evidence_harness.schema import (
    OutlierCasePackage,
    SearchQuery,
    SearchResult,
    FetchResult,
    ExtractedDocument,
    EvidenceClaim,
    HarnessRunResult,
    SourceRegistryRecord,
    to_json_safe,
)


def _minimal_case() -> OutlierCasePackage:
    return OutlierCasePackage(
        case_id="case_abc",
        run_id="run_001",
        signal_type="return_outlier",
        as_of_date="2024-01-25",
    )


class TestOutlierCasePackage:
    def test_minimal_valid(self):
        pkg = _minimal_case()
        pkg.validate()

    def test_missing_case_id_raises(self):
        pkg = OutlierCasePackage(case_id="", run_id="r", signal_type="s", as_of_date="2024-01-01")
        with pytest.raises(ValueError, match="case_id"):
            pkg.validate()

    def test_missing_run_id_raises(self):
        pkg = OutlierCasePackage(case_id="c", run_id="", signal_type="s", as_of_date="2024-01-01")
        with pytest.raises(ValueError, match="run_id"):
            pkg.validate()

    def test_from_dict_ignores_unknown_keys(self):
        d = {
            "case_id": "c", "run_id": "r", "signal_type": "s",
            "as_of_date": "2024-01-01", "unknown_future_key": "x",
        }
        pkg = OutlierCasePackage.from_dict(d)
        assert pkg.case_id == "c"
        assert not hasattr(pkg, "unknown_future_key")

    def test_optional_fields_default_to_none(self):
        pkg = _minimal_case()
        assert pkg.symbol is None
        assert pkg.z_score is None
        assert pkg.family is None

    def test_default_lists_are_empty(self):
        pkg = _minimal_case()
        assert pkg.candidate_terms == []
        assert pkg.source_hints == []
        assert pkg.protected_columns == []


class TestToJsonSafe:
    def test_nan_becomes_none(self):
        pkg = OutlierCasePackage(
            case_id="c", run_id="r", signal_type="s", as_of_date="2024-01-01",
            z_score=float("nan"),
        )
        safe = to_json_safe(pkg)
        assert safe["z_score"] is None

    def test_inf_becomes_none(self):
        pkg = OutlierCasePackage(
            case_id="c", run_id="r", signal_type="s", as_of_date="2024-01-01",
            observed_value=float("inf"),
        )
        safe = to_json_safe(pkg)
        assert safe["observed_value"] is None

    def test_normal_float_preserved(self):
        pkg = OutlierCasePackage(
            case_id="c", run_id="r", signal_type="s", as_of_date="2024-01-01",
            z_score=-8.5,
        )
        safe = to_json_safe(pkg)
        assert safe["z_score"] == -8.5

    def test_result_is_json_serializable(self):
        pkg = OutlierCasePackage(
            case_id="c", run_id="r", signal_type="s", as_of_date="2024-01-01",
            z_score=float("nan"), observed_value=float("inf"),
        )
        safe = to_json_safe(pkg)
        json.dumps(safe)  # must not raise


class TestSearchQuery:
    def test_defaults(self):
        q = SearchQuery(query_id="q1", case_id="c1", text="TSLA stock move 2024-01-25")
        assert q.domains == []
        assert q.priority == 0
        assert q.date_start is None


class TestFetchResult:
    def test_blocked_result_has_reason(self):
        fr = FetchResult(
            fetch_id="f1", url="https://example.com", final_url="https://example.com",
            status_code=200, fetched_at="2024-01-01T00:00:00Z",
            bytes_read=0, content_hash="", blocked_reason="private_ip",
        )
        assert fr.blocked_reason == "private_ip"


class TestHarnessRunResult:
    def test_empty_lists_by_default(self):
        r = HarnessRunResult(
            case_id="c", run_id="r", harness_run_id="h",
            status="insufficient_evidence", verdict="insufficient_evidence", confidence="low",
        )
        assert r.queries == []
        assert r.fetched == []
        assert r.checks == []
