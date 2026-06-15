"""Immutable raw data cache with data-version reads and PIT joins.

v1.4 additions:
- never overwrite raw provider data; write a new ingested_at partition
- log every write to raw/_versions.jsonl for reproducibility
- use available_at, not as_of_date, when joining external data to signals
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

DEFAULT_RAW_DIR = Path("raw")


def _safe_symbol(symbol: str) -> str:
    return str(symbol).replace("/", "_").replace("\\", "_").lower()


def _utc_now() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(timezone.utc))


def _partition_key(value=None) -> str:
    ts = _utc_now() if value is None else pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC")
    return ts.date().isoformat()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _schema_hash(df: pd.DataFrame) -> str:
    cols = sorted((c, str(df[c].dtype)) for c in df.columns)
    return _hash_text(json.dumps(cols, sort_keys=True))


def _data_hash(df: pd.DataFrame) -> str:
    return _hash_text(df.to_csv(index=False))


def _parse_lag(value) -> pd.Timedelta:
    """Parse config lag strings such as '3h', '2D', 'P5D', or Timedelta."""
    if isinstance(value, pd.Timedelta):
        return value
    if value is None:
        return pd.Timedelta(0)
    text = str(value).strip()
    if text.startswith("P") and text.endswith("D"):
        return pd.Timedelta(days=int(text[1:-1]))
    if text.startswith("PT") and text.endswith("H"):
        return pd.Timedelta(hours=int(text[2:-1]))
    return pd.Timedelta(text)


def infer_available_at(as_of_date, data_type: str, cfg: dict):
    """Infer when data was actually knowable using cfg['available_at_lag'].

    as_of_date says what period the data describes. available_at says when a
    strategy was allowed to know it. All external joins must use available_at.
    """
    lag_map = cfg.get("available_at_lag", {})
    lag = _parse_lag(lag_map.get(data_type, cfg.get("default_available_at_lag", "0h")))
    values = pd.to_datetime(as_of_date) + lag
    return pd.to_datetime(values, utc=True)


def add_availability_columns(
    df: pd.DataFrame,
    data_type: str,
    cfg: dict,
    ingested_at=None,
) -> pd.DataFrame:
    """Add available_at and ingested_at when a provider does not supply them."""
    out = df.copy()
    if "available_at" not in out.columns:
        out["available_at"] = infer_available_at(out["as_of_date"], data_type, cfg)
    else:
        out["available_at"] = pd.to_datetime(out["available_at"], utc=True)

    if "ingested_at" not in out.columns:
        ts = _utc_now() if ingested_at is None else pd.Timestamp(ingested_at)
        out["ingested_at"] = pd.to_datetime(ts, utc=True)
    else:
        out["ingested_at"] = pd.to_datetime(out["ingested_at"], utc=True)
    return out


def pit_join(
    signals: pd.DataFrame,
    events: pd.DataFrame,
    decision_col: str = "decision_time",
    event_time_col: str = "available_at",
    by: Optional[str | list[str]] = None,
    suffixes: tuple[str, str] = ("", "_event"),
) -> pd.DataFrame:
    """Point-in-time merge: only events available before decision time join.

    This is intentionally a backward merge_asof. Forward joins would leak data.
    """
    if decision_col not in signals.columns:
        raise ValueError(f"signals missing decision column: {decision_col}")
    if event_time_col not in events.columns:
        raise ValueError(f"events missing available-time column: {event_time_col}")

    left = signals.copy()
    right = events.copy()
    left[decision_col] = pd.to_datetime(left[decision_col], utc=True)
    right[event_time_col] = pd.to_datetime(right[event_time_col], utc=True)

    sort_left = [decision_col]
    sort_right = [event_time_col]
    if by is not None:
        keys = [by] if isinstance(by, str) else list(by)
        sort_left = keys + sort_left
        sort_right = keys + sort_right

    right = right[right[event_time_col] <= left[decision_col].max()]
    return pd.merge_asof(
        left.sort_values(sort_left),
        right.sort_values(sort_right),
        left_on=decision_col,
        right_on=event_time_col,
        by=by,
        direction="backward",
        suffixes=suffixes,
    )


class VersionedCache:
    """Hive-style immutable raw data cache.

    Layout:
        raw/<symbol>/ingested_at=YYYY-MM-DD/data.parquet
        raw/_versions.jsonl
    """

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root or DEFAULT_RAW_DIR)
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def manifest_path(self) -> Path:
        return self.root / "_versions.jsonl"

    def _partition_dir(self, symbol: str, version: str) -> Path:
        return self.root / _safe_symbol(symbol) / f"ingested_at={version}"

    def _data_path(self, symbol: str, version: str, filename: str) -> Path:
        return self._partition_dir(symbol, version) / filename

    def list_versions(self, symbol: str) -> list[str]:
        base = self.root / _safe_symbol(symbol)
        if not base.exists():
            return []
        versions = []
        for path in base.iterdir():
            if path.is_dir() and path.name.startswith("ingested_at="):
                versions.append(path.name.split("=", 1)[1])
        return sorted(versions)

    def latest_partition(self, symbol: str) -> str:
        versions = self.list_versions(symbol)
        if not versions:
            raise FileNotFoundError(f"no raw versions found for {symbol}")
        return versions[-1]

    def partition_at(self, symbol: str, as_of) -> str:
        target = _partition_key(as_of)
        candidates = [v for v in self.list_versions(symbol) if v <= target]
        if not candidates:
            raise FileNotFoundError(f"no raw version for {symbol} at or before {target}")
        return candidates[-1]

    def _append_manifest(self, symbol: str, version: str, df: pd.DataFrame, path: Path, run_id=None):
        record = {
            "symbol": _safe_symbol(symbol),
            "ingested_at": version,
            "written_at": _utc_now().isoformat(),
            "rows": int(len(df)),
            "schema_hash": _schema_hash(df),
            "data_hash": _data_hash(df),
            "path": str(path).replace("\\", "/"),
            "run_id": run_id,
        }
        with open(self.manifest_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    def _write_frame(self, df: pd.DataFrame, path: Path, storage_format: str):
        if storage_format == "parquet":
            try:
                df.to_parquet(path, index=False)
            except ImportError as exc:
                raise RuntimeError(
                    "Parquet output requires pyarrow or fastparquet. Install one, "
                    "or set storage_format='csv'/'pickle' for local tests."
                ) from exc
        elif storage_format == "csv":
            df.to_csv(path, index=False)
        elif storage_format == "pickle":
            df.to_pickle(path)
        else:
            raise ValueError(f"unknown storage format: {storage_format}")

    def _read_frame(self, path: Path, storage_format: str) -> pd.DataFrame:
        if storage_format == "parquet":
            try:
                return pd.read_parquet(path)
            except ImportError as exc:
                raise RuntimeError(
                    "Parquet input requires pyarrow or fastparquet. Install one, "
                    "or use a cache written with storage_format='csv'/'pickle'."
                ) from exc
        if storage_format == "csv":
            return pd.read_csv(path, parse_dates=["as_of_date", "available_at", "ingested_at"])
        if storage_format == "pickle":
            return pd.read_pickle(path)
        raise ValueError(f"unknown storage format: {storage_format}")

    def write(
        self,
        symbol: str,
        df: pd.DataFrame,
        ingested_at=None,
        run_id: Optional[str] = None,
        filename: Optional[str] = None,
        storage_format: str = "parquet",
    ) -> dict:
        """Write one immutable partition. Existing target file is an error."""
        version = _partition_key(ingested_at)
        suffix = {"parquet": "parquet", "csv": "csv", "pickle": "pkl"}[storage_format]
        name = filename or f"data.{suffix}"
        path = self._data_path(symbol, version, name)
        if path.exists():
            raise FileExistsError(f"partition target exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_frame(df, path, storage_format)
        return self._append_manifest(symbol, version, df, path, run_id)

    def read(self, symbol: str, cfg: dict) -> pd.DataFrame:
        """Read a specific raw data version.

        cfg['data_version'] supports:
        - 'latest'
        - explicit 'YYYY-MM-DD'
        - 'as_of_backtest_start'
        """
        version = cfg.get("data_version", "latest")
        if version == "latest":
            partition = self.latest_partition(symbol)
        elif version == "as_of_backtest_start":
            partition = self.partition_at(symbol, cfg["backtest_start"])
        else:
            partition = str(version)

        storage_format = cfg.get("data_storage_format", "parquet")
        suffix = {"parquet": "parquet", "csv": "csv", "pickle": "pkl"}[storage_format]
        filename = cfg.get("versioned_cache", {}).get("filename", f"data.{suffix}")
        path = self._data_path(symbol, partition, filename)
        if not path.exists():
            raise FileNotFoundError(f"raw version file not found: {path}")
        return self._read_frame(path, storage_format)


_cache_instance: Optional[VersionedCache] = None


def get_versioned_cache() -> VersionedCache:
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = VersionedCache()
    return _cache_instance
