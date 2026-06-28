"""Public-safe WTI-incident regression fixture (issue 001).

Reproduces the *structure* and *failure mode* of the WTI data incident without
publishing any licensed vendor rows. All numbers are synthetic. The frame mixes:

- futures rows (one underlying settlement per trade date), and
- option rows (a call/put chain across strikes per trade date),

and deliberately injects two domain failures that the pipeline must surface:

1. Provider/model **IV mismatch** — one call carries an exchange IV wildly
   inconsistent with the IV implied by its (clean) settlement price.
2. **Put-call-parity mismatch** — one call/put pair whose prices break parity.

Because the chain has many option rows per trade date but only one future per
date, it also exercises **grain separation** and proves that reconciling options
to the underlying future must use domain keys, not row index.

The builder prices the *clean* options with Black-76 at ``BASE_IV`` so that the
only flagged rows are the ones we intentionally corrupt.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from core import pricing as _pricing

# ── WTI-style product identity (synthetic; bypasses symbology) ────────────────
PRODUCT_ID = 425
CONTRACT_ROOT = "T"
HUB = "WTI"
PRODUCT_NAME = "WTI Crude Oil Options"

BASE_IV = 0.30
RF_RATE = 0.05
DELIVERY_MONTH = pd.Timestamp("2024-11-01")
EXPIRY = pd.Timestamp("2024-10-17")
STRIKES = (65.0, 70.0, 75.0)

# (trade_date, underlying futures settlement)
SESSIONS = (
    (pd.Timestamp("2024-09-24"), 70.00),
    (pd.Timestamp("2024-09-25"), 71.00),
)

# Corrupted contracts (reproduce the incident):
_IV_MISMATCH_STRIKE = 75.0          # call carries an absurd exchange IV
_IV_MISMATCH_PROVIDED = 2.50        # 250% — inconsistent with its clean price
_PCP_MISMATCH_STRIKE = 65.0         # call price inflated → parity breaks
_PCP_CALL_PRICE_FACTOR = 1.8

_T_DAY = pd.Timedelta(days=1)


def _t_years(as_of: pd.Timestamp) -> float:
    return max((EXPIRY - as_of).days, 0) / 365.0


def build_wti_incident_frame() -> pd.DataFrame:
    """Build the RAW_SCHEMA-compatible WTI-incident frame (source of truth)."""
    rows: list[dict] = []
    available_lag = pd.Timedelta(hours=3)
    ingested_at = pd.Timestamp("2024-09-26T00:00:00Z")

    prev_future_price: float | None = None
    for as_of, fut_price in SESSIONS:
        available_at = pd.Timestamp(as_of, tz="UTC") + available_lag
        # ── Underlying future (one row per date) ──
        rows.append({
            "as_of_date": as_of,
            "available_at": available_at,
            "ingested_at": ingested_at,
            "timestamp": pd.NaT,
            "product_id": PRODUCT_ID,
            "contract_root": CONTRACT_ROOT,
            "hub": HUB,
            "instrument_type": "future",
            "right": None,
            "strike": np.nan,
            "delivery_month": DELIVERY_MONTH,
            "expiry": DELIVERY_MONTH,
            "price": float(fut_price),
            "net_change": (np.nan if prev_future_price is None
                           else float(fut_price - prev_future_price)),
            "iv_provided": np.nan,
            "delta_provided": np.nan,
            "provider": "settlement",
        })
        prev_future_price = fut_price

        # ── Option chain (call + put per strike) ──
        t_years = _t_years(as_of)
        for strike in STRIKES:
            for right in ("C", "P"):
                clean_price = _pricing.price(
                    model="black76", S_or_F=fut_price, K=strike,
                    T=t_years, r=RF_RATE, sigma=BASE_IV, right=right,
                )
                price = clean_price
                iv_provided = BASE_IV

                if right == "C" and strike == _IV_MISMATCH_STRIKE:
                    # Clean price, corrupted exchange IV → provider/model mismatch.
                    iv_provided = _IV_MISMATCH_PROVIDED
                if right == "C" and strike == _PCP_MISMATCH_STRIKE:
                    # Inflated call premium → put-call parity breaks for this pair.
                    price = clean_price * _PCP_CALL_PRICE_FACTOR

                rows.append({
                    "as_of_date": as_of,
                    "available_at": available_at,
                    "ingested_at": ingested_at,
                    "timestamp": pd.NaT,
                    "product_id": PRODUCT_ID,
                    "contract_root": CONTRACT_ROOT,
                    "hub": HUB,
                    "instrument_type": "option",
                    "right": right,
                    "strike": float(strike),
                    "delivery_month": DELIVERY_MONTH,
                    "expiry": EXPIRY,
                    "price": float(round(price, 2)),
                    "net_change": np.nan,
                    "iv_provided": float(iv_provided),
                    "delta_provided": np.nan,
                    "provider": "settlement",
                })

    df = pd.DataFrame(rows)
    for col in ("as_of_date", "delivery_month", "expiry"):
        df[col] = pd.to_datetime(df[col])
    df["available_at"] = pd.to_datetime(df["available_at"], utc=True)
    df["ingested_at"] = pd.to_datetime(df["ingested_at"], utc=True)
    return df


def incident_pipeline_cfg() -> dict:
    """Config that turns the option-market checks ON for the incident frame.

    The production WTI config disables PCP/Greeks for the huge live chain; the
    regression fixture is tiny, so we enable the checks to prove they fire.
    """
    return {
        "family": "futures_options",
        "pricing_model": "black76",
        "iv_source": "provided",
        "rf_rate": RF_RATE,
        "validate_provided_iv": True,
        "check_pcp": True,
        "compute_greeks": True,
        "vol_window": 5,
        "dte": {"basis": "calendar", "day_count": "act_365",
                "exclude_expiry_date": True},
        "option_quality": {"iv_diff_threshold": 0.10},
    }


def write_incident_csv(path: str | Path) -> Path:
    """Write a human-inspectable pipe-delimited evidence copy of the fixture."""
    df = build_wti_incident_frame()
    out = pd.DataFrame({
        "TRADE DATE": df["as_of_date"].dt.strftime("%-m/%-d/%Y"),
        "HUB": df["hub"],
        "PRODUCT": PRODUCT_NAME,
        "STRIP": df["delivery_month"].dt.strftime("%-m/%-d/%Y"),
        "CONTRACT": df["contract_root"],
        "CONTRACT TYPE": df["right"].fillna("F"),
        "STRIKE": df["strike"],
        "SETTLEMENT PRICE": df["price"],
        "NET CHANGE": df["net_change"],
        "EXPIRATION DATE": df["expiry"].dt.strftime("%-m/%-d/%Y"),
        "PRODUCT_ID": df["product_id"],
        # Evidence CSV stores IV as percent, like the raw vendor file.
        "OPTION_VOLATILITY": (df["iv_provided"] * 100.0).round(5),
        "DELTA_FACTOR": df["delta_provided"],
    })
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, sep="|", index=False)
    return path


if __name__ == "__main__":  # regenerate the evidence CSV
    here = Path(__file__).parent / "wti_incident"
    p = write_incident_csv(here / "wti_incident_settlement.csv")
    print(f"wrote {p}")
