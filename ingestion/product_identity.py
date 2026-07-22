"""Provider-aware product identity resolution for settlement rows.

This module sits between raw provider columns and adapter selection. It keeps
row instrument type, product family, underlying type, and exercise style as
data attributes instead of letting pricing models or C/P-shaped rows infer them.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


DEFAULT_PRODUCT_IDENTITY_PATH = Path("configs/symbology/product_identity.yaml")

IDENTITY_COLUMNS = (
    "source_product_id",
    "source_product_name",
    "source_contract",
    "source_contract_type",
    "source_option_root",
    "underlying_root",
    "product_family",
    "option_underlying_type",
    "exercise_style",
    "settlement_type",
    "source_product_identity",
    "product_identity_status",
    "product_identity_confidence",
    "product_identity_reason",
    "product_identity_evidence_ref",
    "equivalent_option_root_cme",
    "option_right",
)


@dataclass(frozen=True)
class ProductIdentityRecord:
    provider: str
    source_product_id: int
    hub: str
    source_product_name: str
    source_contract: str
    product_family: str
    underlying_root: str
    source_option_root: str | None
    option_underlying_type: str | None
    exercise_style: str | None
    settlement_type: str | None
    source_product_identity: str | None
    identity_confidence: str
    evidence_ref: str | None
    equivalent_option_roots: dict[str, str]

    @property
    def key(self) -> tuple[str, int, str, str, str]:
        return (
            _norm(self.provider),
            int(self.source_product_id),
            _norm(self.hub),
            _norm(self.source_product_name),
            _norm(self.source_contract),
        )


class ProductIdentityMaster:
    """Load and validate provider-aware product identity mappings."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or DEFAULT_PRODUCT_IDENTITY_PATH)
        self.schema_version: int | None = None
        self.records: tuple[ProductIdentityRecord, ...] = ()
        self.evidence_warnings: list[str] = []
        self._by_key: dict[tuple[str, int, str, str, str], ProductIdentityRecord] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(f"Product identity master not found: {self.path}")
        with open(self.path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        self.schema_version = data.get("schema_version")
        rows = []
        seen: dict[tuple[str, int, str, str, str], ProductIdentityRecord] = {}
        warnings: list[str] = []
        for entry in data.get("products", []):
            rec = ProductIdentityRecord(
                provider=str(entry["provider"]),
                source_product_id=int(entry["source_product_id"]),
                hub=str(entry.get("hub", "")),
                source_product_name=str(entry.get("source_product_name", "")),
                source_contract=str(entry.get("source_contract", "")),
                product_family=str(entry["product_family"]),
                underlying_root=str(entry.get("underlying_root") or ""),
                source_option_root=entry.get("source_option_root"),
                option_underlying_type=entry.get("option_underlying_type"),
                exercise_style=entry.get("exercise_style"),
                settlement_type=entry.get("settlement_type"),
                source_product_identity=entry.get("source_product_identity"),
                identity_confidence=str(entry.get("identity_confidence", "configured")),
                evidence_ref=entry.get("evidence_ref"),
                equivalent_option_roots=dict(entry.get("equivalent_option_roots") or {}),
            )
            if rec.key in seen:
                raise ValueError(
                    "Duplicate/conflicting product identity mapping for "
                    f"{rec.key}"
                )
            seen[rec.key] = rec
            rows.append(rec)
            if rec.evidence_ref and not Path(rec.evidence_ref).exists():
                warnings.append(f"missing evidence_ref: {rec.evidence_ref}")
        self.records = tuple(rows)
        self._by_key = seen
        self.evidence_warnings = warnings

    def resolve(
        self,
        *,
        provider: str,
        source_product_id: Any,
        hub: Any,
        source_product_name: Any,
        source_contract: Any,
    ) -> ProductIdentityRecord | None:
        if pd.isna(source_product_id):
            return None
        try:
            pid = int(source_product_id)
        except (TypeError, ValueError):
            return None
        key = (
            _norm(provider),
            pid,
            _norm(hub),
            _norm(source_product_name),
            _norm(source_contract),
        )
        return self._by_key.get(key)

    @property
    def mapping_hash(self) -> str:
        blob = self.path.read_bytes()
        return hashlib.sha256(blob).hexdigest()


class ProductIdentityResolver:
    """Resolve row-level product identity for provider settlement data."""

    def __init__(self, master: ProductIdentityMaster | None = None):
        self.master = master or ProductIdentityMaster()

    @classmethod
    def from_config(cls, cfg: dict | None = None) -> "ProductIdentityResolver":
        cfg = cfg or {}
        path = (
            cfg.get("product_identity_path")
            or (cfg.get("product_identity") or {}).get("path")
            or DEFAULT_PRODUCT_IDENTITY_PATH
        )
        return cls(ProductIdentityMaster(path))

    def resolve_frame(
        self,
        df: pd.DataFrame,
        cfg: dict | None = None,
        *,
        provider: str | None = None,
    ) -> pd.DataFrame:
        cfg = cfg or {}
        provider_key = provider or cfg.get("product_identity_provider")
        if provider_key is None:
            cfg_provider = str(cfg.get("provider", "settlement")).strip().lower()
            provider_key = "ice_settlement_file" if cfg_provider == "settlement" else cfg_provider

        out = df.copy()
        for col in IDENTITY_COLUMNS:
            if col not in out.columns:
                out[col] = pd.NA

        source_product_id = _first_series(out, ("PRODUCT_ID", "product_id"))
        source_product_name = _first_series(out, ("PRODUCT", "product_name"))
        source_contract = _first_series(out, ("CONTRACT", "contract_root"))
        source_contract_type = _first_series(out, ("CONTRACT TYPE", "right_raw"))
        strike = _first_series(out, ("STRIKE", "strike"))
        hub = _first_series(out, ("HUB", "hub"))

        out["source_product_id"] = source_product_id
        out["source_product_name"] = source_product_name
        out["source_contract"] = source_contract
        out["source_contract_type"] = source_contract_type

        for idx in out.index:
            cls = classify_instrument(source_contract_type.loc[idx], strike.loc[idx])
            out.at[idx, "instrument_type"] = cls["instrument_type"]
            out.at[idx, "right"] = cls["right"]
            out.at[idx, "option_right"] = cls["option_right"]

            rec = self.master.resolve(
                provider=provider_key,
                source_product_id=source_product_id.loc[idx],
                hub=hub.loc[idx],
                source_product_name=source_product_name.loc[idx],
                source_contract=source_contract.loc[idx],
            )
            if rec is None:
                out.at[idx, "product_identity_status"] = (
                    "conflict" if cls["status"] == "conflict" else "unknown"
                )
                out.at[idx, "product_identity_confidence"] = "none"
                out.at[idx, "product_identity_reason"] = (
                    cls["reason"]
                    if cls["status"] != "resolved"
                    else "product_master_no_match"
                )
                continue

            out.at[idx, "source_option_root"] = rec.source_option_root
            out.at[idx, "underlying_root"] = rec.underlying_root
            out.at[idx, "product_family"] = rec.product_family
            out.at[idx, "option_underlying_type"] = rec.option_underlying_type
            out.at[idx, "exercise_style"] = rec.exercise_style
            out.at[idx, "settlement_type"] = rec.settlement_type
            out.at[idx, "source_product_identity"] = rec.source_product_identity
            out.at[idx, "product_identity_confidence"] = rec.identity_confidence
            out.at[idx, "product_identity_evidence_ref"] = rec.evidence_ref
            out.at[idx, "equivalent_option_root_cme"] = rec.equivalent_option_roots.get("cme")
            out.at[idx, "product_identity_status"] = cls["status"]
            out.at[idx, "product_identity_reason"] = (
                "resolved_from_product_master"
                if cls["status"] == "resolved"
                else cls["reason"]
            )

        return out


def classify_instrument(contract_type: Any, strike: Any) -> dict[str, Any]:
    """Classify a settlement row from CONTRACT TYPE and strike shape."""
    raw = "" if pd.isna(contract_type) else str(contract_type).strip().upper()
    has_strike = pd.notna(strike)

    if raw in {"C", "P"}:
        if has_strike:
            return {
                "instrument_type": "option",
                "right": raw,
                "option_right": "call" if raw == "C" else "put",
                "status": "resolved",
                "reason": "contract_type_option_with_strike",
            }
        return {
            "instrument_type": "unknown",
            "right": raw,
            "option_right": "call" if raw == "C" else "put",
            "status": "unknown",
            "reason": "option_contract_type_without_strike",
        }

    if raw in {"", "F", "M", "D"}:
        if has_strike:
            return {
                "instrument_type": "unknown",
                "right": None,
                "option_right": None,
                "status": "conflict",
                "reason": "future_contract_type_with_strike",
            }
        return {
            "instrument_type": "future",
            "right": None,
            "option_right": None,
            "status": "resolved",
            "reason": "contract_type_future_without_strike",
        }

    if raw == "I":
        return {
            "instrument_type": "index" if not has_strike else "unknown",
            "right": None,
            "option_right": None,
            "status": "resolved" if not has_strike else "conflict",
            "reason": "contract_type_index" if not has_strike else "index_contract_type_with_strike",
        }

    if raw in {"CASH", "CS"}:
        return {
            "instrument_type": "cash" if not has_strike else "unknown",
            "right": None,
            "option_right": None,
            "status": "resolved" if not has_strike else "conflict",
            "reason": "contract_type_cash" if not has_strike else "cash_contract_type_with_strike",
        }

    return {
        "instrument_type": "unknown",
        "right": None,
        "option_right": None,
        "status": "unknown",
        "reason": f"unsupported_contract_type:{raw or 'missing'}",
    }


def summarize_product_identity(df: pd.DataFrame, master: ProductIdentityMaster | None = None) -> dict:
    """Summarize product identity coverage for run summaries and manifests."""
    if "product_identity_status" not in df.columns:
        return {
            "status": "not_checked",
            "rows": int(len(df)),
            "unknown_rows": None,
            "conflict_rows": None,
        }

    status = df["product_identity_status"].astype("string").fillna("unknown")
    unknown = int(status.eq("unknown").sum())
    conflict = int(status.eq("conflict").sum())
    resolved = int(status.eq("resolved").sum())
    out = {
        "status": "fail" if unknown or conflict else "pass",
        "rows": int(len(df)),
        "resolved_rows": resolved,
        "unknown_rows": unknown,
        "conflict_rows": conflict,
        "by_status": {str(k): int(v) for k, v in status.value_counts(dropna=False).items()},
        "evidence_refs": sorted(
            str(v)
            for v in df.get("product_identity_evidence_ref", pd.Series(dtype=object)).dropna().unique()
        ),
    }
    if master is not None:
        out["mapping_hash"] = master.mapping_hash
        out["mapping_schema_version"] = master.schema_version
        out["evidence_warnings"] = list(master.evidence_warnings)
    return out


def _first_series(df: pd.DataFrame, names: tuple[str, ...]) -> pd.Series:
    for name in names:
        if name in df.columns:
            return df[name]
    return pd.Series(pd.NA, index=df.index)


def _norm(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return ""
    return str(value).strip().casefold()
