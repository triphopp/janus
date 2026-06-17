"""Alpha Vantage equity loader — free cross-validation provider.

Free tier: 25 requests/day. No JS challenge. Uses stdlib urllib only.
API key: set env var ALPHA_VANTAGE_API_KEY or pass via cfg.

Endpoint used: TIME_SERIES_DAILY_ADJUSTED
  https://www.alphavantage.co/query?function=TIME_SERIES_DAILY_ADJUSTED
    &symbol=KO&outputsize=full&apikey=KEY
"""

from __future__ import annotations

import io
import json
import os
import urllib.request
from typing import Optional

import pandas as pd


_BASE_URL = "https://www.alphavantage.co/query"
_TIMEOUT = 20


def fetch(
    symbol: str,
    start,
    end,
    api_key: Optional[str] = None,
) -> pd.DataFrame:
    """Fetch daily adjusted closes from Alpha Vantage for one symbol.

    Returns DataFrame with [as_of_date, symbol, raw_close, provider].
    Returns empty DataFrame on error or missing API key — callers must handle.

    Args:
        symbol: ticker (e.g. 'KO')
        start: start date (str, date, or Timestamp) — used to filter after fetch
        end: end date
        api_key: AV key; falls back to env var ALPHA_VANTAGE_API_KEY
    """
    key = api_key or os.environ.get("ALPHA_VANTAGE_API_KEY", "")
    if not key:
        print("    alphavantage: no API key (set ALPHA_VANTAGE_API_KEY)")
        return pd.DataFrame()

    url = (
        f"{_BASE_URL}?function=TIME_SERIES_DAILY_ADJUSTED"
        f"&symbol={symbol}&outputsize=full&datatype=json&apikey={key}"
    )

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "janus-dataops/1.0"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"    alphavantage: network error — {exc}")
        return pd.DataFrame()

    ts = payload.get("Time Series (Daily)")
    if not ts:
        note = payload.get("Note") or payload.get("Information") or payload.get("Error Message")
        if note:
            print(f"    alphavantage: {note[:120]}")
        else:
            print(f"    alphavantage: unexpected response keys: {list(payload.keys())}")
        return pd.DataFrame()

    rows = []
    for date_str, vals in ts.items():
        close = vals.get("5. adjusted close") or vals.get("4. close")
        if close is None:
            continue
        rows.append({"as_of_date": pd.Timestamp(date_str), "raw_close": float(close)})

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).sort_values("as_of_date").reset_index(drop=True)

    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    df = df[(df["as_of_date"] >= start_ts) & (df["as_of_date"] <= end_ts)]

    df["symbol"] = symbol.upper()
    df["provider"] = "alphavantage"
    return df[["as_of_date", "symbol", "raw_close", "provider"]].reset_index(drop=True)
