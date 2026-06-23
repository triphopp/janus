"""Contract tests: case ID payload follows the deterministic contract.

Same tagged outlier row + same run context always produces the same case_id,
and the ID is derived from the canonical payload fields (no timestamps/UUIDs).
"""

import json
from pathlib import Path

import pytest

from core.evidence_harness.ids import case_id
from core.evidence_harness.case_builder import build_case_package_from_tagged_return_outlier

_GOLDEN = Path(__file__).parent.parent.parent / "fixtures" / "evidence_harness" / "golden"


def _load_golden_case(name: str) -> dict:
    return json.loads((_GOLDEN / name).read_text())


class TestCaseIdPayloadContract:
    def test_symbol_used_as_key_when_present(self):
        cid = case_id("run_001", "return_outlier", "2024-01-25", "return_std",
                      family="equity", symbol="TSLA")
        golden = _load_golden_case("equity_low_return_case.json")
        assert cid == golden["case_id"]

    def test_instrument_used_when_no_symbol(self):
        cid_sym = case_id("run_001", "return_outlier", "2024-09-25", "return_std",
                          family="futures", symbol=None, instrument="WTI")
        cid_ins = case_id("run_001", "return_outlier", "2024-09-25", "return_std",
                          family="futures", instrument="WTI")
        golden = _load_golden_case("futures_wti_case.json")
        assert cid_sym == golden["case_id"]
        assert cid_ins == golden["case_id"]

    def test_identity_key_used_as_last_fallback(self):
        cid_a = case_id("run_001", "diff_finding", "2024-01-25", "diff_z",
                        family="futures", symbol=None, instrument=None, identity_key="CLZ4")
        cid_b = case_id("run_001", "diff_finding", "2024-01-25", "diff_z",
                        family="futures", identity_key="CLZ4")
        assert cid_a == cid_b
        assert cid_a.startswith("case_")

    def test_symbol_and_instrument_produce_different_ids(self):
        cid_sym = case_id("r", "return_outlier", "2024-01-01", "return_std",
                          family="equity", symbol="CL")
        cid_ins = case_id("r", "return_outlier", "2024-01-01", "return_std",
                          family="futures", instrument="CL")
        assert cid_sym != cid_ins

    def test_case_id_is_stable(self):
        args = ("run_001", "return_outlier", "2024-01-25", "return_std")
        kwargs = dict(family="equity", symbol="TSLA")
        assert case_id(*args, **kwargs) == case_id(*args, **kwargs)

    def test_case_id_hex_format(self):
        cid = case_id("r", "return_outlier", "2024-01-01", "return_std", symbol="X")
        assert cid.startswith("case_")
        hex_part = cid[len("case_"):]
        assert len(hex_part) == 16
        int(hex_part, 16)  # must be valid hex


class TestCaseBuilderUsesContractId:
    def test_case_builder_matches_direct_case_id(self):
        row = {"symbol": "TSLA", "as_of_date": "2024-01-25",
               "_return_outlier_direction": "low", "_return_outlier_severity": "high",
               "_return_outlier_zscore": -3.5}
        run_context = {"family": "equity"}
        pkg = build_case_package_from_tagged_return_outlier(
            run_id="run_001", row=row, run_context=run_context
        )
        expected = case_id("run_001", "return_outlier", "2024-01-25", "return_std",
                           family="equity", symbol="TSLA")
        assert pkg.case_id == expected

    def test_case_builder_symbol_priority_over_instrument(self):
        row = {"symbol": "TSLA", "instrument": "TSLA_EQ", "as_of_date": "2024-01-25"}
        pkg = build_case_package_from_tagged_return_outlier(
            run_id="run_001", row=row, run_context={"family": "equity"}
        )
        expected = case_id("run_001", "return_outlier", "2024-01-25", "return_std",
                           family="equity", symbol="TSLA")
        assert pkg.case_id == expected

    def test_case_builder_instrument_fallback_when_no_symbol(self):
        row = {"as_of_date": "2024-09-25"}
        pkg = build_case_package_from_tagged_return_outlier(
            run_id="run_001", row=row,
            run_context={"family": "futures", "instrument": "WTI"}
        )
        expected = case_id("run_001", "return_outlier", "2024-09-25", "return_std",
                           family="futures", instrument="WTI")
        assert pkg.case_id == expected

    def test_case_builder_identity_key_fallback_when_no_symbol_or_instrument(self):
        row = {"as_of_date": "2024-09-25", "identity_key": "CLZ4"}
        pkg = build_case_package_from_tagged_return_outlier(
            run_id="run_001", row=row, run_context={"family": "futures"}
        )
        expected = case_id("run_001", "return_outlier", "2024-09-25", "return_std",
                           family="futures", identity_key="CLZ4")
        assert pkg.case_id == expected
        assert pkg.local_context["identity_key"] == "CLZ4"

    def test_case_id_is_deterministic_across_calls(self):
        row = {"symbol": "AAPL", "as_of_date": "2024-05-03",
               "_return_outlier_direction": "high"}
        run_context = {"family": "equity"}
        pkg1 = build_case_package_from_tagged_return_outlier(
            run_id="run_001", row=row, run_context=run_context
        )
        pkg2 = build_case_package_from_tagged_return_outlier(
            run_id="run_001", row=row, run_context=run_context
        )
        assert pkg1.case_id == pkg2.case_id
