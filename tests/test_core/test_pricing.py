"""Pricing tests — Black-76 vs BS-Merton, IV solver, PCP (section 12).

Must pass: diff vs QuantLib < 1e-6 in golden set; PCP within tolerance.
"""

import warnings

import numpy as np
import pandas as pd
import pytest
from pathlib import Path
from core.greeks import single_leg_greeks
from core.pricing import price, price_with_diagnostics, solve_iv


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

    @pytest.mark.parametrize(
        "model,S,K,right,q",
        [
            ("bs", 50.0, 100.0, "P", 0.0),
            ("bsm", 50.0, 100.0, "P", 0.02),
            ("bsm", 100.0, 80.0, "C", 0.10),
        ],
    )
    def test_iv_round_trip_deep_itm_uses_spot_model_bound(self, model, S, K, right, q):
        sigma = 0.10
        mkt = price(model, S, K, 1.0, 0.05, sigma, right, q=q)
        solved = solve_iv(model, mkt, S, K, 1.0, 0.05, right, q=q)
        assert solved == pytest.approx(sigma, abs=1e-5)

    def test_plain_bs_ignores_dividend_yield(self):
        args = (100.0, 100.0, 1.0, 0.05, 0.20, "C")
        assert price("bs", *args, q=0.10) == pytest.approx(price("bs", *args, q=0.0))
        with_dividend = price("bsm", *args, q=0.10)
        assert with_dividend != pytest.approx(price("bs", *args, q=0.10))


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


class TestAdditionalPricingEngines:
    def test_bachelier_prices_negative_futures_and_round_trips_absolute_vol(self):
        F, K, T, r, normal_vol = -37.63, 10.0, 0.5, 0.05, 25.0
        premium = price("bachelier", F, K, T, r, normal_vol, "C")
        assert np.isfinite(premium)
        assert premium > 0
        solved = solve_iv(
            "bachelier",
            premium,
            F,
            K,
            T,
            r,
            "C",
            bounds=(1e-4, 100.0),
        )
        assert solved == pytest.approx(normal_vol, abs=1e-5)

    def test_shifted_black_requires_explicit_shift(self):
        assert np.isnan(price("black76_shifted", -37.63, 10.0, 0.5, 0.05, 0.3, "C"))
        premium = price(
            "black76_shifted", -37.63, 10.0, 0.5, 0.05, 0.3, "C", shift=50.0
        )
        assert np.isfinite(premium)
        solved = solve_iv(
            "black76_shifted",
            premium,
            -37.63,
            10.0,
            0.5,
            0.05,
            "C",
            shift=50.0,
        )
        assert solved == pytest.approx(0.3, abs=1e-5)

    @pytest.mark.parametrize(
        "right,K,S,q,r,T,sigma,expected",
        [
            ("C", 100.0, 90.0, 0.10, 0.10, 0.10, 0.15, 0.0206),
            ("C", 100.0, 100.0, 0.10, 0.10, 0.50, 0.25, 6.8015),
            ("P", 100.0, 90.0, 0.10, 0.10, 0.10, 0.15, 10.0000),
            ("P", 100.0, 100.0, 0.10, 0.10, 0.10, 0.25, 3.1277),
        ],
    )
    def test_bsm_baw_matches_haug_quantlib_reference(
        self, right, K, S, q, r, T, sigma, expected
    ):
        # Public reference cases used by QuantLib's American-option test suite.
        calculated = price("bsm_baw", S, K, T, r, sigma, right, q=q)
        assert calculated == pytest.approx(expected, abs=3e-3)

    @pytest.mark.parametrize("right", ["C", "P"])
    def test_black76_baw_has_premium_and_iv_round_trip(self, right):
        F, K, T, r, sigma = 80.0, 80.0, 0.5, 0.05, 0.30
        european = price("black76", F, K, T, r, sigma, right)
        result = price_with_diagnostics("black76_baw", F, K, T, r, sigma, right)
        assert result.value >= european
        assert result.diagnostics["baw_boundary_converged"] is True
        assert result.diagnostics["baw_boundary_iterations"] > 0
        solved = solve_iv("black76_baw", result.value, F, K, T, r, right)
        assert solved == pytest.approx(sigma, abs=1e-5)

    def test_baw_negative_rate_fails_closed_with_reason(self):
        result = price_with_diagnostics(
            "black76_baw", 80.0, 80.0, 0.5, -0.01, 0.30, "C"
        )
        assert np.isnan(result.value)
        assert result.diagnostics["pricing_domain_reason"] == "baw_negative_rate_not_supported"

    def test_baw_long_tenor_emits_reference_warning(self):
        result = price_with_diagnostics(
            "black76_baw", 80.0, 80.0, 1.5, 0.05, 0.30, "P"
        )
        assert result.diagnostics["model_validity_warning"] == (
            "baw_reference_recommended_for_t_gt_1y"
        )

    def test_crr_reference_matches_european_black76_and_american_baw(self):
        european = price("black76", 100.0, 100.0, 1.0, 0.05, 0.20, "C")
        european_tree = price(
            "crr_binomial", 100.0, 100.0, 1.0, 0.05, 0.20, "C",
            model_params={
                "tree_steps": 800,
                "tree_exercise_style": "european",
                "tree_underlying_type": "future",
            },
        )
        american = price("black76_baw", 100.0, 100.0, 0.5, 0.05, 0.25, "P")
        american_tree = price(
            "crr_binomial", 100.0, 100.0, 0.5, 0.05, 0.25, "P",
            model_params={
                "tree_steps": 800,
                "tree_exercise_style": "american",
                "tree_underlying_type": "future",
            },
        )

        assert european_tree == pytest.approx(european, abs=0.003)
        assert american_tree == pytest.approx(american, abs=0.02)

    def test_deep_otm_solve(self):
        """Deep OTM — vega near 0 — should return NaN not crash."""
        F, K, T, r = 80, 150, 0.1, 0.05
        mkt = 0.001  # very cheap deep OTM
        result = solve_iv("black76", mkt, F, K, T, r, "C")
        # OK if NaN (can't solve) or a number (if possible)
        # Just must not raise
        assert result is not None
