"""Run manifest — content-pinned reproducibility record (P1, invariant I6).

Replaces ``run_id = timestamp`` as the only provenance. A run is reproducible only
if every input is pinned: code version, config, contract versions, exact input data
hash, the point-in-time knowledge cutoff, and the environment.

See: Memory/plans/data_ops_architecture.md §8 (manifest), §13.10 (replay modes).

Two replay modes (§13.10):
- bit_replay     : same deps/container → every hash must match exactly.
- semantic_replay: values within contract tolerance, row identity stable.
``compare_manifests`` here implements the hash side (bit_replay).
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from core.audit import canonical_frame_hash


def git_commit() -> Optional[str]:
    """Current git commit hash, or None outside a repo / git missing."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        sha = out.stdout.strip()
        return f"git:{sha}" if sha else None
    except Exception:
        return None


def config_hash(cfg: dict) -> str:
    """Canonical hash of the (JSON-serializable) config."""
    blob = json.dumps(cfg, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def env_info() -> dict:
    """Dependency + platform versions — what bit_replay must hold constant."""
    info = {"python": platform.python_version(), "platform": platform.platform()}
    for mod in ("numpy", "pandas", "scipy", "pyarrow"):
        try:
            info[mod] = __import__(mod).__version__
        except Exception:
            info[mod] = None
    return info


def _knowledge_cutoff(df: pd.DataFrame, fallback) -> Optional[str]:
    """PIT boundary = latest knowledge_time in the data (available_at), else fallback."""
    if "available_at" in df.columns and len(df):
        ts = pd.to_datetime(df["available_at"], errors="coerce", utc=True).max()
        if pd.notna(ts):
            return ts.isoformat()
    return str(fallback) if fallback is not None else None


def build_manifest(
    run_id: str,
    cfg: dict,
    raw_df: pd.DataFrame,
    prepared_df: pd.DataFrame,
    *,
    symbol: str,
    contract_report: Optional[dict] = None,
    n_trials: Optional[int] = None,
    n_trials_source: str = "config",
    knowledge_cutoff_fallback=None,
    extra: Optional[dict] = None,
) -> dict:
    """Assemble a content-pinned run manifest dict."""
    contract_versions = {}
    if contract_report and contract_report.get("contract_id"):
        contract_versions[contract_report["contract_id"]] = contract_report.get("version")

    manifest = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "code_version": git_commit(),
        "config_hash": config_hash(cfg),
        "contract_versions": contract_versions,
        "input_data_hashes": {symbol: canonical_frame_hash(raw_df)} if len(raw_df) else {},
        "output_data_hashes": {"prepared": canonical_frame_hash(prepared_df)} if len(prepared_df) else {},
        "knowledge_time_cutoff": _knowledge_cutoff(prepared_df, knowledge_cutoff_fallback),
        # DSR honesty (audit H5): record the ACTUAL trial count + where it came from.
        # 'config' = single-run value; a true research-campaign count needs a campaign
        # registry above the run level (open decision §14.4) — flagged, not faked.
        "n_trials": n_trials,
        "n_trials_source": n_trials_source,
        "env": env_info(),
    }
    if extra:
        manifest["extra"] = extra
    return manifest


def write_manifest(manifest: dict, out_dir: Path | str = Path("outputs/manifest")) -> str:
    """Persist a manifest to outputs/manifest/<run_id>.json. Returns the path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{manifest['run_id']}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str)
    return str(path)


def load_manifest(path: Path | str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# Fields that must match for a bit-identical replay (env intentionally compared
# separately — a dependency bump is reported, not silently failed).
_REPLAY_KEYS = (
    "code_version",
    "config_hash",
    "contract_versions",
    "input_data_hashes",
    "output_data_hashes",
    "knowledge_time_cutoff",
)


def compare_manifests(old: dict, new: dict) -> dict:
    """Diff two manifests for replay verification (bit_replay).

    Returns {match: bool, mismatches: {key: {old, new}}, env_changed: bool}.
    """
    mismatches = {}
    for key in _REPLAY_KEYS:
        if old.get(key) != new.get(key):
            mismatches[key] = {"old": old.get(key), "new": new.get(key)}
    return {
        "match": not mismatches,
        "mismatches": mismatches,
        "env_changed": old.get("env") != new.get("env"),
    }
