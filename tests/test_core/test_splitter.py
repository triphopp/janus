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
