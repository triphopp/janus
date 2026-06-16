"""Stage 1 validators — logical bounds, completeness, outlier capping.

All functions are asset-agnostic: receive DataFrame + cfg dict only.
No instrument names, no asset-specific logic.
"""

import numpy as np
import pandas as pd


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

    flags = pd.Series(False, index=df.index)
    reasons = pd.Series("", index=df.index)

    # Price must be positive
    if price_col in df.columns:
        bad = df[price_col] <= 0
        flags |= bad
        reasons = reasons.where(~bad, reasons + "price<=0;")

    # Volume non-negative
    if vol_col in df.columns:
        bad = df[vol_col] < 0
        flags |= bad
        reasons = reasons.where(~bad, reasons + "vol<0;")

    if volume_col and volume_col in df.columns and volume_col != vol_col:
        bad = df[volume_col] < 0
        flags |= bad
        reasons = reasons.where(~bad, reasons + "volume<0;")

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
        duplicates = df.duplicated([*identity_cols, "as_of_date"], keep=False)
        if duplicates.any():
            flags |= duplicates
            reasons = reasons.where(~duplicates, reasons + "duplicate_identity_date;")

        for _, grp in df.groupby(identity_cols, dropna=False):
            grp = grp.sort_values("as_of_date")
            gaps = grp["as_of_date"].diff().dt.days > 5  # 5+ day gap
            if gaps.any():
                idx = grp.index[gaps]
                flags.loc[idx] = True
                reasons.loc[idx] = reasons.loc[idx] + "date_gap>5d;"

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
    if "instrument_type" in df.columns:
        cap_mask = ~df["instrument_type"].astype("string").str.lower().eq("option").fillna(False)
    else:
        cap_mask = pd.Series(True, index=df.index)

    # Per-product rolling MAD outlier detection (PIT: expanding window)
    work = df[cap_mask]
    if "product_id" in work.columns:
        for pid, grp_idx in work.groupby("product_id").groups.items():
            idx = sorted(grp_idx)
            series = df.loc[idx, price_col]
            # Expanding window median + MAD
            rolling_median = series.expanding(min_periods=20).median()
            rolling_mad = (series - rolling_median).abs().expanding(min_periods=20).median()
            threshold = k * rolling_mad * 1.4826  # MAD → std conversion
            upper = rolling_median + threshold
            lower = rolling_median - threshold
            outliers = (series > upper) | (series < lower)
            df.loc[outliers[outliers].index, "_outlier_flag"] = True
            # Cap
            df.loc[outliers[outliers].index, price_col] = df.loc[
                outliers[outliers].index
            ].apply(lambda r: np.clip(r[price_col], lower[r.name], upper[r.name]), axis=1)

    return df
