"""Local data-source registry for imported external files.

The registry maps a ticker/symbol to one or more registered local files and
records the metadata an official run needs to trust them: absolute path,
SHA-256, detected format, row count, observed date range, provider label, and
import timestamp. One source per ticker is ``active``.

Storage is machine-local and public-safe by policy: the default registry path is
``configs/local/data_sources.yaml`` which is git-ignored because it stores
private absolute paths.

Dependency-light: stdlib + PyYAML only. Format/row/date scanning uses the stdlib
``csv`` module so importing this does not pull in pandas or the pipeline.
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

__all__ = [
    "DEFAULT_REGISTRY_PATH",
    "SourceRecord",
    "RegistryError",
    "sha256_file",
    "detect_format",
    "scan_file",
    "import_source",
    "list_sources",
    "get_active",
    "use_source",
]

DEFAULT_REGISTRY_PATH = Path("configs/local/data_sources.yaml")

_DELIMITERS = {"psv": "|", "tsv": "\t", "csv": ","}
# Candidate column names that hold the as-of/trade date in raw vendor files.
_DATE_HEADER_HINTS = ("trade date", "as_of_date", "date", "asofdate", "trade_date")


class RegistryError(ValueError):
    """Raised on registry import/list/use failures."""


@dataclass
class SourceRecord:
    source_id: str
    path: str
    sha256: str
    format: str
    provider: str
    rows: int
    date_range: list  # [start, end] or []
    imported_at: str

    def to_dict(self) -> dict:
        return asdict(self)


# ── file inspection ───────────────────────────────────────────────────────────

def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def detect_format(path: str | Path) -> str:
    """Sniff the delimiter from the header line. Returns psv|tsv|csv."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        header = fh.readline()
    if not header:
        raise RegistryError(f"file is empty: {path}")
    # Pick the delimiter that splits the header into the most fields.
    best_fmt, best_count = "csv", 1
    for fmt, delim in _DELIMITERS.items():
        count = header.count(delim)
        if count > best_count:
            best_fmt, best_count = fmt, count
    if best_count <= 0:
        raise RegistryError(
            f"could not detect a delimiter in header of {path}; "
            "expected a pipe, tab, or comma separated file"
        )
    return best_fmt


def _find_date_column(header: list[str]) -> int | None:
    lowered = [h.strip().lower() for h in header]
    for hint in _DATE_HEADER_HINTS:
        if hint in lowered:
            return lowered.index(hint)
    return None


_US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _norm_date(raw: str) -> str | None:
    raw = raw.strip()
    m = _ISO_DATE.match(raw)
    if m:
        return raw
    m = _US_DATE.match(raw)
    if m:
        mo, d, y = (int(g) for g in m.groups())
        try:
            return datetime(y, mo, d).date().isoformat()
        except ValueError:
            return None
    return None


def scan_file(path: str | Path) -> dict:
    """Return ``{format, rows, date_range}`` for a delimited file.

    Validates the file has a recognizable header (rejects obvious wrong inputs
    such as an empty file or a single unsplittable line). ``rows`` excludes the
    header. ``date_range`` is ``[min, max]`` ISO dates when a date column is
    found, else ``[]``.
    """
    path = Path(path)
    if not path.exists():
        raise RegistryError(f"file does not exist: {path}")
    fmt = detect_format(path)
    delim = _DELIMITERS[fmt]

    rows = 0
    dmin: str | None = None
    dmax: str | None = None
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh, delimiter=delim)
        try:
            header = next(reader)
        except StopIteration:
            raise RegistryError(f"file is empty: {path}")
        if len(header) < 2:
            raise RegistryError(
                f"header has only {len(header)} column(s); "
                f"file does not look like a {fmt} table: {path}"
            )
        date_idx = _find_date_column(header)
        for row in reader:
            if not row or all(not c.strip() for c in row):
                continue
            rows += 1
            if date_idx is not None and date_idx < len(row):
                iso = _norm_date(row[date_idx])
                if iso:
                    if dmin is None or iso < dmin:
                        dmin = iso
                    if dmax is None or iso > dmax:
                        dmax = iso

    date_range = [dmin, dmax] if dmin and dmax else []
    return {"format": fmt, "rows": rows, "date_range": date_range}


# ── registry I/O ──────────────────────────────────────────────────────────────

def _load(registry_path: Path) -> dict:
    if not registry_path.exists():
        return {}
    with open(registry_path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise RegistryError(f"registry is not a mapping: {registry_path}")
    return data


def _save(registry_path: Path, data: dict) -> None:
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    with open(registry_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=True, default_flow_style=False)


def _norm_ticker(ticker: str) -> str:
    t = ticker.strip().upper()
    if not t:
        raise RegistryError("ticker is required")
    return t


def _default_source_id(ticker: str, path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9]+", "_", path.stem).strip("_").lower()
    return f"{ticker.lower()}_{stem}" if stem else ticker.lower()


def import_source(
    ticker: str,
    file: str | Path,
    *,
    source_id: str | None = None,
    provider: str = "settlement",
    use: bool = True,
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
) -> SourceRecord:
    """Register an external local file for ``ticker`` and compute its SHA-256.

    Does not clean or mutate the raw data and does not run the pipeline.
    """
    ticker = _norm_ticker(ticker)
    registry_path = Path(registry_path)
    path = Path(file)
    if not path.exists():
        raise RegistryError(
            f"cannot import: file not found: {file}\n"
            f"  Check the path and try again:\n"
            f"    janus import {ticker} path/to/file.csv"
        )
    path = path.resolve()

    meta = scan_file(path)
    sid = source_id or _default_source_id(ticker, path)
    record = SourceRecord(
        source_id=sid,
        path=str(path),
        sha256=sha256_file(path),
        format=meta["format"],
        provider=provider,
        rows=meta["rows"],
        date_range=meta["date_range"],
        imported_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )

    data = _load(registry_path)
    entry = data.setdefault(ticker, {"active": None, "sources": {}})
    entry.setdefault("sources", {})[sid] = record.to_dict()
    if use or entry.get("active") is None:
        entry["active"] = sid
    _save(registry_path, data)
    return record


def list_sources(
    ticker: str,
    *,
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
) -> dict:
    """Return ``{active, sources: {sid: record}}`` for one ticker (empty if none)."""
    ticker = _norm_ticker(ticker)
    data = _load(Path(registry_path))
    entry = data.get(ticker)
    if not entry:
        return {"active": None, "sources": {}}
    return {"active": entry.get("active"), "sources": entry.get("sources", {})}


def get_active(
    ticker: str,
    *,
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
) -> SourceRecord | None:
    """Return the active SourceRecord for ``ticker``, or None if unregistered."""
    info = list_sources(ticker, registry_path=registry_path)
    active = info.get("active")
    if not active:
        return None
    rec = info["sources"].get(active)
    if not rec:
        return None
    return SourceRecord(**rec)


def use_source(
    ticker: str,
    source_id: str,
    *,
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
) -> SourceRecord:
    """Set the active source for ``ticker``."""
    ticker = _norm_ticker(ticker)
    registry_path = Path(registry_path)
    data = _load(registry_path)
    entry = data.get(ticker)
    if not entry or source_id not in entry.get("sources", {}):
        known = ", ".join(sorted((entry or {}).get("sources", {}))) or "(none)"
        raise RegistryError(
            f"no registered source {source_id!r} for {ticker}. Known sources: {known}"
        )
    entry["active"] = source_id
    _save(registry_path, data)
    return SourceRecord(**entry["sources"][source_id])
