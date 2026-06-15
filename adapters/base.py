"""AdapterBase — contract for all asset-specific adapters.

Every adapter implements prepare(raw_df) → (df, cfg).
Core consumes df + cfg without knowing the asset class.
"""

from abc import ABC, abstractmethod
from typing import Tuple

import pandas as pd


class AdapterBase(ABC):
    """Abstract base for asset adapters.

    prepare() transforms raw data from ingestion layer into
    standardized (df, cfg) that core modules consume.
    """

    def __init__(self, cfg: dict):
        """
        Args:
            cfg: instrument-level config (loaded from configs/instruments/*.yaml)
        """
        self.cfg = cfg

    @abstractmethod
    def prepare(self, raw_df: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
        """Transform raw_df → (df, cfg) ready for core pipeline.

        Responsibilities:
        - Column name normalization (provider-specific → standard)
        - Asset-specific data cleaning (corp actions, rolls, etc.)
        - Compute derived columns (returns, realized vol, etc.)
        - Build cfg dict that core uses to find columns

        Args:
            raw_df: DataFrame from ingestion layer (RAW_SCHEMA-compliant)

        Returns:
            (df, cfg): standardized DataFrame + config for core pipeline
        """
        ...
