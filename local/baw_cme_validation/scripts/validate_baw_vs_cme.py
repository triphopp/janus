"""Validate janus core.pricing black76_baw against CME-published
OPTION_VOLATILITY / DELTA_FACTOR in data/WTI.csv, across several
risk-free rate assumptions.

Usage:
    python3 local/baw_cme_validation/scripts/validate_baw_vs_cme.py [--sample-frac 0.2] [--seed 7]

Outputs (under local/baw_cme_validation/output/):
    row_level_results.csv  - one row per sampled option, iv/delta error per tested rate
    rate_summary.csv       - MAE / median-abs / bias per rate, aggregated across the sample
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from core.pricing import solve_iv  # noqa: E402
from core.greeks import single_leg_greeks  # noqa: E402

CSV_PATH = REPO_ROOT / "data" / "WTI.csv"
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"

RATES = [-0.02, -0.01, -0.005, 0.0, 0.005, 0.01, 0.02, 0.03, 0.05]


def load_matched_options() -> pd.DataFrame:
    df = pd.read_csv(CSV_PATH, sep="|", low_memory=False)
    df.columns = [c.strip() for c in df.columns]

    df["TRADE DATE"] = pd.to_datetime(df["TRADE DATE"])
    df["EXPIRATION DATE"] = pd.to_datetime(df["EXPIRATION DATE"])
    df["STRIP"] = pd.to_datetime(df["STRIP"])

    futures = df[df["CONTRACT TYPE"] == "F"]
    fut_key = futures.set_index(["TRADE DATE", "STRIP", "PRODUCT_ID"])["SETTLEMENT PRICE"]
    fut_key = fut_key[~fut_key.index.duplicated(keep="first")]

    opts = df[df["CONTRACT TYPE"].isin(["C", "P"])].copy()
    opts = opts.dropna(subset=["STRIKE", "SETTLEMENT PRICE", "OPTION_VOLATILITY", "DELTA_FACTOR"])

    idx = pd.MultiIndex.from_arrays([opts["TRADE DATE"], opts["STRIP"], opts["PRODUCT_ID"]])
    opts["F"] = fut_key.reindex(idx).values
    opts = opts.dropna(subset=["F"])
    opts = opts[opts["SETTLEMENT PRICE"] > 0.001]

    opts["T"] = (opts["EXPIRATION DATE"] - opts["TRADE DATE"]).dt.days / 365.0
    opts = opts[opts["T"] > 0]

    return opts.reset_index(drop=True)


def rate_col(prefix: str, rate: float) -> str:
    return f"{prefix}_r{rate:+.3f}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-frac", type=float, default=0.20)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading WTI.csv and matching options to futures settlement price...")
    opts = load_matched_options()
    print(f"Matched option rows available: {len(opts):,}")

    n = int(len(opts) * args.sample_frac)
    sample = opts.sample(n=n, random_state=args.seed).reset_index(drop=True)
    print(f"Sampling {args.sample_frac:.0%} -> {len(sample):,} rows (seed={args.seed})")

    row_records = []
    t0 = time.time()
    for i, row in sample.iterrows():
        F = float(row["F"])
        K = float(row["STRIKE"])
        T = float(row["T"])
        mkt_price = float(row["SETTLEMENT PRICE"])
        right = row["CONTRACT TYPE"]
        given_iv = float(row["OPTION_VOLATILITY"]) / 100.0
        given_delta = float(row["DELTA_FACTOR"])

        rec = {
            "trade_date": row["TRADE DATE"].date(),
            "strip": row["STRIP"].date(),
            "contract": row["CONTRACT"],
            "product_id": row["PRODUCT_ID"],
            "strike": K,
            "right": right,
            "expiration_date": row["EXPIRATION DATE"].date(),
            "T_years": T,
            "F": F,
            "mkt_price": mkt_price,
            "given_iv": given_iv,
            "given_delta": given_delta,
        }

        for r in RATES:
            try:
                model_iv = solve_iv("black76_baw", mkt_price, F, K, T, r, right)
            except Exception:
                model_iv = np.nan
            iv_err = model_iv - given_iv if np.isfinite(model_iv) else np.nan

            try:
                model_delta = single_leg_greeks("black76_baw", F, K, T, r, given_iv, right)["delta"]
            except Exception:
                model_delta = np.nan
            delta_err = model_delta - given_delta if np.isfinite(model_delta) else np.nan

            rec[rate_col("iv_err", r)] = iv_err
            rec[rate_col("delta_err", r)] = delta_err

        row_records.append(rec)

        if (i + 1) % 20000 == 0:
            elapsed = time.time() - t0
            rate_per_s = (i + 1) / elapsed
            eta = (len(sample) - (i + 1)) / rate_per_s
            print(f"  {i + 1:,}/{len(sample):,} rows done, {elapsed:6.1f}s elapsed, ETA {eta:6.1f}s")

    row_df = pd.DataFrame(row_records)
    row_out = OUTPUT_DIR / "row_level_results.csv"
    row_df.to_csv(row_out, index=False)
    print(f"Wrote {row_out} ({len(row_df):,} rows)")

    summary_records = []
    for r in RATES:
        iv_errs = row_df[rate_col("iv_err", r)].dropna()
        dl_errs = row_df[rate_col("delta_err", r)].dropna()
        summary_records.append({
            "rate": r,
            "n_iv": len(iv_errs),
            "iv_mae": iv_errs.abs().mean() if len(iv_errs) else np.nan,
            "iv_median_abs": iv_errs.abs().median() if len(iv_errs) else np.nan,
            "iv_bias": iv_errs.mean() if len(iv_errs) else np.nan,
            "n_delta": len(dl_errs),
            "delta_mae": dl_errs.abs().mean() if len(dl_errs) else np.nan,
            "delta_median_abs": dl_errs.abs().median() if len(dl_errs) else np.nan,
            "delta_bias": dl_errs.mean() if len(dl_errs) else np.nan,
        })

    summary_df = pd.DataFrame(summary_records)
    summary_out = OUTPUT_DIR / "rate_summary.csv"
    summary_df.to_csv(summary_out, index=False)
    print(f"Wrote {summary_out}")
    print()
    print(summary_df.to_string(index=False))

    best_iv_rate = summary_df.loc[summary_df["iv_mae"].idxmin(), "rate"]
    best_delta_rate = summary_df.loc[summary_df["delta_mae"].idxmin(), "rate"]
    print()
    print(f"Best-fit rate by IV MAE:    {best_iv_rate}")
    print(f"Best-fit rate by delta MAE: {best_delta_rate}")


if __name__ == "__main__":
    main()
