"""Phase 6 — Independent external reference validation for Black-76 and BSM Greeks.

Reference values come from tools/generate_greek_reference.py which uses scipy.stats.norm
with NO shared code from core/greeks.py. This breaks circularity between the analytic
formula and the tests that validate it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.greeks import single_leg_greeks, batch_greeks

_REF_PATH = Path(__file__).parent.parent / "fixtures" / "greek_reference.json"

# Tolerances from the reference file
_PRICE_TOL = 1e-6
_GREEK_TOL = 1e-4


@pytest.fixture(scope="module")
def reference():
    if not _REF_PATH.exists():
        pytest.skip("greek_reference.json not found — run tools/generate_greek_reference.py first")
    with open(_REF_PATH) as f:
        return json.load(f)


class TestReferenceMetadata:
    def test_metadata_present(self, reference):
        assert "metadata" in reference
        assert "source" in reference["metadata"]
        assert "tolerances" in reference["metadata"]
        assert "notes" in reference["metadata"]

    def test_both_models_present(self, reference):
        assert "black76" in reference
        assert "bsm" in reference

    def test_reference_has_expected_rows(self, reference):
        assert len(reference["black76"]) >= 9
        assert len(reference["bsm"]) >= 9


class TestBlack76VsReference:
    def test_price_matches_reference(self, reference):
        for row in reference["black76"]:
            g = single_leg_greeks("black76", row["F"], row["K"], row["T"], row["r"], row["sigma"], row["right"])
            from core.pricing import price as price_fn
            p = price_fn("black76", row["F"], row["K"], row["T"], row["r"], row["sigma"], row["right"])
            assert p == pytest.approx(row["price"], abs=_PRICE_TOL), (
                f"Black-76 price mismatch [{row['moneyness']} {row['right']}]: "
                f"got {p:.8f}, expected {row['price']:.8f}"
            )

    def test_delta_matches_reference(self, reference):
        for row in reference["black76"]:
            g = single_leg_greeks("black76", row["F"], row["K"], row["T"], row["r"], row["sigma"], row["right"])
            assert g["delta"] == pytest.approx(row["delta"], abs=_GREEK_TOL), (
                f"Black-76 delta mismatch [{row['moneyness']} {row['right']}]: "
                f"got {g['delta']:.6f}, expected {row['delta']:.6f}"
            )

    def test_gamma_matches_reference(self, reference):
        for row in reference["black76"]:
            g = single_leg_greeks("black76", row["F"], row["K"], row["T"], row["r"], row["sigma"], row["right"])
            assert g["gamma"] == pytest.approx(row["gamma"], abs=_GREEK_TOL)

    def test_vega_matches_reference(self, reference):
        for row in reference["black76"]:
            g = single_leg_greeks("black76", row["F"], row["K"], row["T"], row["r"], row["sigma"], row["right"])
            assert g["vega"] == pytest.approx(row["vega"], abs=_GREEK_TOL)

    def test_theta_matches_reference(self, reference):
        for row in reference["black76"]:
            g = single_leg_greeks("black76", row["F"], row["K"], row["T"], row["r"], row["sigma"], row["right"])
            assert g["theta"] == pytest.approx(row["theta"], abs=_GREEK_TOL), (
                f"Black-76 theta mismatch [{row['moneyness']} {row['right']}]: "
                f"got {g['theta']:.6f}, expected {row['theta']:.6f}"
            )

    def test_rho_matches_reference(self, reference):
        for row in reference["black76"]:
            g = single_leg_greeks("black76", row["F"], row["K"], row["T"], row["r"], row["sigma"], row["right"])
            assert g["rho"] == pytest.approx(row["rho"], abs=_GREEK_TOL)

    def test_batch_matches_reference(self, reference):
        """batch_greeks numpy path must also match the external reference."""
        rows = reference["black76"]
        import numpy as np
        g = batch_greeks(
            model="black76",
            S_or_F=[r["F"] for r in rows],
            K=[r["K"] for r in rows],
            T=[r["T"] for r in rows],
            r=[r["r"] for r in rows],
            sigma=[r["sigma"] for r in rows],
            right=[r["right"] for r in rows],
            backend="numpy",
        )
        for i, row in enumerate(rows):
            for greek in ("delta", "gamma", "vega", "theta", "rho"):
                assert g[greek][i] == pytest.approx(row[greek], abs=_GREEK_TOL), (
                    f"batch_greeks {greek} mismatch [{row['moneyness']} {row['right']}]"
                )


class TestBSMVsReference:
    def test_delta_matches_reference(self, reference):
        for row in reference["bsm"]:
            g = single_leg_greeks("bsm", row["S"], row["K"], row["T"], row["r"], row["sigma"], row["right"])
            assert g["delta"] == pytest.approx(row["delta"], abs=_GREEK_TOL)

    def test_gamma_matches_reference(self, reference):
        for row in reference["bsm"]:
            g = single_leg_greeks("bsm", row["S"], row["K"], row["T"], row["r"], row["sigma"], row["right"])
            assert g["gamma"] == pytest.approx(row["gamma"], abs=_GREEK_TOL)

    def test_vega_matches_reference(self, reference):
        for row in reference["bsm"]:
            g = single_leg_greeks("bsm", row["S"], row["K"], row["T"], row["r"], row["sigma"], row["right"])
            assert g["vega"] == pytest.approx(row["vega"], abs=_GREEK_TOL)

    def test_theta_matches_reference(self, reference):
        for row in reference["bsm"]:
            g = single_leg_greeks("bsm", row["S"], row["K"], row["T"], row["r"], row["sigma"], row["right"])
            assert g["theta"] == pytest.approx(row["theta"], abs=_GREEK_TOL)

    def test_rho_matches_reference(self, reference):
        for row in reference["bsm"]:
            g = single_leg_greeks("bsm", row["S"], row["K"], row["T"], row["r"], row["sigma"], row["right"])
            assert g["rho"] == pytest.approx(row["rho"], abs=_GREEK_TOL)

    def test_batch_matches_reference(self, reference):
        rows = reference["bsm"]
        import numpy as np
        g = batch_greeks(
            model="bsm",
            S_or_F=[r["S"] for r in rows],
            K=[r["K"] for r in rows],
            T=[r["T"] for r in rows],
            r=[r["r"] for r in rows],
            sigma=[r["sigma"] for r in rows],
            right=[r["right"] for r in rows],
            backend="numpy",
        )
        for i, row in enumerate(rows):
            for greek in ("delta", "gamma", "vega", "theta", "rho"):
                assert g[greek][i] == pytest.approx(row[greek], abs=_GREEK_TOL)
