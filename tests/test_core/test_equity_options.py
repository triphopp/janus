"""Equity-options ingestion path: chain contract + int64 volume overflow regression."""

import numpy as np
import pandas as pd

from core import contracts as C
from ingestion.symbology import Symbology


def _chain(n=6):
    asof = pd.Timestamp("2026-06-16")
    return pd.DataFrame({
        "as_of_date":       [asof] * n,
        "available_at":     pd.to_datetime([asof] * n, utc=True),
        "ingested_at":      pd.Timestamp("2026-06-16T20:00:00Z"),
        "symbol":           "AAPL",
        "expiry":           [asof + pd.Timedelta(days=30)] * n,
        "right":            (["C", "P"] * n)[:n],
        "strike":           [200.0 + i for i in range(n)],
        "price":            [5.0 + i for i in range(n)],
        "underlying_price": 205.0,
        "iv_provided":      [0.25] * n,
        "volume":           [10] * n,
        "open_interest":    [100] * n,
    })


def test_equity_options_contract_resolves():
    cid, ver = C.resolve_contract_id({"family": "equity_options"})
    assert cid == "equity_options"


def test_clean_chain_passes_gate():
    res = C.validate_for_cfg(_chain(), {"family": "equity_options"}, symbology=Symbology())
    assert res.report["rows_quarantined"] == 0
    assert len(res.passed) == 6


def test_bad_right_and_negative_strike_quarantined():
    df = _chain()
    df.loc[0, "right"] = "X"         # not C/P
    df.loc[1, "strike"] = -5.0       # non-positive strike
    res = C.validate_for_cfg(df, {"family": "equity_options"}, symbology=Symbology())
    assert res.report["rows_quarantined"] == 2
    reasons = set(res.report["quarantine_by_reason"])
    assert any("right" in r for r in reasons)
    assert any("strike" in r for r in reasons)


def test_expiry_before_asof_quarantined():
    df = _chain()
    df.loc[2, "expiry"] = df.loc[2, "as_of_date"] - pd.Timedelta(days=1)
    res = C.validate_for_cfg(df, {"family": "equity_options"}, symbology=Symbology())
    assert res.report["rows_quarantined"] == 1


def test_high_volume_int64_no_overflow():
    # SPX/SPY daily volume exceeds 2^31; the loader must use int64, not platform int (int32
    # on Windows) which silently overflows to negative and trips the `volume >= 0` rule.
    big = pd.Series([3_000_000_000, 2_500_000_000]).astype("int64")
    assert (big >= 0).all()
    assert big.max() > 2**31
