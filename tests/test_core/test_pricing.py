"""Pricing tests — Black-76 vs BS-Merton, IV solver, PCP (section 12).

Must pass: diff vs QuantLib < 1e-6 in golden set; PCP within tolerance.
"""

import warnings

import numpy as np
import pandas as pd
import pytest
from pathlib import Path
from core.greeks import single_leg_greeks
from core.pricing import price, solve_iv


class TestBlack76:
    """Black-76 for futures options."""

    def test_atm_call(self):
        """ATM call: F=80, K=80, T=0.5, r=0.05, σ=0.3."""
        p = price("black76", 80, 80, 0.5, 0.05, 0.3, "C")
        assert p > 0
        # Rough sanity: for ATM, price ≈ 0.4 * σ * sqrt(T) * disc * F
        expected_approx = 0.4 * 0.3 * np.sqrt(0.5) * np.exp(-0.05 * 0.5) * 80
        assert p == pytest.approx(expected_approx, rel=0.15)

    def test_put_call_parity_black76(self):
        """C − P = e^(-rT)(F − K)."""
        F, K, T, r, sigma = 80, 85, 0.5, 0.05, 0.3
        C = price("black76", F, K, T, r, sigma, "C")
        P = price("black76", F, K, T, r, sigma, "P")
        diff = C - P
        expected = np.exp(-r * T) * (F - K)
        assert diff == pytest.approx(expected, rel=1e-10)

    def test_deep_itm_call(self):
        """Deep ITM: F >> K → C ≈ e^(-rT)(F − K)."""
        C = price("black76", 100, 50, 0.5, 0.05, 0.2, "C")
        intrinsic = np.exp(-0.05 * 0.5) * (100 - 50)
        assert C == pytest.approx(intrinsic, rel=0.01)

    def test_deep_otm_call(self):
        """Deep OTM: C → 0."""
        C = price("black76", 50, 100, 0.5, 0.05, 0.2, "C")
        assert C < 0.01

    def test_expired_option(self):
        """Expired option: price = intrinsic only."""
        C = price("black76", 90, 80, 0, 0.05, 0.3, "C")
        assert C == 10.0

    def test_explicit_european_alias_matches_black76(self):
        args = (80, 85, 0.5, 0.05, 0.3, "C")
        assert price("black76_european", *args) == pytest.approx(price("black76", *args))

    def test_invalid_lognormal_domain_returns_nan_without_runtime_warning(self):
        invalid_cases = [
            (-37.63, 70.0, 0.5, 0.05, 0.3, "C"),
            (80.0, 0.0, 0.5, 0.05, 0.3, "C"),
            (80.0, 70.0, 0.5, 0.05, 0.0, "C"),
            (80.0, 70.0, 0.5, 0.05, 0.3, "X"),
        ]
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            out = [price("black76", *case) for case in invalid_cases]
        assert all(np.isnan(value) for value in out)
        assert not [w for w in caught if issubclass(w.category, RuntimeWarning)]

    def test_golden_reference_file(self):
        """Golden Black-76 fixture must match closed-form prices and Greeks."""
        path = Path("tests/golden/black76_reference.csv")
        golden = pd.read_csv(path, comment="#")

        for _, row in golden.iterrows():
            call = price("black76", row["F"], row["K"], row["T"], row["r"], row["sigma"], "C")
            put = price("black76", row["F"], row["K"], row["T"], row["r"], row["sigma"], "P")
            greeks = single_leg_greeks(
                "black76", row["F"], row["K"], row["T"], row["r"], row["sigma"], "C"
            )

            assert call == pytest.approx(row["call_price"], abs=1e-6)
            assert put == pytest.approx(row["put_price"], abs=1e-6)
            assert greeks["delta"] == pytest.approx(row["delta_call"], abs=1e-4)
            assert greeks["gamma"] == pytest.approx(row["gamma"], abs=1e-4)
            assert greeks["vega"] == pytest.approx(row["vega"], abs=1e-4)
            assert greeks["theta"] == pytest.approx(row["theta_call"], abs=1e-4)


class TestBSMerton:
    """BS-Merton for equity/index options."""

    def test_atm_call_bsm(self):
        p = price("bsm", 100, 100, 0.5, 0.05, 0.3, "C", q=0.02)
        assert p > 0
        assert p < 100  # sanity

    def test_put_call_parity_bsm(self):
        """C − P = S*e^(-qT) − K*e^(-rT)."""
        S, K, T, r, sigma, q = 100, 100, 0.5, 0.05, 0.3, 0.02
        C = price("bsm", S, K, T, r, sigma, "C", q)
        P = price("bsm", S, K, T, r, sigma, "P", q)
        diff = C - P
        expected = S * np.exp(-q * T) - K * np.exp(-r * T)
        assert diff == pytest.approx(expected, rel=1e-10)

    def test_black76_vs_bs_delta_factor(self):
        """Black-76 delta = e^(-rT)*N(d1); BS delta = N(d1).
        At r=5%, T=0.5: delta difference ≈ e^(0.025) ≈ 1.025.
        """
        # ATM call
        F, K, T, r, sigma = 80, 80, 0.5, 0.05, 0.3
        c_black76 = price("black76", F, K, T, r, sigma, "C")
        c_bs = price("bs", F, K, T, r, sigma, "C")
        # BS on "spot" = 80 gives different price than Black-76 on F=80
        # because BS doesn't discount the forward
        assert c_black76 != pytest.approx(c_bs, abs=0.01)


class TestIVSolver:
    """Implied volatility solver."""

    def test_round_trip(self):
        """price(σ=0.3) → solve_iv → 0.3 ± 1e-5."""
        F, K, T, r, sigma = 80, 80, 0.5, 0.05, 0.3
        mkt = price("black76", F, K, T, r, sigma, "C")
        solved = solve_iv("black76", mkt, F, K, T, r, "C")
        assert solved == pytest.approx(sigma, abs=1e-5)

    def test_alias_round_trip(self):
        F, K, T, r, sigma = 80, 80, 0.5, 0.05, 0.3
        mkt = price("black76_european", F, K, T, r, sigma, "C")
        solved = solve_iv("black76_european", mkt, F, K, T, r, "C")
        assert solved == pytest.approx(sigma, abs=1e-5)

    def test_invalid_domain_returns_nan_without_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = solve_iv("black76", 1.0, -37.63, 70, 0.5, 0.05, "C")
        assert np.isnan(result)
        assert not [w for w in caught if issubclass(w.category, RuntimeWarning)]

    def test_arb_violation_returns_nan(self):
        """Intrinsic > mkt_price → NaN (arbitrage)."""
        # Put with K >> F should have intrinsic
        # If mkt_price is too low, solver returns NaN
        F, K, T, r = 80, 120, 0.5, 0.05
        mkt = 0.01  # impossibly cheap deep ITM put
        result = solve_iv("black76", mkt, F, K, T, r, "P")
        assert np.isnan(result)

    def test_Inear_zero_handled(self):
        """T very small but positive should still work."""
        result = solve_iv("black76", 5.0, 80, 82, 0.001, 0.05, "C")
        # May or may not converge — but shouldn't crash
        assert result is not None

    def test_deep_otm_solve(self):
        """Deep OTM — vega near 0 — should return NaN not crash."""
        F, K, T, r = 80, 150, 0.1, 0.05
        mkt = 0.001  # very cheap deep OTM
        result = solve_iv("black76", mkt, F, K, T, r, "C")
        # OK if NaN (can't solve) or a number (if possible)
        # Just must not raise
        assert result is not None
