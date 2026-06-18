"""Stage 3 — Walk-forward split, purge/embargo, diversity gate (KL + JS)."""

from typing import List, Tuple

import numpy as np
import pandas as pd


def walk_forward_split(
    df: pd.DataFrame,
    cfg: dict,
) -> List[Tuple[np.ndarray, np.ndarray]]:
    """Generate walk-forward train/val index pairs.

    Expanding or rolling window. Purge/embargo applied after.

    Args:
        df: DataFrame with as_of_date column
        cfg: dict with keys [n_folds, purge_bars, event_embargo_bars, date_col]

    Returns:
        List of (train_indices, val_indices) as integer-position arrays
    """
    n_folds = cfg.get("n_folds", 8)
    date_col = cfg.get("date_col", "as_of_date")

    if date_col not in df.columns:
        # Fallback: sequential split
        n = len(df)
        fold_size = n // (n_folds + 1)
        folds = []
        for i in range(n_folds):
            split = fold_size * (i + 1)
            folds.append((np.arange(split), np.arange(split, min(split + fold_size, n))))
        return folds

    dates = pd.to_datetime(df[date_col])
    unique_dates = pd.Index(dates.dropna().sort_values().unique())
    n = len(unique_dates)
    if n < 2:
        return []

    n_folds = min(n_folds, n - 1)
    fold_size = max(1, n // (n_folds + 1))

    folds = []
    for i in range(n_folds):
        split_idx = fold_size * (i + 1)
        if split_idx >= n:
            break

        val_end = min(split_idx + fold_size, n)
        train_dates = set(unique_dates[:split_idx])
        val_dates = set(unique_dates[split_idx:val_end])

        train_idx = np.flatnonzero(dates.isin(train_dates))
        val_idx = np.flatnonzero(dates.isin(val_dates))
        if len(val_idx) > 0:
            folds.append((train_idx, val_idx))

    return folds


def purge_embargo(
    folds: List[Tuple[np.ndarray, np.ndarray]],
    df: pd.DataFrame,
    cfg: dict,
) -> List[Tuple[np.ndarray, np.ndarray]]:
    """Apply purge and embargo to walk-forward folds.

    Purge: remove training observations whose bar index overlaps with
           val period (e.g. if val starts at t, purge training bars
           that extend into val via their label horizon).
    Embargo: remove training bars within `event_embargo_bars` of val
             to prevent event leakage.

    Args:
        folds: list of (train_idx, val_idx)
        df: DataFrame
        cfg: dict with keys [purge_bars, event_embargo_bars, date_col]

    Returns:
        Purged/embargoed folds
    """
    purge_bars = cfg.get("purge_bars", 5)
    embargo_bars = cfg.get("event_embargo_bars", 2)

    # "max_dte" sentinel: resolve to the max DTE found in the data (options context)
    if purge_bars == "max_dte":
        purge_bars = int(cfg.get("_max_dte", 90))

    date_col = cfg.get("date_col", "as_of_date")
    label_end_col = cfg.get("label_end_col")
    if label_end_col is None:
        for candidate in ("label_end_time", "label_end_date"):
            if candidate in df.columns:
                label_end_col = candidate
                break
    elif label_end_col not in df.columns:
        raise ValueError(f"label_end_col={label_end_col!r} not found in DataFrame")

    dates = pd.to_datetime(df[date_col]) if date_col in df.columns else None
    unique_dates = pd.Index(dates.dropna().sort_values().unique()) if dates is not None else None

    result = []
    for train_idx, val_idx in folds:
        if len(train_idx) == 0 or len(val_idx) == 0:
            continue

        if dates is not None and unique_dates is not None and len(unique_dates) > 0:
            val_start_time = dates.iloc[val_idx].min()
            if label_end_col is not None:
                label_end = pd.to_datetime(df[label_end_col])
                keep = (label_end.iloc[train_idx] < val_start_time).to_numpy(copy=True)
                if int(embargo_bars) > 0:
                    val_pos = unique_dates.searchsorted(val_start_time)
                    cutoff_pos = val_pos - int(embargo_bars)
                    if cutoff_pos < 0:
                        keep = np.zeros(len(train_idx), dtype=bool)
                    else:
                        allowed_dates = set(unique_dates[:cutoff_pos + 1])
                        keep &= dates.iloc[train_idx].isin(allowed_dates).to_numpy()
                new_train = train_idx[keep]
            else:
                val_pos = unique_dates.searchsorted(val_start_time)
                gap = int(purge_bars) + int(embargo_bars)
                cutoff_pos = val_pos - gap
                if cutoff_pos < 0:
                    new_train = np.array([], dtype=int)
                else:
                    allowed_dates = set(unique_dates[:cutoff_pos + 1])
                    new_train = train_idx[dates.iloc[train_idx].isin(allowed_dates)]
        else:
            val_start = val_idx[0]
            purge_cutoff = val_start - int(purge_bars)
            new_train = train_idx[train_idx <= purge_cutoff]
            embargo_cutoff = val_start - int(embargo_bars) - int(purge_bars)
            new_train = new_train[new_train <= embargo_cutoff]

        if len(new_train) > 0 and len(val_idx) > 0:
            result.append((new_train, val_idx))

    return result


def regime_diversity_gate(
    folds: List[Tuple[np.ndarray, np.ndarray]],
    labels: pd.Series,
    cfg: dict,
) -> pd.DataFrame:
    """Fail fold if: unseen regime in val, concentration > max, KL/JS > threshold.

    Uses both KL (asymmetric — catches severe shift) and
    JS (symmetric, bounded — primary, comparable across folds).

    Args:
        folds: list of (train_idx, val_idx) as integer-position arrays
        labels: regime label per row (same index as folds reference)
        cfg: dict with keys [max_concentration, kl_threshold, js_threshold]

    Returns:
        DataFrame with columns: fold, pass, unseen, conc, kl, js
    """
    max_conc = cfg.get("max_concentration", 0.80)
    kl_thresh = cfg.get("kl_threshold", 0.5)
    js_thresh = cfg.get("js_threshold", 0.3)

    _EMPTY_COLS = ["fold", "pass", "unseen", "conc", "kl", "js"]
    if not folds:
        return pd.DataFrame(columns=_EMPTY_COLS)

    rows = []
    for i, (tr, va) in enumerate(folds):
        p = labels.iloc[va].value_counts(normalize=True)
        q = labels.iloc[tr].value_counts(normalize=True)

        unseen = set(p.index) - set(q.index)

        # Align distributions
        all_labels = sorted(set(p.index) | set(q.index))
        p_aligned = pd.Series({k: p.get(k, 0.0) for k in all_labels})
        q_aligned = pd.Series({k: q.get(k, 0.0) for k in all_labels})

        kl = _kl_div(p_aligned, q_aligned)
        js = _js_div(p_aligned, q_aligned)

        ok = (
            (len(unseen) == 0)
            and p.max() <= max_conc
            and kl <= kl_thresh
            and js <= js_thresh
        )

        rows.append({
            "fold": i,
            "pass": ok,
            "unseen": unseen,
            "conc": p.max(),
            "kl": kl,
            "js": js,
        })

    return pd.DataFrame(rows)


def combinatorial_purged_cv(
    df: pd.DataFrame,
    cfg: dict,
    n_splits: int = 10,
    test_size: float = 0.2,
) -> List[Tuple[np.ndarray, np.ndarray]]:
    """Combinatorial Purged Cross-Validation (Lopez de Prado).

    Generates multiple train/test splits with purge, then samples
    `n_splits` diverse folds.

    Args:
        df: DataFrame
        cfg: config dict
        n_splits: number of CV splits to generate
        test_size: fraction for test

    Returns:
        List of (train_idx, val_idx)
    """
    n = len(df)
    test_n = max(1, int(n * test_size))
    purge_bars = cfg.get("purge_bars", 5)
    if purge_bars == "max_dte":
        purge_bars = int(cfg.get("_max_dte", 90))

    folds = []
    # Generate all possible splits separated by purge
    step = max(test_n // 2, 1)
    for start in range(0, n - test_n, step):
        val_start = start
        val_end = min(start + test_n, n)
        train_end = max(0, val_start - purge_bars)

        train_idx = np.arange(0, train_end)
        val_idx = np.arange(val_start, val_end)

        if len(train_idx) > test_n and len(val_idx) > 0:
            folds.append((train_idx, val_idx))

    # Sample n_splits diverse folds
    if len(folds) > n_splits:
        idx = np.linspace(0, len(folds) - 1, n_splits, dtype=int)
        folds = [folds[i] for i in idx]

    return folds


# ── divergence helpers ──

def _kl_div(p: pd.Series, q: pd.Series, eps: float = 1e-10) -> float:
    """KL divergence: KL(p || q). Asymmetric — catches severe shift.

    KL = Σ p(x) * log(p(x) / q(x))
    Becomes infinity if p has a category q doesn't (unseen regime).
    """
    p = np.clip(p.values, eps, 1.0)
    q = np.clip(q.values, eps, 1.0)
    return float(np.sum(p * np.log(p / q)))


def _js_div(p: pd.Series, q: pd.Series) -> float:
    """Jensen-Shannon divergence — symmetric, bounded [0, ln(2)].

    JS = ½ KL(p || m) + ½ KL(q || m)  where m = ½(p + q)
    Primary diversity metric — comparable across folds.
    """
    m = 0.5 * (p + q)
    eps = 1e-10
    p_arr = np.clip(p.values, eps, 1.0)
    q_arr = np.clip(q.values, eps, 1.0)
    m_arr = np.clip(m.values, eps, 1.0)
    return 0.5 * np.sum(p_arr * np.log(p_arr / m_arr)) + 0.5 * np.sum(q_arr * np.log(q_arr / m_arr))
