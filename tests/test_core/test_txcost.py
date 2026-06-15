"""v1.4 tests: transaction cost / liquidity model."""

import pytest

from core.txcost import cost_per_trade


def test_level1_fixed_cost_two_leg_spread():
    legs = [{"qty": -1}, {"qty": 1}]
    cfg = {"txcost": {"level": 1, "commission_per_contract": 2.50, "half_spread_fixed": 0.05}}
    result = cost_per_trade(legs, cfg=cfg)
    assert result.total == pytest.approx(5.10)
    assert result.level == 1


def test_level2_near_expiry_costs_more_than_far_expiry():
    cfg = {
        "txcost": {
            "level": 2,
            "commission_per_contract": 0.0,
            "half_spread_fixed": 0.05,
            "dte_spread_curve": {">60": 1.0, "30-60": 1.4, "14-30": 1.8, "<14": 2.8},
            "spread_regime_mult": {"mid_vol": 1.0},
        }
    }
    near = cost_per_trade([{"qty": 1, "dte": 7, "bid_ask_mid": 0.10}], cfg=cfg)
    far = cost_per_trade([{"qty": 1, "dte": 90, "bid_ask_mid": 0.10}], cfg=cfg)
    assert near.total > far.total


def test_level3_adds_market_impact():
    leg = {"qty": 1, "dte": 30, "bid_ask_mid": 0.10, "participation_rate": 0.04, "price": 10.0}
    cfg2 = {"txcost": {"level": 2, "commission_per_contract": 0.0, "half_spread_fixed": 0.05}}
    cfg3 = {"txcost": {"level": 3, "commission_per_contract": 0.0, "half_spread_fixed": 0.05, "impact_coeff": 0.1}}
    assert cost_per_trade([leg], cfg=cfg3).total > cost_per_trade([leg], cfg=cfg2).total
