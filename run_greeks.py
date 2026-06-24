"""Greek-only runner — compute option Greeks without running the full pipeline.

Workflows:
  A) Prepared rows (CSV or Parquet):
       python run_greeks.py --input options.csv --model black76 --output greeks.parquet

  B) Raw chain via instrument config (minimal option prep):
       python run_greeks.py --instrument bz --data-file WTI.csv \\
           --start 2024-01-01 --end 2024-12-31 --output greeks.parquet

Does NOT run splitter, metrics, reporting, CDC, or dashboard generation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from core.greek_inputs import resolve_greek_inputs
from core.greeks import batch_greeks

_SCHEMA_VERSION = 1


# ── Provenance helpers ───────────────────────────────────────────────────────

def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _file_sha256(path: str) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except Exception:
        return None


# ── I/O helpers ──────────────────────────────────────────────────────────────

def _load_input(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    if p.suffix.lower() == ".parquet":
        return pd.read_parquet(p)
    return pd.read_csv(p)


def _write_output(df: pd.DataFrame, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.suffix.lower() == ".parquet":
        df.to_parquet(p, index=False)
    else:
        df.to_csv(p, index=False)


def _write_summary(summary: dict, output_path: str) -> str:
    p = Path(output_path)
    summary_path = p.with_suffix("").with_suffix(".greek_summary.json")
    summary_path.write_text(json.dumps(summary, indent=2))
    return str(summary_path)


# ── Core logic ────────────────────────────────────────────────────────────────

_IDENTITY_COLS = [
    "as_of_date", "expiry", "instrument", "symbol", "ticker",
    "right", "K", "strike", "product_id", "contract_root",
]

_CONVENTIONS = {
    "theta": "annualized calendar-time decay, -dV/dT",
    "vega": "per 1.0 vol unit",
    "rate": "continuously compounded",
}


def _resolve_T_for_filter(df: pd.DataFrame, dte_cfg: dict | None) -> pd.Series:
    """Resolve T (years) from existing T column or date columns, for DTE filtering."""
    dte_cfg = dte_cfg or {"basis": "calendar", "day_count": "act_365", "exclude_expiry_date": False}
    from core.dte import compute_dte
    T = pd.Series(np.nan, index=df.index)
    if "T" in df.columns:
        T = pd.to_numeric(df["T"], errors="coerce")
    if T.isna().any() and "as_of_date" in df.columns and "expiry" in df.columns:
        for idx in df.index[T.isna()]:
            try:
                t_val = compute_dte(df.at[idx, "as_of_date"], df.at[idx, "expiry"], dte_cfg)
                if t_val > 0:
                    T.at[idx] = t_val
            except Exception:
                pass
    return T


def run_greek_only(
    df: pd.DataFrame,
    *,
    model: str = "black76",
    backend: str = "numpy",
    batch_size: int | None = None,
    dtype: str = "float64",
    div_yield: float | None = None,
    iv_source: str = "computed",
    rf_rate_default: float = 0.0,
    cfg: dict | None = None,
    min_dte: int | None = None,
    max_dte: int | None = None,
    min_option_price: float | None = None,
    max_iv: float | None = None,
    dte_cfg: dict | None = None,
    provenance: dict | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Resolve inputs, apply universe filters, compute Greeks.

    Returns:
        (output_df, summary)
    """
    cfg = cfg or {}
    # Resolve q: explicit arg wins, including an intentional 0.0 override.
    cfg_div_yield = cfg.get("div_yield", 0.0)
    q = float(div_yield) if div_yield is not None else float(cfg_div_yield or 0.0)
    n_input = len(df)

    # Resolve T upfront so DTE filters work even when T is absent but dates exist
    T_for_filter = _resolve_T_for_filter(df, dte_cfg)

    # Universe filters before Greek computation
    mask = pd.Series(True, index=df.index)
    if min_dte is not None:
        mask &= (T_for_filter * 365 >= min_dte) | T_for_filter.isna()
    if max_dte is not None:
        mask &= (T_for_filter * 365 <= max_dte) | T_for_filter.isna()
    if min_option_price is not None:
        for price_col in ("option_price", "mid_price", "price"):
            if price_col in df.columns:
                price_numeric = pd.to_numeric(df[price_col], errors="coerce")
                mask &= (price_numeric >= min_option_price) | price_numeric.isna()
                break
    if max_iv is not None and "iv" in df.columns:
        iv_numeric = pd.to_numeric(df["iv"], errors="coerce")
        mask &= (iv_numeric <= max_iv) | iv_numeric.isna()

    filtered = df[mask].copy()
    n_filtered = len(filtered)

    # Resolve inputs (full coercion + invalid reasons)
    resolved, input_summary = resolve_greek_inputs(
        filtered, cfg=cfg, iv_source=iv_source,
        rf_rate_default=rf_rate_default, dte_cfg=dte_cfg,
    )

    # Warn if iv_provided rows have quality flags
    config_warnings = []
    if iv_source == "provided" and "iv_provided" in filtered.columns and "_iv_quality_flag" in filtered.columns:
        failed = filtered["_iv_quality_flag"].notna() & (filtered["_iv_quality_flag"] != "")
        n_failed = int(failed.sum())
        if n_failed:
            config_warnings.append(
                f"{n_failed} iv_provided rows have _iv_quality_flag set — review before trusting Greeks"
            )

    # Compute Greeks
    greeks_result = batch_greeks(
        model=model,
        S_or_F=resolved["S_or_F"].to_numpy(),
        K=resolved["K"].to_numpy(),
        T=resolved["T"].to_numpy(),
        r=resolved["r"].to_numpy(),
        sigma=resolved["sigma"].to_numpy(),
        right=resolved["right"].to_numpy(),
        q=q,
        backend=backend,
        batch_size=batch_size,
        dtype=dtype,
    )

    # Build output — identity columns first, then Greeks
    out = filtered[[c for c in _IDENTITY_COLS if c in filtered.columns]].copy()
    for greek in ("delta", "gamma", "vega", "theta", "rho"):
        out[greek] = greeks_result[greek]
    out["greek_model"] = model
    out["greek_backend"] = backend
    out["greek_dtype"] = dtype
    out["greek_input_valid"] = resolved["greek_input_valid"].values
    out["greek_invalid_reason"] = resolved["greek_invalid_reason"].values

    # Force NaN on invalid rows
    for greek in ("delta", "gamma", "vega", "theta", "rho"):
        out.loc[~out["greek_input_valid"], greek] = np.nan

    summary: dict = {
        "schema_version": _SCHEMA_VERSION,
        "model": model,
        "backend": backend,
        "dtype": dtype,
        "div_yield": q if model in ("bs", "bsm") else None,
        "universe_filter": {
            "input_rows": n_input,
            "rows_after_filter": n_filtered,
            "rows_dropped": n_input - n_filtered,
        },
        "input_quality": input_summary,
        "output_rows": len(out),
        "conventions": _CONVENTIONS,
        "config_warnings": config_warnings,
        "provenance": provenance or {},
    }

    return out, summary


# ── Instrument-mode (Phase 3) ────────────────────────────────────────────────

def run_instrument_mode(
    instrument: str,
    *,
    data_file: str | None = None,
    start: str | None = None,
    end: str | None = None,
    backend: str = "numpy",
    batch_size: int | None = None,
    dtype: str = "float64",
    iv_source: str = "computed",
    min_dte: int | None = None,
    max_dte: int | None = None,
    min_option_price: float | None = None,
    max_iv: float | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Load raw chain via config, run minimal option prep, compute Greeks.

    Reuses load_config + get_provider + get_adapter from run_pipeline.
    Runs: ingestion → adapter.prepare() → adapter.compute_greeks().
    Does NOT run validators, splitter, metrics, reporting, CDC, or dashboard.

    Returns:
        (output_df, summary)
    """
    from run_pipeline import load_config, get_provider, get_adapter, apply_runtime_overrides

    cfg = load_config(instrument)
    if data_file:
        cfg["data_file"] = data_file
        cfg.setdefault("provider", "settlement")
    cfg = apply_runtime_overrides(
        cfg,
        compute_greeks=True,
        greeks_backend=backend,
        greeks_batch_size=batch_size,
        greeks_dtype=dtype,
        min_dte=min_dte,
        max_dte=max_dte,
        min_option_price=min_option_price,
        iv_cap=max_iv,
    )

    # Ingestion
    provider = get_provider(cfg)
    provider_name = cfg.get("provider", "settlement")
    if provider_name == "settlement":
        data_source = cfg.get("data_file") or instrument
    else:
        data_source = cfg.get("symbol", {}).get("ticker", instrument)

    raw_df = provider.fetch(data_source, start, end)

    # Adapter prep (DTE, IV, underlying mapping, universe filters, quality flags)
    adapter = get_adapter(cfg)
    df, adapted_cfg = adapter.prepare(raw_df)

    # Compute Greeks via adapter (shares same batch_greeks engine)
    df = adapter.compute_greeks(df)

    # Identify option rows — include invalid rows (NaN Greeks) but exclude
    # futures/context rows. Prefer explicit instrument_type column; fallback
    # requires valid right (C/P) AND non-null strike/K so expiry alone on a
    # future row does not pull it into the Greek-only artifact.
    if "instrument_type" in df.columns:
        option_mask = df["instrument_type"].astype(str).str.lower() == "option"
    else:
        right_valid = pd.Series(False, index=df.index)
        if "right" in df.columns:
            right_valid = df["right"].astype(str).str.upper().isin({"C", "P"})
        strike_present = pd.Series(False, index=df.index)
        for col in ("strike", "K"):
            if col in df.columns:
                strike_present |= df[col].notna()
        option_mask = right_valid & strike_present

    out = df[option_mask].copy()

    # Ensure greek_input_valid and greek_invalid_reason columns exist
    if "greek_input_valid" not in out.columns:
        greek_cols = ["delta", "gamma", "vega", "theta", "rho"]
        has_all = out[[c for c in greek_cols if c in out.columns]].notna().all(axis=1)
        out["greek_input_valid"] = has_all
        out["greek_invalid_reason"] = out["greek_input_valid"].map(
            lambda v: "" if v else "adapter_did_not_compute"
        )

    # Add metadata
    model = adapted_cfg.get("pricing_model", "black76")
    out["greek_model"] = model
    out["greek_backend"] = backend
    out["greek_dtype"] = dtype

    # Force NaN on invalid rows for Greek columns
    for col in ("delta", "gamma", "vega", "theta", "rho"):
        if col in out.columns:
            out.loc[~out["greek_input_valid"], col] = np.nan

    # Provenance
    prov: dict = {
        "instrument": instrument,
        "git_commit": _git_commit(),
        "config_model": model,
        "dte_cfg": adapted_cfg.get("dte", {}),
        "iv_source": iv_source,
    }
    if data_file:
        prov["data_file"] = data_file
        prov["data_hash"] = _file_sha256(data_file)

    # Underlying missing count (futures underlying-map diagnostics)
    underlying_missing = 0
    for col in ("underlying_price", "F"):
        if col in df.columns:
            underlying_missing = int(df[col].isna().sum())
            break

    # Invalid rows count
    n_invalid = int((~out["greek_input_valid"]).sum()) if "greek_input_valid" in out.columns else 0

    summary: dict = {
        "schema_version": _SCHEMA_VERSION,
        "mode": "instrument",
        "instrument": instrument,
        "model": model,
        "backend": backend,
        "dtype": dtype,
        "raw_rows": len(raw_df),
        "prepared_rows": len(df),
        "output_rows": len(out),
        "valid_greek_rows": int(out["greek_input_valid"].sum()) if "greek_input_valid" in out.columns else len(out),
        "invalid_greek_rows": n_invalid,
        "underlying_missing_rows": underlying_missing,
        "conventions": _CONVENTIONS,
        "provenance": prov,
        "config_warnings": [],
    }

    return out, summary


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_greeks.py",
        description="Compute option Greeks without running the full Janus pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Prepared CSV (WTI futures options)
  python run_greeks.py --input wti_options.csv --model black76 --output outputs/greeks/wti.parquet

  # Prepared Parquet (AAPL equity options)
  python run_greeks.py --input aapl_options.parquet --model bsm --backend numpy --output outputs/greeks/aapl.parquet

  # Instrument config + raw data file
  python run_greeks.py --instrument bz --data-file WTI.csv \\
      --start 2024-01-01 --end 2024-12-31 --output outputs/greeks/bz.parquet

  # Universe filter before computation
  python run_greeks.py --input options.csv --min-dte 1 --max-dte 90 --max-iv 2.0 --output greeks.csv
""",
    )

    inp = p.add_argument_group("Input")
    inp.add_argument("--input", "-i", metavar="PATH",
                     help="Prepared option rows (CSV or Parquet).")
    inp.add_argument("--instrument", metavar="NAME",
                     help="Instrument name — triggers config-driven mode.")
    inp.add_argument("--data-file", metavar="PATH",
                     help="Raw data file for instrument mode.")
    inp.add_argument("--start", metavar="DATE", help="Start date (YYYY-MM-DD).")
    inp.add_argument("--end", metavar="DATE", help="End date (YYYY-MM-DD).")

    mdl = p.add_argument_group("Model")
    mdl.add_argument("--model", default="black76", choices=["black76", "bs", "bsm"],
                     help="Pricing model. (default: black76)")
    mdl.add_argument("--iv-source", default="computed", choices=["computed", "provided"],
                     help="IV column to use. (default: computed)")
    mdl.add_argument("--rf-rate", type=float, default=0.0,
                     help="Risk-free rate fallback. (default: 0.0)")
    mdl.add_argument("--div-yield", type=float, default=None,
                     help="Dividend yield for BSM model. Defaults to config div_yield, then 0.0.")

    bck = p.add_argument_group("Backend")
    bck.add_argument("--backend", default="numpy", choices=["numpy", "loop", "auto", "cuda"],
                     help="Greek computation backend. (default: numpy)")
    bck.add_argument("--batch-size", type=int, default=None,
                     help="Chunk size for batched computation.")
    bck.add_argument("--dtype", default="float64", choices=["float64", "float32"],
                     help="Floating-point dtype. (default: float64)")

    uni = p.add_argument_group("Universe filters")
    uni.add_argument("--min-dte", type=int, default=None)
    uni.add_argument("--max-dte", type=int, default=None)
    uni.add_argument("--min-option-price", type=float, default=None)
    uni.add_argument("--max-iv", type=float, default=None)

    out = p.add_argument_group("Output")
    out.add_argument("--output", "-o", metavar="PATH", required=True,
                     help="Output path (.csv or .parquet).")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.input and not args.instrument:
        parser.error("Provide --input (prepared rows) or --instrument (config mode).")

    # Instrument mode
    if args.instrument:
        try:
            out_df, summary = run_instrument_mode(
                args.instrument,
                data_file=args.data_file,
                start=args.start,
                end=args.end,
                backend=args.backend,
                batch_size=args.batch_size,
                dtype=args.dtype,
                iv_source=args.iv_source,
                min_dte=args.min_dte,
                max_dte=args.max_dte,
                min_option_price=args.min_option_price,
                max_iv=args.max_iv,
            )
        except Exception as exc:
            print(f"ERROR (instrument mode): {exc}", file=sys.stderr)
            return 1
    else:
        # Prepared-row mode
        try:
            df = _load_input(args.input)
        except Exception as exc:
            print(f"ERROR loading input: {exc}", file=sys.stderr)
            return 1

        if len(df) == 0:
            print("WARNING: input file has zero rows. Writing empty output.", file=sys.stderr)

        prov = {
            "input_file": args.input,
            "input_hash": _file_sha256(args.input),
            "git_commit": _git_commit(),
        }

        try:
            out_df, summary = run_greek_only(
                df,
                model=args.model,
                backend=args.backend,
                batch_size=args.batch_size,
                dtype=args.dtype,
                div_yield=args.div_yield,
                iv_source=args.iv_source,
                rf_rate_default=args.rf_rate,
                min_dte=args.min_dte,
                max_dte=args.max_dte,
                min_option_price=args.min_option_price,
                max_iv=args.max_iv,
                provenance=prov,
            )
        except (ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    summary["output_path"] = args.output

    try:
        _write_output(out_df, args.output)
        summary_path = _write_summary(summary, args.output)
    except Exception as exc:
        print(f"ERROR writing output: {exc}", file=sys.stderr)
        return 1

    valid = summary.get("input_quality", {}).get("valid_rows", summary.get("valid_greek_rows", "?"))
    total = summary.get("input_quality", {}).get("total_rows", summary.get("prepared_rows", "?"))
    print(f"Greeks computed: {valid}/{total} valid rows → {args.output}")
    print(f"Summary: {summary_path}")
    if summary.get("config_warnings"):
        for w in summary["config_warnings"]:
            print(f"WARNING: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
