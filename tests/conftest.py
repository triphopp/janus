"""Shared fixtures for all test modules."""

import pytest
import pandas as pd
import numpy as np
from pathlib import Path
import yaml
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ingestion.symbology import Symbology


# ── Real Brent row fixture ──
REAL_BRENT_ROW = (
    "9/25/2024|North Sea|Brent Crude Futures|11/1/2024|B|C|10.0000|"
    "63.46000|-1.71000|9/25/2024|254|0.01000|0.00000"
)


@pytest.fixture
def brent_row():
    """Single parsed Brent option row."""
    from ingestion.settlement_loader import parse_pipe_row
    return parse_pipe_row(REAL_BRENT_ROW)


@pytest.fixture
def sample_raw_df():
    """Small synthetic DataFrame in RAW_SCHEMA format."""
    dates = pd.date_range("2024-01-01", periods=100, freq="B")
    available_at = pd.to_datetime(dates + pd.Timedelta(hours=3), utc=True)
    ingested_at = pd.Timestamp("2024-06-01T00:00:00Z")
    np.random.seed(42)
    return pd.DataFrame({
        "as_of_date":      dates,
        "available_at":    available_at,
        "ingested_at":     ingested_at,
        "product_id":      254,
        "contract_root":   "B",
        "hub":             "North Sea",
        "instrument_type": "future",
        "right":           None,
        "strike":          np.nan,
        "delivery_month":  dates + pd.DateOffset(months=1),
        "expiry":          dates + pd.DateOffset(months=1),
        "price":           80 + np.cumsum(np.random.randn(100) * 0.5),
        "net_change":      np.random.randn(100) * 0.3,
        "iv_provided":     np.nan,
        "delta_provided":  np.nan,
        "provider":        "test",
        "timestamp":       None,
    })


@pytest.fixture
def sample_option_df():
    """Synthetic option data for pricing/Greeks tests."""
    np.random.seed(123)
    n = 50
    return pd.DataFrame({
        "as_of_date": pd.date_range("2024-06-01", periods=n, freq="B"),
        "product_id": 254,
        "strike":     np.linspace(60, 100, n),
        "expiry":     pd.Timestamp("2024-12-31"),
        "price":      np.random.uniform(2, 15, n),
        "F":          80.0,
        "T":          0.5,
        "r":          0.05,
        "right":      np.where(np.random.rand(n) > 0.5, "C", "P"),
        "iv_provided": np.random.uniform(0.2, 0.5, n),
    })


@pytest.fixture
def sample_returns():
    """Synthetic return series for metrics tests."""
    np.random.seed(99)
    return pd.Series(np.random.randn(500) * 0.01 + 0.0005,
                     index=pd.date_range("2020-01-01", periods=500, freq="B"))


@pytest.fixture
def symbology():
    """Load symbology from config."""
    return Symbology()


@pytest.fixture
def bz_config():
    """Load Brent instrument config."""
    path = Path("configs/instruments/bz.yaml")
    with open(path) as f:
        return yaml.safe_load(f)


@pytest.fixture
def sample_regime_labels():
    """Synthetic regime labels."""
    np.random.seed(7)
    regimes = ["low_vol", "med_vol", "high_vol"]
    return pd.Series(
        np.random.choice(regimes, 500),
        index=pd.date_range("2020-01-01", periods=500, freq="B"),
    )
