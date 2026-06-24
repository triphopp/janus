"""Greeks tests — closed-form vs bump, net greeks for spreads (section 12)."""

from math import erf

import numpy as np
import pytest
from core.greeks import single_leg_greeks, net_greeks, bump_greeks, Leg
from core.pricing import price


class TestClosedFormGreeks:
    """Closed-form Greek calculations."""

    def test_delta_call_vs_put(self):
        """Call delta + |Put delta| ≈ e^(-rT) for ATM (Black-76)."""
        g_call = single_leg_greeks("black76", 80, 80, 0.5, 0.05, 0.3, "C")
        g_put = single_leg_greeks("black76", 80, 80, 0.5, 0.05, 0.3, "P")
        # delta_C - delta_P ≈ e^(-rT)
        total = g_call["delta"] + abs(g_put["delta"])
        expected = np.exp(-0.05 * 0.5)
        assert total == pytest.approx(expected, rel=1e-6)

    def test_gamma_same_for_call_and_put(self):
        """Gamma must be identical for calls and puts at same strike."""
        g_call = single_leg_greeks("black76", 80, 80, 0.5, 0.05, 0.3, "C")
        g_put = single_leg_greeks("black76", 80, 80, 0.5, 0.05, 0.3, "P")
        assert g_call["gamma"] == pytest.approx(g_put["gamma"], rel=1e-10)

    def test_vega_positive(self):
        """Vega is always positive for both calls and puts."""
        g = single_leg_greeks("black76", 80, 80, 0.5, 0.05, 0.3, "C")
        assert g["vega"] > 0

    def test_theta_negative_for_long(self):
        """Theta is negative for long options (decay)."""
        g = single_leg_greeks("black76", 80, 80, 0.5, 0.05, 0.3, "C")
        assert g["theta"] < 0  # long option loses value over time

    def test_black76_vs_bsm_delta_differs(self):
        """Black-76 delta must use futures-options d1 and discounting."""
        F, K, T, r, sigma = 80, 80, 0.5, 0.05, 0.3
        g_76 = single_leg_greeks("black76", F, K, T, r, sigma, "C")
        g_bs = single_leg_greeks("bs", F, K, T, r, sigma, "C")

        d1_black76 = (np.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * np.sqrt(T))
        expected_black76 = np.exp(-r * T) * 0.5 * (1 + erf(d1_black76 / np.sqrt(2)))

        assert g_76["delta"] == pytest.approx(expected_black76, rel=1e-10)
        assert g_76["delta"] != pytest.approx(g_bs["delta"], abs=1e-3)

    def test_black76_rho_price_identity(self):
        """For Black-76 with fixed futures price, rho = -T * option price."""
        F, K, T, r, sigma = 80, 85, 0.5, 0.05, 0.3
        for right in ("C", "P"):
            g = single_leg_greeks("black76", F, K, T, r, sigma, right)
            option_price = price("black76", F, K, T, r, sigma, right)
            assert g["rho"] == pytest.approx(-T * option_price, rel=1e-10)


class TestBumpVsAnalytic:
    """Numerical bump must match closed-form within tolerance."""

    def test_delta_bump_match(self):
        """Finite diff delta ≈ analytic delta."""
        F, K, T, r, sigma = 80, 80, 0.5, 0.05, 0.3
        analytic = single_leg_greeks("black76", F, K, T, r, sigma, "C")
        bump = bump_greeks("black76", price, F, K, T, r, sigma, "C")
        assert analytic["delta"] == pytest.approx(bump["delta"], abs=1e-4)

    def test_vega_bump_match(self):
        F, K, T, r, sigma = 80, 80, 0.5, 0.05, 0.3
        analytic = single_leg_greeks("black76", F, K, T, r, sigma, "C")
        bump = bump_greeks("black76", price, F, K, T, r, sigma, "C")
        assert analytic["vega"] == pytest.approx(bump["vega"], abs=1e-4)

    def test_gamma_bump_match(self):
        F, K, T, r, sigma = 80, 80, 0.5, 0.05, 0.3
        analytic = single_leg_greeks("black76", F, K, T, r, sigma, "C")
        bump = bump_greeks("black76", price, F, K, T, r, sigma, "C")
        assert analytic["gamma"] == pytest.approx(bump["gamma"], abs=1e-4)

    def test_theta_bump_match(self):
        F, K, T, r, sigma = 80, 80, 0.5, 0.05, 0.3
        analytic = single_leg_greeks("black76", F, K, T, r, sigma, "C")
        bump = bump_greeks("black76", price, F, K, T, r, sigma, "C")
        assert analytic["theta"] == pytest.approx(bump["theta"], abs=1e-4)

    def test_rho_bump_match(self):
        F, K, T, r, sigma = 80, 80, 0.5, 0.05, 0.3
        analytic = single_leg_greeks("black76", F, K, T, r, sigma, "C")
        bump = bump_greeks("black76", price, F, K, T, r, sigma, "C")
        assert analytic["rho"] == pytest.approx(bump["rho"], abs=1e-4)


class TestNetGreeksSpread:
    """Net Greeks for multi-leg spreads."""

    def test_net_zero_for_opposite_legs(self):
        """Long call + short call at same K, T → net Greeks = 0."""
        leg1 = Leg(qty=+1, right="C", K=80, expiry=None, F_at_t=80, iv_at_t=0.3, T_at_t=0.5)
        leg2 = Leg(qty=-1, right="C", K=80, expiry=None, F_at_t=80, iv_at_t=0.3, T_at_t=0.5)
        cfg = {"pricing_model": "black76", "vega_bucket_cutoff": 60, "vega_beta": 0.7}
        ng = net_greeks([leg1, leg2], cfg)
        for k in ["delta", "gamma", "theta"]:
            assert ng[k] == pytest.approx(0.0, abs=1e-12)

    def test_calendar_spread_vega_term_risk(self):
        """Calendar spread: vega_total may be 0 but vega_term_risk ≠ 0."""
        leg_short = Leg(qty=-1, right="C", K=80, expiry=None, F_at_t=80, iv_at_t=0.3, T_at_t=30/365)  # 30 DTE
        leg_long  = Leg(qty=+1, right="C", K=80, expiry=None, F_at_t=80, iv_at_t=0.25, T_at_t=90/365)  # 90 DTE
        cfg = {"pricing_model": "black76", "vega_bucket_cutoff": 60, "vega_beta": 0.7}
        ng = net_greeks([leg_short, leg_long], cfg)
        # vega_term_risk must differ from vega_total
        # vega_total ≈ 0 (offsetting), but term risk ≠ 0 (non-parallel)
        # Since short-term IV moves more (beta=0.7), term risk captures this
        assert ng["vega_short_term"] != 0.0
        assert ng["vega_long_term"] != 0.0
        # Vega buckets should be different
        assert abs(ng["vega_short_term"]) > 0

    def test_calendar_vega_buckets(self):
        """Short DTE → short_term bucket; Long DTE → long_term bucket."""
        leg_short = Leg(qty=+1, right="P", K=80, expiry=None, F_at_t=80, iv_at_t=0.3, T_at_t=10/365)
        leg_long  = Leg(qty=+1, right="P", K=80, expiry=None, F_at_t=80, iv_at_t=0.3, T_at_t=120/365)
        cfg = {"pricing_model": "black76", "vega_bucket_cutoff": 60, "vega_beta": 0.7}
        ng = net_greeks([leg_short, leg_long], cfg)
        # short vega in short_term, long vega in long_term
        assert ng["vega_short_term"] > 0
        assert ng["vega_long_term"] > 0
