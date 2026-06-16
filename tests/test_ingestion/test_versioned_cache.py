"""v1.4 tests: immutable raw versions and available_at PIT joins."""

import pandas as pd
import pytest

from ingestion.versioned_cache import VersionedCache, infer_available_at, pit_join


def test_infer_available_at_uses_config_lag():
    cfg = {"available_at_lag": {"settlement": "3h", "event": "P5D"}}
    ts = infer_available_at(pd.Timestamp("2024-09-25"), "settlement", cfg)
    assert ts == pd.Timestamp("2024-09-25T03:00:00Z")

    event_ts = infer_available_at(pd.Timestamp("2024-09-25"), "event", cfg)
    assert event_ts == pd.Timestamp("2024-09-30T00:00:00Z")


def test_equity_available_at_after_market_close():
    cfg = {
        "available_at_lag": {"equity_price": "3h"},
        "exchange_tz": "America/New_York",
        "market_close_time": "16:00",
    }

    ts = infer_available_at(pd.Timestamp("2024-01-02"), "equity_price", cfg)

    assert ts == pd.Timestamp("2024-01-03T00:00:00Z")


def test_pit_join_never_joins_future_event():
    signals = pd.DataFrame({
        "decision_time": pd.to_datetime(["2024-09-25T12:00:00Z", "2024-09-26T12:00:00Z"]),
        "signal": [1, 2],
    })
    events = pd.DataFrame({
        "available_at": pd.to_datetime(["2024-09-25T18:00:00Z"]),
        "event_value": [99],
    })

    out = pit_join(signals, events)
    assert pd.isna(out.loc[out["signal"] == 1, "event_value"]).iloc[0]
    assert out.loc[out["signal"] == 2, "event_value"].iloc[0] == 99


def test_versioned_cache_write_is_immutable(tmp_path):
    cache = VersionedCache(tmp_path)
    df = pd.DataFrame({
        "as_of_date": [pd.Timestamp("2024-09-25")],
        "available_at": [pd.Timestamp("2024-09-25T03:00:00Z")],
        "ingested_at": [pd.Timestamp("2024-09-26T00:00:00Z")],
        "price": [63.46],
    })

    cache.write("bz", df, ingested_at="2024-09-26", storage_format="csv")
    with pytest.raises(FileExistsError):
        cache.write("bz", df, ingested_at="2024-09-26", storage_format="csv")

    out = cache.read("bz", {
        "data_version": "latest",
        "data_storage_format": "csv",
        "versioned_cache": {"filename": "data.csv"},
    })
    assert out["price"].iloc[0] == 63.46
