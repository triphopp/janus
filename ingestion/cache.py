"""Parquet cache + incremental update + PIT roll.

Reads cached data, computes missing date ranges, fetches only gaps.
"""

from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple

import pandas as pd

DEFAULT_CACHE_DIR = Path("outputs/clean_data")


class Cache:
    """Parquet-based cache with incremental update support."""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = Path(cache_dir or DEFAULT_CACHE_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str) -> Path:
        safe = symbol.replace("/", "_").replace("\\", "_")
        return self.cache_dir / f"{safe}.parquet"

    def read(self, symbol: str, start, end) -> Optional[pd.DataFrame]:
        """Read cached data for symbol. Returns None if no cache exists."""
        p = self._path(symbol)
        if not p.exists():
            return None
        df = pd.read_parquet(p)
        # Filter to requested range
        if "as_of_date" in df.columns:
            df = df[(df["as_of_date"] >= pd.Timestamp(start)) & (df["as_of_date"] <= pd.Timestamp(end))]
        return df if len(df) > 0 else None

    def write(self, symbol: str, df: pd.DataFrame):
        """Write (or update) cache for symbol. Merges with existing if present."""
        p = self._path(symbol)
        if p.exists():
            existing = pd.read_parquet(p)
            # Upsert: remove overlapping dates, then concat
            if "as_of_date" in existing.columns and "as_of_date" in df.columns:
                existing = existing[~existing["as_of_date"].isin(df["as_of_date"])]
            df = pd.concat([existing, df], ignore_index=True)
        df.sort_values(["as_of_date", "product_id"], inplace=True)
        df.to_parquet(p, index=False)

    def is_complete(self, cached: Optional[pd.DataFrame], start, end) -> bool:
        """Check if cached data covers the full requested range."""
        if cached is None or cached.empty:
            return False
        if "as_of_date" not in cached.columns:
            return True  # can't verify, assume complete
        dates = pd.to_datetime(cached["as_of_date"])
        return dates.min() <= pd.Timestamp(start) and dates.max() >= pd.Timestamp(end)

    def missing_ranges(self, cached: Optional[pd.DataFrame], start, end) -> Tuple:
        """Return (start, end) for missing data. Returns full range if no cache."""
        if cached is None or cached.empty:
            return (pd.Timestamp(start), pd.Timestamp(end))
        if "as_of_date" not in cached.columns:
            return (pd.Timestamp(start), pd.Timestamp(end))
        dates = pd.to_datetime(cached["as_of_date"])
        cache_start, cache_end = dates.min(), dates.max()
        # Simple: return the gap. For production, compute exact missing sub-ranges.
        need_start = max(pd.Timestamp(start), cache_start)
        need_end = max(pd.Timestamp(end), cache_end)
        if need_start >= need_end:
            # Cache covers everything
            need_end = pd.Timestamp(end)
            if cache_end < pd.Timestamp(end):
                need_start = cache_end
        return (need_start, need_end)


# Module-level singleton
_cache_instance: Optional[Cache] = None


def get_cache() -> Cache:
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = Cache()
    return _cache_instance
