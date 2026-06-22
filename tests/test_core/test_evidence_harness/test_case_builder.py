"""Tests for CaseBuilder — stable case IDs from tagged outlier rows."""

import math
from core.evidence_harness.case_builder import build_case_package_from_tagged_return_outlier


def _run_context(family="equity", instrument=None):
    return {"family": family, "instrument": instrument}


def _row(**kwargs):
    defaults = {
        "symbol": "TSLA",
        "as_of_date": "2024-01-25",
        "_return_outlier_direction": "low",
        "_return_outlier_severity": "severe",
        "_return_outlier_zscore": -8.5,
        "_return_prior_median": 0.001,
        "return_std": -0.121,
        "return_raw": -0.121,
    }
    defaults.update(kwargs)
    return defaults


class TestCaseBuilderFromTaggedReturnOutlier:
    def test_case_builder_from_tagged_return_outlier_is_stable(self):
        row = _row()
        ctx = _run_context()
        pkg1 = build_case_package_from_tagged_return_outlier(
            run_id="fixture_run", row=row, run_context=ctx
        )
        pkg2 = build_case_package_from_tagged_return_outlier(
            run_id="fixture_run", row=row, run_context=ctx
        )
        assert pkg1.case_id == pkg2.case_id

    def test_maps_required_fields(self):
        pkg = build_case_package_from_tagged_return_outlier(
            run_id="fixture_run", row=_row(), run_context=_run_context()
        )
        assert pkg.run_id == "fixture_run"
        assert pkg.symbol == "TSLA"
        assert pkg.as_of_date == "2024-01-25"
        assert pkg.signal_type == "return_outlier"
        assert pkg.metric_name == "return_std"

    def test_observed_value_maps_from_return_std(self):
        pkg = build_case_package_from_tagged_return_outlier(
            run_id="r", row=_row(return_std=-0.121), run_context=_run_context()
        )
        assert pkg.observed_value == pytest.approx(-0.121)

    def test_z_score_mapped(self):
        pkg = build_case_package_from_tagged_return_outlier(
            run_id="r", row=_row(_return_outlier_zscore=-8.5), run_context=_run_context()
        )
        assert pkg.z_score == pytest.approx(-8.5)

    def test_nan_values_sanitized_to_none(self):
        pkg = build_case_package_from_tagged_return_outlier(
            run_id="r", row=_row(return_std=float("nan"), _return_outlier_zscore=float("inf")),
            run_context=_run_context()
        )
        assert pkg.observed_value is None
        assert pkg.z_score is None

    def test_candidate_terms_populated_for_low_direction(self):
        pkg = build_case_package_from_tagged_return_outlier(
            run_id="r", row=_row(_return_outlier_direction="low"),
            run_context=_run_context()
        )
        assert "fall" in pkg.candidate_terms or "drop" in pkg.candidate_terms

    def test_candidate_terms_populated_for_high_direction(self):
        pkg = build_case_package_from_tagged_return_outlier(
            run_id="r", row=_row(_return_outlier_direction="high"),
            run_context=_run_context()
        )
        assert "rise" in pkg.candidate_terms or "jump" in pkg.candidate_terms

    def test_futures_source_hints_include_eia(self):
        pkg = build_case_package_from_tagged_return_outlier(
            run_id="r", row=_row(), run_context=_run_context(family="futures")
        )
        assert "EIA" in pkg.source_hints

    def test_equity_source_hints_include_sec(self):
        pkg = build_case_package_from_tagged_return_outlier(
            run_id="r", row=_row(), run_context=_run_context(family="equity")
        )
        assert "SEC" in pkg.source_hints

    def test_different_symbol_different_case_id(self):
        pkg_a = build_case_package_from_tagged_return_outlier(
            run_id="r", row=_row(symbol="TSLA"), run_context=_run_context()
        )
        pkg_b = build_case_package_from_tagged_return_outlier(
            run_id="r", row=_row(symbol="AAPL"), run_context=_run_context()
        )
        assert pkg_a.case_id != pkg_b.case_id

    def test_local_context_has_no_secrets(self):
        pkg = build_case_package_from_tagged_return_outlier(
            run_id="r", row=_row(), run_context=_run_context()
        )
        ctx_str = str(pkg.local_context)
        forbidden = ["password", "secret", "api_key", "token", "/Users/", "DSN"]
        for word in forbidden:
            assert word not in ctx_str


import pytest
