"""Stooq equity loader — NOTE: Stooq.com now serves a Cloudflare JS challenge
that blocks plain urllib/requests fetches. This module is kept for reference but
fetch() will return an empty DataFrame in most environments.

Use market_loader.py (yfinance SPY proxy) for cross-provider validation instead.
"""

from __future__ import annotations

import pandas as pd


def fetch(symbol: str, start, end) -> pd.DataFrame:  # noqa: ARG001
    """Stooq is blocked by Cloudflare JS challenge — returns empty DataFrame.

    Callers fall through to market_loader automatically when this returns empty.
    """
    return pd.DataFrame()
