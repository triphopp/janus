"""Immutable raw data cache with data-version reads and PIT joins.

v1.4 additions:
- never overwrite raw provider data; write a new ingested_at partition
- log every write to raw/_versions.jsonl for reproducibility
- use available_at, not as_of_date, when joining external data to signals
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

DEFAULT_RAW_DIR = Path("raw")


def _writer_identity() -> dict:
    """Who/where wrote this partition — needed for tamper-evidence (§13.3)."""
    try:
        user = getpass.getuser()
    except Exception:
        user = None
    return {"host": socket.gethostname(), "pid": os.getpid(), "user": user}


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
    """Canonical, value-based content hash (not to_csv text — fixes float-repr drift).

    Delegates to ``core.audit.canonical_frame_hash``: columns sorted, floats rounded to
    8 dp, hashed via ``hash_pandas_object``. Required for I1 hash-chain + I6 replay.
    """
    from core.audit import canonical_frame_hash

    return canonical_frame_hash(df)


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
    asof = pd.to_datetime(as_of_date)

    if data_type == "equity_price":
        market_close = str(cfg.get("market_close_time", "16:00"))
        exchange_tz = cfg.get("exchange_tz", "America/New_York")
        return _anchor_to_session(asof, market_close, exchange_tz, lag, as_of_date)

    if data_type == "settlement":
        # Settlement availability MUST anchor to the exchange settlement-release /
        # session-close time, never midnight of as_of_date (issue 022). A US EOD
        # settlement for date t is not knowable before the local session for t
        # closes; anchoring to midnight + a few hours made it appear knowable on
        # the prior evening, causing a one-day point-in-time leak.
        release_time = str(
            cfg.get("settlement_release_time", cfg.get("market_close_time", "16:30"))
        )
        exchange_tz = cfg.get("exchange_tz", "America/New_York")
        return _anchor_to_session(asof, release_time, exchange_tz, lag, as_of_date)

    values = asof + lag
    return pd.to_datetime(values, utc=True)


def _anchor_to_session(asof, local_time: str, exchange_tz: str, lag, as_of_date):
    """Anchor as_of_date to a local session time in exchange_tz, return UTC.

    Shared by equity-close and settlement-release availability. Adding ``lag`` to a
    timezone-aware local instant means DST and the UTC date boundary are handled by
    the conversion, not by hand.
    """
    hour, minute = [int(part) for part in str(local_time).split(":", 1)]
    dates = pd.Series(asof).dt.normalize()
    local_anchor = dates + pd.Timedelta(hours=hour, minutes=minute)
    if local_anchor.dt.tz is None:
        # Resolve DST edge cases deterministically: a release time that lands in a
        # spring-forward gap shifts forward; a fall-back overlap takes the earlier
        # (DST) instant. Without this, tz_localize raises on transition dates.
        local_anchor = local_anchor.dt.tz_localize(
            exchange_tz, ambiguous=True, nonexistent="shift_forward"
        )
    else:
        local_anchor = local_anchor.dt.tz_convert(exchange_tz)
    values = local_anchor + lag

    if isinstance(as_of_date, (pd.Series, pd.Index, list, tuple)):
        return pd.to_datetime(values, utc=True)
    return pd.to_datetime(values.iloc[0], utc=True)


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

    def _last_chain_hash(self, symbol: str) -> Optional[str]:
        """Last chain_hash recorded for this symbol — links the tamper-evident chain."""
        if not self.manifest_path.exists():
            return None
        target = _safe_symbol(symbol)
        last = None
        with open(self.manifest_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("symbol") == target:
                    last = rec.get("chain_hash")
        return last

    def _append_manifest(self, symbol: str, version: str, df: pd.DataFrame, path: Path, run_id=None):
        data_hash = _data_hash(df)
        schema_hash = _schema_hash(df)
        prev_hash = self._last_chain_hash(symbol)
        # chain_hash links prev → this, making history rewrites detectable (§13.3).
        chain_hash = _hash_text(f"{prev_hash or ''}|{schema_hash}|{data_hash}")
        record = {
            "symbol": _safe_symbol(symbol),
            "ingested_at": version,
            "written_at": _utc_now().isoformat(),
            "rows": int(len(df)),
            "schema_hash": schema_hash,
            "data_hash": data_hash,
            "prev_hash": prev_hash,
            "chain_hash": chain_hash,
            "path": str(path).replace("\\", "/"),
            "run_id": run_id,
            "writer": _writer_identity(),
        }
        with open(self.manifest_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    def _write_frame(self, df: pd.DataFrame, path: Path, storage_format: str):
        """ACID write: serialize to a temp file, then atomic-rename into place (§13.4).

        Guarantees a reader never sees a half-written partition; a crash mid-write
        leaves only an orphan ``.tmp`` (ignored by readers), never a partial ``path``.
        """
        tmp = path.with_name(path.name + ".tmp")
        try:
            if storage_format == "parquet":
                try:
                    df.to_parquet(tmp, index=False)
                except ImportError as exc:
                    raise RuntimeError(
                        "Parquet output requires pyarrow or fastparquet. Install one, "
                        "or set storage_format='csv'/'pickle' for local tests."
                    ) from exc
            elif storage_format == "csv":
                df.to_csv(tmp, index=False)
            elif storage_format == "pickle":
                df.to_pickle(tmp)
            else:
                raise ValueError(f"unknown storage format: {storage_format}")
            os.replace(tmp, path)  # atomic on the same filesystem
        finally:
            if tmp.exists():
                tmp.unlink()

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

    def manifest_record(self, symbol: str, version: str) -> Optional[dict]:
        """Latest manifest record for (symbol, ingested_at=version), or None."""
        if not self.manifest_path.exists():
            return None
        target = _safe_symbol(symbol)
        found = None
        with open(self.manifest_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("symbol") == target and rec.get("ingested_at") == version:
                    found = rec
        return found

    def verify_partition(self, symbol: str, version: str, storage_format: str = "parquet",
                         filename: Optional[str] = None) -> bool:
        """Recompute the partition's data hash and compare to its manifest record.

        Returns True only when a manifest entry exists AND the recomputed canonical
        hash matches (§13.4: readers ignore absent/invalid partitions). Reliable for
        dtype-preserving formats (parquet/pickle); CSV round-trips dtypes so skip it.
        """
        rec = self.manifest_record(symbol, version)
        if rec is None:
            return False
        if storage_format == "csv":
            return True  # csv loses dtypes; manifest write-time hash stays authoritative
        suffix = {"parquet": "parquet", "csv": "csv", "pickle": "pkl"}[storage_format]
        name = filename or f"data.{suffix}"
        path = self._data_path(symbol, version, name)
        if not path.exists():
            return False
        return _data_hash(self._read_frame(path, storage_format)) == rec.get("data_hash")

    def read(self, symbol: str, cfg: dict) -> pd.DataFrame:
        """Read a specific raw data version (bitemporal).

        cfg['data_version'] supports:
        - 'latest'
        - explicit 'YYYY-MM-DD' (exact knowledge partition)
        - 'as_of_backtest_start' (latest partition with knowledge_time <= backtest_start)
        - 'as_of_knowledge' (latest partition with knowledge_time <= cfg['knowledge_time'])

        Time-travel: 'as_of_*' answers "what did we KNOW as of T" — immune to later
        vendor restatements (an original value, not the corrected future one).
        Set cfg['verify_on_read']=True to assert the partition hash matches its manifest.
        """
        version = cfg.get("data_version", "latest")
        if version == "latest":
            partition = self.latest_partition(symbol)
        elif version == "as_of_backtest_start":
            partition = self.partition_at(symbol, cfg["backtest_start"])
        elif version == "as_of_knowledge":
            partition = self.partition_at(symbol, cfg["knowledge_time"])
        else:
            partition = str(version)

        storage_format = cfg.get("data_storage_format", "parquet")
        suffix = {"parquet": "parquet", "csv": "csv", "pickle": "pkl"}[storage_format]
        filename = cfg.get("versioned_cache", {}).get("filename", f"data.{suffix}")
        path = self._data_path(symbol, partition, filename)
        if not path.exists():
            raise FileNotFoundError(f"raw version file not found: {path}")
        if cfg.get("verify_on_read") and not self.verify_partition(
            symbol, partition, storage_format, filename
        ):
            raise RuntimeError(
                f"partition integrity check failed for {symbol} @ {partition} "
                "(missing manifest entry or hash mismatch)"
            )
        return self._read_frame(path, storage_format)


_cache_instance: Optional[VersionedCache] = None


def get_versioned_cache() -> VersionedCache:
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = VersionedCache()
    return _cache_instance
