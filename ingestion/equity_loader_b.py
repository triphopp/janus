"""Equity provider B loader (POC: Massive / test purpose).

Cross-checks provider A — validates adjustment conventions match before merging.
Differences between providers signal calculation errors in one of them.
"""

import pandas as pd

from .base import ProviderBase


class EquityLoaderB(ProviderBase):
    """Equity provider B — cross-check provider for data quality.

    Key responsibility: detect adjustment convention mismatches.
    If A and B disagree on adj_factor by > tolerance, flag for investigation.
    """

    def __init__(self, tolerance: float = 0.001):
        """
        Args:
            tolerance: max allowed difference in adj_factor between providers
        """
        self.tolerance = tolerance

    def fetch(self, symbol: str, start, end) -> pd.DataFrame:
        """Fetch from provider B → standardized equity DataFrame.

        In production, this would connect to a different data vendor.
        For POC, returns placeholder structure — swap implementation at ingestion layer.
        """
        # Placeholder — replace with actual provider B implementation
        # Structure matches EQUITY_RAW_SCHEMA
        return pd.DataFrame(columns=[
            "as_of_date", "available_at", "ingested_at", "symbol",
            "raw_close", "adj_factor", "volume", "is_delisted", "provider"
        ])

    def cross_check(self, df_a: pd.DataFrame, df_b: pd.DataFrame) -> pd.DataFrame:
        """Compare provider A vs B adjustment conventions.

        Returns DataFrame with columns: as_of_date, symbol, adj_diff, flag
        where flag=True means difference exceeds tolerance.
        """
        if df_a.empty or df_b.empty:
            return pd.DataFrame()

        merged = df_a.merge(
            df_b, on=["as_of_date", "symbol"],
            suffixes=("_a", "_b"), how="inner"
        )
        merged["adj_diff"] = (merged["adj_factor_a"] - merged["adj_factor_b"]).abs()
        merged["flag"] = merged["adj_diff"] > self.tolerance
        return merged[["as_of_date", "symbol", "adj_diff", "flag"]]

    def list_expired(self, root: str, asof) -> list:
        return []
