"""Contract tests: seed queries match the deterministic golden contract exactly.

Each fixture file is the canonical expected output — tests compare exact JSON.
"""

import json
from pathlib import Path

import pytest

from core.evidence_harness.schema import OutlierCasePackage
from core.evidence_harness.planner import QueryPlanner, date_window

_GOLDEN = Path(__file__).parent.parent.parent / "fixtures" / "evidence_harness" / "golden"


def _load(name: str):
    return json.loads((_GOLDEN / name).read_text())


def _plan(case: OutlierCasePackage) -> list[dict]:
    import dataclasses
    return [dataclasses.asdict(q) for q in QueryPlanner().plan(case)]


class TestGoldenQueryContract:
    def test_equity_low_return_exact_match(self):
        golden_case = _load("equity_low_return_case.json")
        golden_queries = _load("equity_low_return_queries.json")
        case = OutlierCasePackage(**golden_case)
        actual = _plan(case)
        assert actual == golden_queries

    def test_equity_high_return_exact_match(self):
        golden_case = _load("equity_high_return_case.json")
        golden_queries = _load("equity_high_return_queries.json")
        case = OutlierCasePackage(**golden_case)
        actual = _plan(case)
        assert actual == golden_queries

    def test_futures_wti_exact_match(self):
        golden_case = _load("futures_wti_case.json")
        golden_queries = _load("futures_wti_queries.json")
        case = OutlierCasePackage(**golden_case)
        actual = _plan(case)
        assert actual == golden_queries

    def test_options_vol_surface_exact_match(self):
        golden_case = _load("options_vol_surface_case.json")
        golden_queries = _load("options_vol_surface_queries.json")
        case = OutlierCasePackage(**golden_case)
        actual = _plan(case)
        assert actual == golden_queries


class TestQueryContractRules:
    def _equity_low(self) -> OutlierCasePackage:
        d = _load("equity_low_return_case.json")
        return OutlierCasePackage(**d)

    def _equity_high(self) -> OutlierCasePackage:
        d = _load("equity_high_return_case.json")
        return OutlierCasePackage(**d)

    def test_equity_low_first_query_is_neutral(self):
        queries = QueryPlanner().plan(self._equity_low())
        assert queries[0].text == "TSLA stock move 2024-01-25"

    def test_equity_low_contains_directional_shares_query(self):
        queries = QueryPlanner().plan(self._equity_low())
        texts = [q.text for q in queries]
        assert "TSLA shares fall 2024-01-25" in texts

    def test_equity_low_contains_earnings_guidance(self):
        queries = QueryPlanner().plan(self._equity_low())
        texts = [q.text for q in queries]
        assert "TSLA earnings guidance 2024-01-25" in texts

    def test_equity_low_contains_sec_filing(self):
        queries = QueryPlanner().plan(self._equity_low())
        texts = [q.text for q in queries]
        assert "TSLA SEC filing 2024-01-25" in texts

    def test_equity_low_contains_analyst_downgrade(self):
        queries = QueryPlanner().plan(self._equity_low())
        texts = [q.text for q in queries]
        assert "TSLA analyst downgrade 2024-01-25" in texts

    def test_equity_high_first_query_is_neutral(self):
        queries = QueryPlanner().plan(self._equity_high())
        assert queries[0].text == "AAPL stock move 2024-05-03"

    def test_equity_high_contains_analyst_upgrade(self):
        queries = QueryPlanner().plan(self._equity_high())
        texts = [q.text for q in queries]
        assert "AAPL analyst upgrade 2024-05-03" in texts

    def test_all_queries_use_iso_date_not_friendly(self):
        for name in ("equity_low_return_case.json", "equity_high_return_case.json"):
            case = OutlierCasePackage(**_load(name))
            queries = QueryPlanner().plan(case)
            for q in queries:
                # no month-name phrasing in seed queries
                for month in ("January","February","March","April","May","June",
                              "July","August","September","October","November","December"):
                    assert month not in q.text, \
                        f"Friendly date found in '{q.text}' — contract requires ISO dates"

    def test_query_ids_are_deterministic_across_calls(self):
        case = OutlierCasePackage(**_load("equity_low_return_case.json"))
        ids_a = [q.query_id for q in QueryPlanner().plan(case)]
        ids_b = [q.query_id for q in QueryPlanner().plan(case)]
        assert ids_a == ids_b

    def test_candidate_terms_never_displace_contract_seed_order(self):
        data = _load("equity_high_return_case.json")
        data["candidate_terms"] = ["AAPL stock rally", "AAPL stock move 2024-05-03"]
        case = OutlierCasePackage(**data)
        queries = QueryPlanner().plan(case)
        texts = [q.text for q in queries]

        assert texts[:5] == [
            "AAPL stock move 2024-05-03",
            "AAPL shares rise 2024-05-03",
            "AAPL earnings guidance 2024-05-03",
            "AAPL SEC filing 2024-05-03",
            "AAPL analyst upgrade 2024-05-03",
        ]
        assert "AAPL stock rally" in texts[5:]
        assert texts.count("AAPL stock move 2024-05-03") == 1

    def test_date_window_skips_weekends_for_daily_outlier(self):
        from core.evidence_harness.schema import OutlierCasePackage as OCP
        # 2024-01-29 is Monday → 2 prev business days = Thursday 2024-01-25
        case = OCP(case_id="c", run_id="r", signal_type="return_outlier",
                   as_of_date="2024-01-29")
        start, end = date_window(case)
        assert start == "2024-01-25"
        assert end == "2024-01-31"
