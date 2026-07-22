"""Backtest-readiness regression tests.

These lock the cross-module behavior from issue 035: product identity must be
resolved before model policy and WTI American options must select the runtime
BAW engine without silently falling back to a European model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


WTI_HEADER = (
    "TRADE DATE|HUB|PRODUCT|STRIP|CONTRACT|CONTRACT TYPE|STRIKE|"
    "SETTLEMENT PRICE|NET CHANGE|EXPIRATION DATE|PRODUCT_ID|"
    "OPTION_VOLATILITY|DELTA_FACTOR"
)


def _write_wti_chain(path: Path) -> Path:
    rows = [
        "9/25/2024|WTI|WTI Crude Futures|11/1/2024|T|F||70.00000|0.00000|11/1/2024|425||",
        "9/25/2024|WTI|WTI Crude Futures|11/1/2024|T|C|70.0000|2.10000|0.05000|10/17/2024|425|30.00000|0.50000",
        "9/25/2024|WTI|WTI Crude Futures|11/1/2024|T|P|70.0000|1.90000|0.03000|10/17/2024|425|31.00000|-0.50000",
    ]
    path.write_text(WTI_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def _base_cfg(**overrides) -> dict:
    cfg = {
        "family": "futures_options",
        "provider": "settlement",
        "pricing_model": "auto",
        "iv_source": "provided",
        "rf_rate": 0.05,
        "compute_greeks": False,
        "check_pcp": False,
        "vol_window": 5,
        "iv_raw_unit": "percent",
        "exchange_tz": "America/New_York",
        "settlement_release_time": "14:30",
        "available_at_lag": {"settlement": "0h"},
        "dte": {"basis": "calendar", "day_count": "act_365"},
        "symbol": {"product_id": 425, "contract_root": "T", "hub": "WTI"},
        "export": {
            "product": "WTI",
            "underlying_root": "CL",
            "option_root": "LO",
            "exchange": "NYMEX",
            "currency": "USD",
            "price_unit": "USD_per_barrel",
            "contract_unit": "1000_barrels",
            "price_tick": 0.01,
        },
        "exchange_calendar": "NYMEX",
    }
    cfg.update(overrides)
    return cfg


def _load_raw(path: Path, cfg: dict) -> pd.DataFrame:
    from ingestion.settlement_loader import SettlementLoader
    from ingestion.symbology import Symbology

    return SettlementLoader(Symbology(), cfg=cfg).fetch(
        str(path),
        "2024-09-25",
        "2024-09-26",
    )


def test_official_auto_wti_american_uses_baw_without_european_fallback(tmp_path):
    """Regression: official auto prices American WTI with the implemented BAW engine."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    cfg = _base_cfg(require_fixed_data_version=True)
    raw = _load_raw(_write_wti_chain(tmp_path / "wti.psv"), cfg)

    prepared, prepared_cfg = FuturesOptionsAdapter(cfg).prepare(raw)
    options = prepared[prepared["instrument_type"] == "option"]

    assert prepared_cfg["pricing_model"] == "black76_baw"
    assert set(options["pricing_model_source"]) == {"policy_default"}
    assert options["pricing_model_contract_match"].all()
    assert not options["is_model_approximation"].all()


def test_diagnostic_auto_wti_american_export_uses_baw_contract_identity(tmp_path):
    """Regression: diagnostic mode also uses BAW once the runtime exists."""
    from adapters.futures_options_adapter import FuturesOptionsAdapter
    from core import option_chain_export as oce

    cfg = _base_cfg(
        preset="diagnostic",
        require_fixed_data_version=False,
        allow_model_approximation=True,
    )
    raw = _load_raw(_write_wti_chain(tmp_path / "wti.psv"), cfg)
    prepared, prepared_cfg = FuturesOptionsAdapter(cfg).prepare(raw)

    result = oce.write_option_chain_export(
        prepared,
        prepared_cfg,
        {"status": "ready", "reasons": []},
        tmp_path / "run",
    )

    csv_path = Path(result["option_chain_greeks_csv"])
    manifest_path = Path(result["option_chain_greeks_manifest"])
    assert csv_path.parts[-3:] == ("exports", "option_chain_greeks", "option_chain_greeks.csv")
    frame = pd.read_csv(csv_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert set(frame["pricing_model"]) == {"black76_baw"}
    assert set(frame["pricing_model_target"]) == {"black76_baw"}
    assert set(frame["pricing_model_source"]) == {"policy_default"}
    assert set(frame["pricing_model_contract_match"].astype(str).str.lower()) == {"true"}
    assert set(frame["pricing_model_contract_reason"]) == {"policy_default_contract_match"}
    assert set(frame["is_model_approximation"].astype(str).str.lower()) == {"false"}
    assert set(frame["product_family"]) == {"futures_options"}
    assert set(frame["option_underlying_type"]) == {"future"}
    assert set(frame["exercise_style"]) == {"american"}

    assert manifest["pricing_model"] == "black76_baw"
    assert manifest["pricing_model_target"] == "black76_baw"
    assert manifest["is_model_approximation"] is False
    assert manifest["pricing_model_contract_match"] is True
    assert manifest["root_provenance"]["option_root_source"] == "source_option_root"
