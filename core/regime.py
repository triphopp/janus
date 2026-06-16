"""Regime labeling — rule-based primary, HMM/GMM as offline validator.

IRON RULE: assign_regime_labels() is the ONLY source of regime labels used for
fold splitting. HMM/GMM are offline validators run AFTER folds are created.
Full-sample ML models must NOT label folds — that leaks future information.
"""

from typing import Dict, Optional

import numpy as np
import pandas as pd

from core.causal import broadcast_by_date, causal_rank, causal_vol, to_causal_series


def assign_regime_labels(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """Primary regime labeler — rolling rule-based only.

    No look-ahead: uses expanding/rolling windows anchored at each point.
    Labels derived from cfg['regime_axes'].

    Typical axes:
    - vol_regime: high/med/low based on expanding percentile of realized vol
    - vrp_sign: variance risk premium direction
    - term_structure: contango/backwardation
    - skew_direction: put_skew/call_skew/neutral

    Args:
        df: DataFrame with columns referenced in regime_axes
        cfg: dict with keys [regime_axes, vol_window, return_col, vol_col]

    Returns:
        Series of regime labels (string), same index as df
    """
    axes = cfg.get("regime_axes", ["vol_regime"])
    vol_window = cfg.get("vol_window", 21)
    return_col = cfg.get("return_col", "return_std")
    vol_col = cfg.get("vol_col", "vol_std")
    date_col = cfg.get("date_col", "as_of_date")

    labels = pd.Series("neutral", index=df.index)

    # ── Vol regime (date-grain expanding percentile — PIT safe) ──
    if "vol_regime" in axes and return_col in df.columns:
        if date_col in df.columns:
            returns = to_causal_series(
                df,
                return_col,
                date_col=date_col,
                agg=cfg.get("return_agg", "mean"),
            )
            realized_vol = causal_vol(
                returns,
                vol_window,
                min_periods=cfg.get("vol_min_periods", 5),
            )
            expanding_rank = causal_rank(
                realized_vol,
                min_periods=cfg.get("vol_rank_min_periods", max(5, min(20, vol_window))),
            )
            vol_labels = broadcast_by_date(df, expanding_rank.apply(_vol_label), date_col=date_col)
        else:
            realized_vol = causal_vol(
                df[return_col],
                vol_window,
                min_periods=cfg.get("vol_min_periods", 5),
            )
            expanding_rank = causal_rank(
                realized_vol,
                min_periods=cfg.get("vol_rank_min_periods", max(5, min(20, vol_window))),
            )
            vol_labels = expanding_rank.apply(_vol_label)

        labels = _combine_labels(labels, vol_labels)

    # ── VRP sign ──
    if "vrp_sign" in axes and "vrp" in df.columns:
        if date_col in df.columns:
            vrp = to_causal_series(
                df,
                "vrp",
                date_col=date_col,
                agg=cfg.get("vrp_agg", "median"),
            )
            vrp_values = broadcast_by_date(df, vrp, date_col=date_col)
        else:
            vrp_values = df["vrp"]
        vrp_labels = vrp_values.apply(_vrp_label)
        labels = _combine_labels(labels, vrp_labels)

    # ── Term structure ──
    if "term_structure" in axes and "term_structure_slope" in df.columns:
        if date_col in df.columns:
            ts = to_causal_series(
                df,
                "term_structure_slope",
                date_col=date_col,
                agg=cfg.get("term_structure_agg", "mean"),
            )
            ts_values = broadcast_by_date(df, ts, date_col=date_col)
        else:
            ts_values = df["term_structure_slope"]
        ts_labels = ts_values.apply(_term_structure_label)
        labels = _combine_labels(labels, ts_labels)

    # ── Skew direction ──
    if "skew_direction" in axes and "skew_25d" in df.columns:
        if date_col in df.columns:
            skew = to_causal_series(
                df,
                "skew_25d",
                date_col=date_col,
                agg=cfg.get("skew_agg", "median"),
            )
            skew_values = broadcast_by_date(df, skew, date_col=date_col)
        else:
            skew_values = df["skew_25d"]
        skew_labels = skew_values.apply(_skew_label)
        labels = _combine_labels(labels, skew_labels)

    # ── Event regimes (from cfg event_regimes) ──
    event_regimes = cfg.get("event_regimes", [])
    for er in event_regimes:
        if er in df.columns:
            if date_col in df.columns:
                event = to_causal_series(df, er, date_col=date_col, agg="any")
                event_values = broadcast_by_date(df, event, date_col=date_col)
            else:
                event_values = df[er]
            event_labels = event_values.apply(lambda x: er if pd.notna(x) and bool(x) else f"non_{er}")
            labels = _combine_labels(labels, event_labels)

    return labels


def compute_transition_matrix(labels: pd.Series) -> pd.DataFrame:
    """Compute regime transition probability matrix.

    P[i→j] = P(regime=j at t+1 | regime=i at t)

    Args:
        labels: regime labels in time order

    Returns:
        DataFrame where rows = from, cols = to, values = transition probability
    """
    regimes = sorted(labels.dropna().unique())
    n = len(regimes)
    if n == 0:
        return pd.DataFrame()

    trans = pd.DataFrame(np.zeros((n, n)), index=regimes, columns=regimes)

    prev = labels.iloc[:-1]
    next_ = labels.iloc[1:]

    for r_from in regimes:
        mask = prev == r_from
        if mask.sum() == 0:
            continue
        next_given_from = next_[mask.values]
        counts = next_given_from.value_counts()
        for r_to in regimes:
            trans.loc[r_from, r_to] = counts.get(r_to, 0) / mask.sum()

    return trans


def validate_labels_hmm(
    labels: pd.Series,
    df: pd.DataFrame,
    n_components: int = 3,
) -> dict:
    """HMM offline validator — compare rule-based labels with unsupervised HMM.

    Must NOT replace rule-based labels. Run after folds are created.
    Concordance ≥ 0.7 is acceptable.

    Args:
        labels: rule-based regime labels
        df: DataFrame with features to fit HMM on
        n_components: number of HMM states

    Returns:
        dict with concordance, hmm_labels, adjusted_rand_score
    """
    from sklearn.metrics import adjusted_rand_score
    from hmmlearn import hmm

    # Prepare features
    numeric = df.select_dtypes(include=[np.number]).dropna()
    if numeric.shape[1] < 2 or len(numeric) < 100:
        return {"error": "insufficient data for HMM"}

    # Fit HMM
    model = hmm.GaussianHMM(n_components=n_components, covariance_type="full", n_iter=100)
    model.fit(numeric.values)

    # Predict states
    hmm_states = model.predict(numeric.values)
    hmm_labels = pd.Series(hmm_states, index=numeric.index, name="hmm_label")

    # Align with rule-based labels
    common_idx = labels.index.intersection(hmm_labels.index)
    if len(common_idx) < 10:
        return {"error": "insufficient overlap"}

    ari = adjusted_rand_score(
        labels.loc[common_idx].astype(str),
        hmm_labels.loc[common_idx].astype(str),
    )

    return {
        "concordance": ari,  # ARI ∈ [-1, 1]; 1 = perfect agreement
        "adjusted_rand_score": ari,
        "n_components": n_components,
        "acceptable": ari >= 0.3,  # > 0.3 = better than random
    }


def diversity_check_gmm(windows: list[pd.DataFrame], n_components: int = 3) -> dict:
    """GMM diversity check — verify regime diversity across windows.

    Args:
        windows: list of DataFrames, one per time window
        n_components: GMM components

    Returns:
        dict with cluster_distribution per window, has_diversity
    """
    from sklearn.mixture import GaussianMixture

    # Combine all windows to fit GMM once
    combined = pd.concat([w.select_dtypes(include=[np.number]) for w in windows], ignore_index=True)
    combined = combined.dropna()

    if len(combined) < 50:
        return {"error": "insufficient data"}

    gmm = GaussianMixture(n_components=n_components, random_state=42)
    gmm.fit(combined.values)

    window_distributions = []
    for i, w in enumerate(windows):
        numeric = w.select_dtypes(include=[np.number]).dropna()
        if len(numeric) < 10:
            window_distributions.append(None)
            continue
        clusters = gmm.predict(numeric.values)
        dist = pd.Series(clusters).value_counts(normalize=True).to_dict()
        window_distributions.append(dist)

    # Check if any window is missing a cluster
    all_clusters = set(range(n_components))
    has_diversity = all(
        dist is not None and len(set(dist.keys())) >= n_components - 1
        for dist in window_distributions
    )

    return {
        "window_distributions": window_distributions,
        "has_diversity": has_diversity,
    }


def _combine_labels(current: pd.Series, new: pd.Series) -> pd.Series:
    """Combine two label series with underscore separator."""
    combined = current.astype(str) + "_" + new.astype(str)
    # Clean up: remove leading/trailing underscores from "neutral" or "unknown"
    combined = combined.str.replace("^neutral_", "", regex=True)
    return combined


def _vol_label(pct):
    if pd.isna(pct):
        return "unknown"
    if pct > 0.8:
        return "high_vol"
    if pct < 0.2:
        return "low_vol"
    return "med_vol"


def _vrp_label(x):
    if pd.isna(x):
        return "vrp_unknown"
    return "vrp_positive" if x > 0 else "vrp_negative"


def _term_structure_label(x):
    if pd.isna(x):
        return "term_structure_unknown"
    return "contango" if x > 0 else "backwardation"


def _skew_label(x):
    if pd.isna(x):
        return "unknown_skew"
    if x < -0.05:
        return "put_skew"
    if x > 0.05:
        return "call_skew"
    return "neutral_skew"
