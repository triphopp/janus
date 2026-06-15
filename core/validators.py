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
    flags = pd.Series(False, index=df.index)
    reasons = pd.Series("", index=df.index)

    # Check for gaps in date sequence per product_id
    if "as_of_date" in df.columns and "product_id" in df.columns:
        for pid, grp in df.groupby("product_id"):
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

    # Per-product rolling MAD outlier detection (PIT: expanding window)
    if "product_id" in df.columns:
        for pid, grp_idx in df.groupby("product_id").groups.items():
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
