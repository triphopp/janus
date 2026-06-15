"""v1.4 tests: performance attribution waterfall."""

import pandas as pd
import pytest

from core.attribution import to_frame, waterfall


def test_options_waterfall_uses_greek_layers_and_costs():
    trades = pd.DataFrame({
        "pnl_gross": [1000.0],
        "delta_entry": [0.5],
        "d_underlying": [20.0],
        "gamma_entry": [0.1],
        "d_underlying_sq": [400.0],
        "theta_entry": [5.0],
        "days_held": [10.0],
        "vega_total_entry": [100.0],
        "d_iv_parallel": [0.01],
        "vega_term_risk": [-200.0],
        "d_iv_term_slope": [0.02],
        "tx_cost": [25.0],
        "financing_cost": [5.0],
    })
    result = waterfall(trades, {"family": "futures_options"})
    assert result.gross == 1000.0
    assert result.cost == 25.0
    assert result.financing == 5.0
    assert result.net == 970.0
    assert any(layer.name == "vega_term" for layer in result.layers)


def test_equity_waterfall_leaves_alpha_as_residual():
    trades = pd.DataFrame({
        "pnl_gross": [2100.0],
        "market_beta": [1.0],
        "market_return": [0.01],
        "portfolio_value": [100000.0],
        "tx_cost": [100.0],
    })
    result = waterfall(trades, {"family": "equity"})
    assert result.residual == pytest.approx(1100.0)
    assert result.net == 2000.0

    frame = to_frame(result)
    assert "net" in set(frame["layer"])
