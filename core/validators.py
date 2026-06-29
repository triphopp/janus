"""Stage 1 validators — logical bounds, completeness, outlier capping.

All functions are asset-agnostic: receive DataFrame + cfg dict only.
No instrument names, no asset-specific logic.
"""

import pandas as pd
import numpy as np


def logical_bounds_check(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Flag rows that violate logical bounds.

    Checks:
    - price > 0
    - volume >= 0
    - IV > 0 (if column present)
    - strike > 0 (if column present)
    - bid <= ask (if columns present)

    Args:
        df: DataFrame with price_col, vol_col from cfg
        cfg: dict with keys [price_col, vol_col]

    Returns:
        DataFrame with added flag columns (_bound_flag, _bound_reason)
    """
    df = df.copy()
    price_col = cfg.get("price_col", "price")
    vol_col = cfg.get("vol_col", "volume")
    volume_col = cfg.get("volume_col")
    option_price_col = cfg.get("option_price_col", "option_price")
    bid_col = cfg.get("bid_col", "bid")
    ask_col = cfg.get("ask_col", "ask")

    flags = pd.Series(False, index=df.index)
    reasons = pd.Series("", index=df.index)

    # Price must be positive
    if price_col in df.columns:
        bad = df[price_col] <= 0
        flags |= bad
        reasons = reasons.where(~bad, reasons + "price<=0;")

    option_mask = _option_mask(df)
    premium = None
    if option_mask.any():
        if option_price_col in df.columns:
            premium = pd.to_numeric(df[option_price_col], errors="coerce")
        elif "price" in df.columns:
            premium = pd.to_numeric(df["price"], errors="coerce")

    if premium is not None:
        bad = option_mask & (premium <= 0)
        flags |= bad
        reasons = reasons.where(~bad, reasons + "option_price<=0;")

        if cfg.get("validate_intrinsic_bounds", True):
            intrinsic = _option_intrinsic(df)
            if intrinsic is not None:
                tol = float(cfg.get("premium_intrinsic_tolerance", 1e-8))
                bad = option_mask & premium.notna() & intrinsic.notna() & (premium + tol < intrinsic)
                flags |= bad
                reasons = reasons.where(~bad, reasons + "option_price<intrinsic;")

    # Volume non-negative
    if vol_col in df.columns:
        bad = df[vol_col] < 0
        flags |= bad
        reasons = reasons.where(~bad, reasons + "vol<0;")

    if volume_col and volume_col in df.columns and volume_col != vol_col:
        bad = df[volume_col] < 0
        flags |= bad
        reasons = reasons.where(~bad, reasons + "volume<0;")

    if bid_col in df.columns and ask_col in df.columns:
        bid = pd.to_numeric(df[bid_col], errors="coerce")
        ask = pd.to_numeric(df[ask_col], errors="coerce")
        bad = bid.notna() & ask.notna() & (bid > ask)
        flags |= bad
        reasons = reasons.where(~bad, reasons + "bid>ask;")

    # IV positive
    iv_col = "iv_provided"
    if iv_col in df.columns:
        bad = df[iv_col] <= 0
        flags |= bad
        reasons = reasons.where(~bad, reasons + "iv<=0;")

    # Strike positive
    if "strike" in df.columns:
        bad = df["strike"] <= 0
        flags |= bad
        reasons = reasons.where(~bad, reasons + "strike<=0;")

    df["_bound_flag"] = flags
    df["_bound_reason"] = reasons
    return df


def missing_completeness(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Check data completeness — missing dates, sparse OI.

    Args:
        df: DataFrame with as_of_date, and optionally open_interest / volume
        cfg: dict with keys [min_oi, futures_oi_floor]

    Returns:
        DataFrame with _missing_flag and _missing_reason columns
    """
    df = df.copy()
    min_oi = cfg.get("min_oi", 100)
    min_volume = cfg.get("min_volume")
    volume_col = cfg.get("volume_col", "volume")
    flags = pd.Series(False, index=df.index)
    reasons = pd.Series("", index=df.index)

    identity_cols = cfg.get("identity_cols")
    if identity_cols is None:
        identity_cols = [col for col in ("product_id", "symbol") if col in df.columns]
    if isinstance(identity_cols, str):
        identity_cols = [identity_cols]

    # Check for duplicate grain and gaps in date sequence per identity.
    if "as_of_date" in df.columns and identity_cols:
        grain_cols = list(dict.fromkeys([*identity_cols, "as_of_date"]))
        duplicates = df.duplicated(grain_cols, keep=False)
        if duplicates.any():
            flags |= duplicates
            reasons = reasons.where(~duplicates, reasons + "duplicate_identity_date;")

        series_identity_cols = [col for col in identity_cols if col != "as_of_date"]
        gap_threshold = int(cfg.get("date_gap_days", cfg.get("max_gap_days", 5)))
        gap_basis = str(cfg.get("date_gap_basis", "business")).lower()
        holidays = cfg.get("calendar_holidays", cfg.get("holidays", [])) or []
        holidays = np.array(pd.to_datetime(holidays, errors="coerce").dropna().date, dtype="datetime64[D]")

        date_series = pd.to_datetime(df["as_of_date"], errors="coerce")
        work = pd.DataFrame({"_date": date_series}, index=df.index)
        for col in series_identity_cols:
            work[col] = df[col]
        sort_cols = [*series_identity_cols, "_date"] if series_identity_cols else ["_date"]
        work = work.sort_values(sort_cols, kind="mergesort")

        if series_identity_cols:
            group_key = series_identity_cols[0] if len(series_identity_cols) == 1 else series_identity_cols
            prev_dates = work.groupby(
                group_key, dropna=False, sort=False, observed=False
            )["_date"].shift(1)
        else:
            prev_dates = work["_date"].shift(1)

        if gap_basis in {"business", "trading"}:
            valid = prev_dates.notna() & work["_date"].notna()
            elapsed = pd.Series(0, index=work.index, dtype="int64")
            if valid.any():
                prev_days = prev_dates.loc[valid].dt.date.to_numpy(dtype="datetime64[D]")
                curr_days = work.loc[valid, "_date"].dt.date.to_numpy(dtype="datetime64[D]")
                elapsed.loc[valid] = np.busday_count(
                    prev_days, curr_days, holidays=holidays
                ).astype("int64")
            gaps = elapsed > gap_threshold
        else:
            gaps = (work["_date"] - prev_dates).dt.days > gap_threshold

        if gaps.any():
            idx = gaps[gaps].index
            flags.loc[idx] = True
            reasons.loc[idx] = reasons.loc[idx] + f"date_gap>{gap_threshold}{gap_basis[0]}d;"

    # Open interest floor
    oi_col = "open_interest"
    if oi_col in df.columns:
        floor = cfg.get("futures_oi_floor", min_oi)
        bad = df[oi_col] < floor
        flags |= bad
        reasons = reasons.where(~bad, reasons + f"OI<{floor};")

    # Equity volume/liquidity floor.
    if min_volume is not None and volume_col in df.columns:
        bad = df[volume_col].fillna(-1) < min_volume
        flags |= bad
        reasons = reasons.where(~bad, reasons + f"volume<{min_volume};")

    df["_missing_flag"] = flags
    df["_missing_reason"] = reasons
    return df


def outlier_cap(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Cap extreme outliers using peer-group or rolling method.

    Point-in-time: uses expanding window (no future data).
    MAD-based: median ± k * MAD (MAD = median absolute deviation).

    Args:
        cfg: dict with keys [price_col, outlier_k, outlier_window]

    Returns:
        DataFrame with price_col capped, _outlier_flag column
    """
    df = df.copy()
    price_col = cfg.get("price_col", "price")
    k = cfg.get("outlier_k", 5.0)

    if price_col not in df.columns:
        return df

    df["_outlier_flag"] = False

    # When instrument_type is present, restrict MAD detection to non-option rows.
    # Option rows carry broadcast underlying prices (one value per date repeated
    # across all strikes), which distort the expanding MAD time-series.
    cap_mask = ~_option_mask(df)
    if not cap_mask.any() and "instrument_type" not in df.columns:
        cap_mask = pd.Series(True, index=df.index)

    # Per-instrument rolling MAD outlier detection (PIT: expanding window).
    # Prefer product_id (futures/options), fall back to symbol (equity), then treat
    # the entire frame as a single series. The original code only handled product_id,
    # leaving equity frames with zero outlier detection (silent dead-code path).
    work = df[cap_mask]
    group_cols = cfg.get("outlier_identity_cols")
    if isinstance(group_cols, str):
        group_cols = [group_cols]
    if group_cols:
        group_cols = [col for col in group_cols if col in work.columns]
    if not group_cols:
        contract_cols = [
            col for col in ("product_id", "contract_root", "hub", "delivery_month", "expiry")
            if col in work.columns
        ]
        if {"product_id", "delivery_month"}.issubset(contract_cols):
            group_cols = contract_cols
        else:
            group_col = next(
                (c for c in ("product_id", "symbol") if c in work.columns), None
            )
            group_cols = [group_col] if group_col is not None else []

    # Equity frames use non-stationary price levels — expanding median on a trending
    # stock (e.g. TSLA 3× rally) anchors to early-year prices, clipping genuine
    # late-year highs as false outliers. Return-level clipping (stationary) is already
    # handled upstream by EquityAdapter._pit_mad_clip. Skip here for equity frames only;
    # futures/options (product_id present) use price-level MAD on stationary spreads.
    if group_cols == ["symbol"] and "product_id" not in work.columns:
        return df

    if len(work) == 0:
        return df

    sort_cols = []
    if group_cols:
        sort_cols.extend(group_cols)
    if "as_of_date" in work.columns:
        sort_cols.append("as_of_date")
    ordered = work.sort_values(sort_cols, kind="mergesort") if sort_cols else work.sort_index()
    price = pd.to_numeric(ordered[price_col], errors="coerce")

    if group_cols:
        group_key = group_cols[0] if len(group_cols) == 1 else group_cols
        tmp = ordered[group_cols].copy()
        tmp["_price"] = price
        rolling_median = (
            tmp.groupby(group_key, dropna=False, sort=False, observed=False)["_price"]
            .expanding(min_periods=20)
            .median()
            .reset_index(level=list(range(len(group_cols))), drop=True)
        )
        abs_dev = (price - rolling_median).abs()
        tmp["_abs_dev"] = abs_dev
        rolling_mad = (
            tmp.groupby(group_key, dropna=False, sort=False, observed=False)["_abs_dev"]
            .expanding(min_periods=20)
            .median()
            .reset_index(level=list(range(len(group_cols))), drop=True)
        )
    else:
        # No identity column — treat whole frame as one series (single-instrument file)
        rolling_median = price.expanding(min_periods=20).median()
        rolling_mad = (price - rolling_median).abs().expanding(min_periods=20).median()

    threshold = k * rolling_mad * 1.4826      # MAD → std conversion
    upper = rolling_median + threshold
    lower = rolling_median - threshold
    outliers = (price > upper) | (price < lower)
    hit = outliers[outliers].index
    if len(hit) > 0:
        df.loc[hit, "_outlier_flag"] = True
        clipped = price.clip(lower=lower, upper=upper)
        df.loc[hit, price_col] = clipped.loc[hit]

    return df


def _option_mask(df: pd.DataFrame) -> pd.Series:
    if "instrument_type" in df.columns:
        typed = df["instrument_type"].astype("string").str.lower().eq("option").fillna(False)
    else:
        typed = pd.Series(False, index=df.index)

    if "right" in df.columns and "strike" in df.columns:
        right = df["right"].astype("string").str.upper()
        inferred = right.isin(["C", "P"]).fillna(False) & df["strike"].notna()
    else:
        inferred = pd.Series(False, index=df.index)
    return typed | inferred


def _option_intrinsic(df: pd.DataFrame) -> pd.Series | None:
    right_col = df.get("right")
    strike_col = df.get("strike")
    if right_col is None or strike_col is None:
        return None

    underlying = None
    for col in ("underlying_price", "F", "S", "price_std"):
        if col in df.columns:
            underlying = pd.to_numeric(df[col], errors="coerce")
            break
    if underlying is None:
        return None

    strike = pd.to_numeric(strike_col, errors="coerce")
    right = right_col.astype("string").str.upper()
    call_intrinsic = (underlying - strike).clip(lower=0)
    put_intrinsic = (strike - underlying).clip(lower=0)
    intrinsic = pd.Series(np.nan, index=df.index)
    intrinsic = intrinsic.where(right != "C", call_intrinsic)
    intrinsic = intrinsic.where(right != "P", put_intrinsic)

    if "T" in df.columns and "r" in df.columns:
        t = pd.to_numeric(df["T"], errors="coerce").clip(lower=0)
        r = pd.to_numeric(df["r"], errors="coerce").fillna(0)
        intrinsic = intrinsic * np.exp(-r * t)
    return intrinsic
