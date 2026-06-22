"""Tests for QueryPlanner — golden seed query contract."""

import pytest
from core.evidence_harness.schema import OutlierCasePackage
from core.evidence_harness.planner import QueryPlanner, date_window


def _case(**kwargs) -> OutlierCasePackage:
    defaults = dict(case_id="c", run_id="r", signal_type="return_outlier", as_of_date="2024-01-25")
    defaults.update(kwargs)
    return OutlierCasePackage(**defaults)


class TestGoldenSeedQueries:
    def test_equity_low_return_seed_queries_match_golden(self):
        case = _case(
            family="equity", symbol="TSLA", as_of_date="2024-01-25",
            signal_type="return_outlier",
            local_context={"direction": "low"},
        )
        queries = QueryPlanner().plan(case)
        texts = [q.text for q in queries]
        assert texts[0] == "TSLA stock fall January 2024"
        assert "TSLA earnings January 2024" in texts
        assert "TSLA SEC filing 2024-01-25" in texts
        assert "TSLA analyst downgrade January 2024" in texts

    def test_equity_high_return_seed_queries_match_golden(self):
        case = _case(
            family="equity", symbol="AAPL", as_of_date="2024-05-03",
            signal_type="return_outlier",
            local_context={"direction": "high"},
        )
        queries = QueryPlanner().plan(case)
        texts = [q.text for q in queries]
        assert texts[0] == "AAPL stock rise May 2024"
        assert "AAPL earnings May 2024" in texts
        assert "AAPL SEC filing 2024-05-03" in texts
        assert "AAPL analyst upgrade May 2024" in texts

    def test_futures_seed_queries_match_golden(self):
        case = _case(
            family="futures", instrument="WTI", symbol="WTI", as_of_date="2024-09-25",
            signal_type="return_outlier",
            local_context={"direction": "high"},
            source_hints=["EIA", "OPEC", "CME"],
        )
        queries = QueryPlanner().plan(case)
        texts = [q.text for q in queries]
        assert texts[0] == "WTI price move 2024-09-25"
        assert "WTI futures settlement 2024-09-25" in texts
        assert "WTI inventory report 2024-09-25" in texts
        assert "EIA crude oil inventory 2024-09-25" in texts
        assert "OPEC oil market 2024-09-25" in texts

    def test_options_vol_surface_seed_queries_match_golden(self):
        case = _case(
            family="equity_options", symbol="TSLA", as_of_date="2024-01-25",
            signal_type="vol_surface_cluster", metric_name="iv_surface_z",
        )
        queries = QueryPlanner().plan(case)
        texts = [q.text for q in queries]
        assert "TSLA implied volatility spike 2024-01-25" in texts
        assert "TSLA options volume 2024-01-25" in texts
        assert "TSLA volatility skew 2024-01-25" in texts
        assert "TSLA earnings volatility 2024-01-25" in texts


class TestPlannerProperties:
    def test_queries_are_deterministic(self):
        case = _case(family="equity", symbol="TSLA", local_context={"direction": "low"})
        a = QueryPlanner().plan(case)
        b = QueryPlanner().plan(case)
        assert [q.text for q in a] == [q.text for q in b]

    def test_query_ids_are_deterministic(self):
        case = _case(family="equity", symbol="TSLA", local_context={"direction": "low"})
        a = QueryPlanner().plan(case)
        b = QueryPlanner().plan(case)
        assert [q.query_id for q in a] == [q.query_id for q in b]

    def test_no_duplicate_queries(self):
        case = _case(family="futures", instrument="WTI", symbol="WTI",
                     source_hints=["EIA", "OPEC"],
                     local_context={"direction": "high"})
        queries = QueryPlanner().plan(case)
        texts = [q.text for q in queries]
        assert len(texts) == len(set(texts))

    def test_respects_max_queries(self):
        case = _case(family="equity", symbol="TSLA", local_context={"direction": "low"})
        queries = QueryPlanner(max_queries=2).plan(case)
        assert len(queries) <= 2

    def test_all_queries_have_date_window(self):
        case = _case(family="equity", symbol="TSLA", as_of_date="2024-01-25",
                     local_context={"direction": "low"})
        queries = QueryPlanner().plan(case)
        for q in queries:
            assert q.date_start is not None
            assert q.date_end is not None

    def test_diff_finding_generates_correction_queries(self):
        case = _case(signal_type="diff_finding", instrument="WTI")
        queries = QueryPlanner().plan(case)
        texts = [q.text for q in queries]
        assert any("correction" in t.lower() or "data" in t.lower() for t in texts)

    def test_llm_expansion_rejected_when_budget_zero(self):
        planner = QueryPlanner()
        reason = planner.validate_llm_expansion(
            "TSLA news", None, None, "2024-01-23", "2024-01-27", set(), [], 0
        )
        assert reason == "query_budget_exhausted"

    def test_llm_expansion_rejected_for_duplicate(self):
        planner = QueryPlanner()
        seen = {"tsla stock move 2024-01-25"}
        reason = planner.validate_llm_expansion(
            "TSLA stock move 2024-01-25", None, None,
            "2024-01-23", "2024-01-27", seen, [], 5
        )
        assert reason == "duplicate_query"

    def test_llm_expansion_rejected_outside_date_window(self):
        planner = QueryPlanner()
        reason = planner.validate_llm_expansion(
            "TSLA Q3 2023", "2023-01-01", None,
            "2024-01-23", "2024-01-27", set(), [], 5
        )
        assert reason == "date_window_violation"

    def test_llm_expansion_accepted_for_valid_query(self):
        planner = QueryPlanner()
        reason = planner.validate_llm_expansion(
            "TSLA earnings beat 2024-01-25", "2024-01-24", "2024-01-26",
            "2024-01-23", "2024-01-27", set(), [], 5
        )
        assert reason is None


class TestDateWindow:
    def test_daily_outlier_skips_weekend_for_start(self):
        case = _case(as_of_date="2024-01-29", signal_type="return_outlier")  # Monday
        start, end = date_window(case)
        assert start == "2024-01-25"  # Thursday (skip Sat+Sun)
        assert end == "2024-01-31"

    def test_vol_surface_uses_same_window_as_return_outlier(self):
        case_ret = _case(as_of_date="2024-01-29", signal_type="return_outlier")
        case_vol = _case(as_of_date="2024-01-29", signal_type="vol_surface_cluster")
        assert date_window(case_ret) == date_window(case_vol)

    def test_diff_finding_uses_7_day_window(self):
        case = _case(as_of_date="2024-01-29", signal_type="diff_finding")
        start, end = date_window(case)
        assert end == "2024-01-29"
        assert start == "2024-01-22"
