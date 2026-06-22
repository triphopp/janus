"""Replay mode — reproduce a prior harness run from cached artifacts only.

A replay run:
  - must not call any live search or fetch provider
  - must read all search results and pages from cache
  - must produce the same verdict and same document IDs as the original
  - may differ only in harness_run_id and timestamps

Usage::

    from core.evidence_harness.replay import run_replay
    result = run_replay(manifest_path="outputs/evidence/harness/run_id/case_id/hrn_id/replay_manifest.json")
"""

from __future__ import annotations

import json
from pathlib import Path

from .schema import OutlierCasePackage, HarnessRunResult
from .config import HarnessConfig, load_harness_config
from .cache import HarnessCache, ReplaySearchProvider, ReplayFetchProvider, ReplayCacheMiss
from .controller import run_harness


def run_replay(
    manifest_path: str | Path,
    *,
    artifact_dir_override: str | None = None,
) -> HarnessRunResult:
    """Load a replay manifest and re-run the harness using only cached data.

    Parameters
    ----------
    manifest_path:
        Path to ``replay_manifest.json`` from a prior harness run.
    artifact_dir_override:
        Write new artifacts here instead of the original artifact_dir.
        Useful in tests to avoid touching real output dirs.

    Raises
    ------
    ReplayCacheMiss
        If the cache is incomplete and a required search or page entry is missing.
    FileNotFoundError
        If manifest_path, case_package.json, or config.json do not exist.
    """
    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        raise FileNotFoundError(f"replay manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    run_dir = manifest_path.parent

    # ── Load original case package ─────────────────────────────────────────
    case_package_path = run_dir / "case_package.json"
    if not case_package_path.exists():
        raise FileNotFoundError(f"case_package.json not found in {run_dir}")
    case = OutlierCasePackage.from_dict(json.loads(case_package_path.read_text(encoding="utf-8")))

    # ── Load original config ───────────────────────────────────────────────
    config_path = run_dir / "config.json"
    if not config_path.exists():
        cfg = load_harness_config()
    else:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        cfg = _config_from_dict(raw)

    cfg.mode = "replay"

    if artifact_dir_override:
        cfg.artifact_dir = artifact_dir_override

    # ── Build replay providers ─────────────────────────────────────────────
    cache = HarnessCache(cfg.cache_dir)
    cache_entries: list[dict] = manifest.get("cache_entries", [])

    search_provider = ReplaySearchProvider(cache)
    fetch_provider = ReplayFetchProvider(cache, cache_entries)

    # ── Run harness in replay mode ─────────────────────────────────────────
    result = run_harness(case, cfg, search_provider=search_provider, fetch_provider=fetch_provider)
    return result


def verify_replay(
    original_result: HarnessRunResult,
    replay_result: HarnessRunResult,
) -> dict:
    """Compare an original run with its replay. Returns a verification report."""
    issues: list[str] = []

    if original_result.verdict != replay_result.verdict:
        issues.append(
            f"verdict mismatch: original={original_result.verdict!r} "
            f"replay={replay_result.verdict!r}"
        )

    orig_docs = {d.document_id for d in original_result.documents}
    replay_docs = {d.document_id for d in replay_result.documents}
    missing = orig_docs - replay_docs
    extra = replay_docs - orig_docs
    if missing:
        issues.append(f"document_ids missing from replay: {sorted(missing)}")
    if extra:
        issues.append(f"extra document_ids in replay: {sorted(extra)}")

    orig_queries = [q.query_id for q in original_result.queries]
    replay_queries = [q.query_id for q in replay_result.queries]
    if orig_queries != replay_queries:
        issues.append("query_ids differ between original and replay")

    return {
        "status": "pass" if not issues else "fail",
        "issues": issues,
        "original_verdict": original_result.verdict,
        "replay_verdict": replay_result.verdict,
        "original_document_count": len(orig_docs),
        "replay_document_count": len(replay_docs),
    }


def _config_from_dict(raw: dict) -> HarnessConfig:
    fields = HarnessConfig.__dataclass_fields__
    kwargs = {k: raw[k] for k in fields if k in raw}
    cfg = HarnessConfig(**kwargs)
    return cfg
