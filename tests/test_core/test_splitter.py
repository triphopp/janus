"""Splitter tests — no look-ahead, embargo, diversity gate."""

import numpy as np
import pandas as pd
import pytest
from core.splitter import walk_forward_split, purge_embargo, regime_diversity_gate


class TestNoLookAhead:
    """Val fold index must all be > train fold max + purge_bars."""

    def test_val_after_train(self, sample_raw_df):
        cfg = {"n_folds": 4, "date_col": "as_of_date", "purge_bars": 5, "event_embargo_bars": 2}
        folds = walk_forward_split(sample_raw_df, cfg)
        folds = purge_embargo(folds, sample_raw_df, cfg)
        for i, (tr, va) in enumerate(folds):
            if len(tr) > 0 and len(va) > 0:
                assert va.min() > tr.max(), \
                    f"Fold {i}: val[{va.min()}] <= train[{tr.max()}] — look-ahead leak!"

    def test_purge_separation(self, sample_raw_df):
        """Purge ensures gap between train and val."""
        cfg = {"n_folds": 4, "date_col": "as_of_date", "purge_bars": 5, "event_embargo_bars": 0}
        folds = walk_forward_split(sample_raw_df, cfg)
        folds = purge_embargo(folds, sample_raw_df, cfg)
        for tr, va in folds:
            if len(tr) > 0 and len(va) > 0:
                gap = va.min() - tr.max()
                assert gap >= 5, f"Purge gap {gap} < 5"

    def test_walk_forward_no_shared_dates_for_chain_rows(self):
        """All rows for the same decision date stay in one side of a fold."""
        dates = pd.to_datetime(
            ["2024-01-01"] * 5
            + ["2024-01-02"] * 7
            + ["2024-01-03"] * 6
            + ["2024-01-04"] * 8
        )
        df = pd.DataFrame({"as_of_date": dates})
        folds = walk_forward_split(df, {"n_folds": 2, "date_col": "as_of_date"})

        for tr, va in folds:
            train_dates = set(df.iloc[tr]["as_of_date"])
            val_dates = set(df.iloc[va]["as_of_date"])
            assert train_dates.isdisjoint(val_dates)

    def test_purge_uses_time_groups_not_row_count(self):
        """Purge over chain rows should remove whole dates, not a few rows."""
        dates = pd.to_datetime(
            ["2024-01-01"] * 100
            + ["2024-01-02"] * 100
            + ["2024-01-03"] * 100
            + ["2024-01-04"] * 100
            + ["2024-01-05"] * 100
            + ["2024-01-06"] * 100
        )
        df = pd.DataFrame({"as_of_date": dates})
        cfg = {"n_folds": 2, "date_col": "as_of_date", "purge_bars": 2, "event_embargo_bars": 0}
        folds = purge_embargo(walk_forward_split(df, cfg), df, cfg)

        tr, va = folds[0]
        val_start = df.iloc[va]["as_of_date"].min()
        max_train = df.iloc[tr]["as_of_date"].max()

        assert max_train <= pd.Timestamp("2024-01-01")
        assert val_start == pd.Timestamp("2024-01-03")

    def test_label_end_purge_also_applies_embargo(self):
        """Expiry-aware purge should still leave the configured embargo gap."""
        df = pd.DataFrame({
            "as_of_date": pd.date_range("2024-01-01", periods=8, freq="B"),
            "expiry": pd.date_range("2024-01-01", periods=8, freq="B"),
        })
        cfg = {
            "n_folds": 1,
            "date_col": "as_of_date",
            "label_end_col": "expiry",
            "purge_bars": 0,
            "event_embargo_bars": 2,
        }

        folds = purge_embargo(walk_forward_split(df, cfg), df, cfg)

        tr, va = folds[0]
        val_start = df.iloc[va]["as_of_date"].min()
        max_train = df.iloc[tr]["as_of_date"].max()
        unique_dates = pd.Index(df["as_of_date"].sort_values().unique())

        assert unique_dates.get_loc(val_start) - unique_dates.get_loc(max_train) >= 2

    def test_max_dte_fallback_clamps_to_available_dates(self):
        """Fallback path should not let max_dte erase every training fold."""
        df = pd.DataFrame({"as_of_date": pd.date_range("2024-01-01", periods=12, freq="B")})
        cfg = {
            "n_folds": 2,
            "date_col": "as_of_date",
            "purge_bars": "max_dte",
            "_max_dte": 90,
            "event_embargo_bars": 1,
        }

        folds = purge_embargo(walk_forward_split(df, cfg), df, cfg)

        assert folds


class TestDiversityGate:
    """Regime diversity gate — KL + JS."""

    def test_unseen_regime_fails(self, sample_regime_labels):
        """Val with unseen regime must fail."""
        idx = np.arange(500)
        folds = [(idx[:250], idx[250:500])]
        # Modify val labels to include unseen
        labels = sample_regime_labels.copy()
        labels.iloc[250:300] = "crash_regime"  # only in val, not train
        cfg = {"max_concentration": 0.80, "kl_threshold": 0.5, "js_threshold": 0.3}
        result = regime_diversity_gate(folds, labels, cfg)
        assert result.iloc[0]["pass"] == False
        assert "crash_regime" in result.iloc[0]["unseen"]

    def test_homogeneous_fold_passes(self, sample_regime_labels):
        """Fold with similar train/val distributions should pass."""
        idx = np.arange(500)
        folds = [(idx[:250], idx[250:500])]
        labels = sample_regime_labels.copy()  # Same distribution throughout
        cfg = {"max_concentration": 0.80, "kl_threshold": 1.0, "js_threshold": 0.5}
        result = regime_diversity_gate(folds, labels, cfg)
        # With high thresholds it should pass
        assert result.iloc[0]["pass"] == True
