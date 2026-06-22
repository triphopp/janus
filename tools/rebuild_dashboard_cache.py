#!/usr/bin/env python3
"""Rebuild the dashboard view-model cache from existing pipeline artifacts.

Reads only artifact files on disk — never imports or calls run_pipeline.
Cache files live in outputs/dashboard_cache/ and are derived-only
(safe to delete and rebuild at any time).

Usage:
    python3 tools/rebuild_dashboard_cache.py --all
    python3 tools/rebuild_dashboard_cache.py --run-id wti_q4
    python3 tools/rebuild_dashboard_cache.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

# Resolve project root (tools/ lives one level below repo root)
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from web import scanner
from web import view_model as _vm

CACHE_DIR = _ROOT / "outputs" / "dashboard_cache"
CACHE_SCHEMA_VERSION = "dashboard.cache.v1"
NORMALIZER_VERSION = "2026-06-22"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _source_artifact_meta(path: Path) -> dict:
    if not path.exists():
        return {"path": str(path), "exists": False}
    return {
        "path": str(path),
        "exists": True,
        "mtime_ns": path.stat().st_mtime_ns,
        "sha256": _sha256(path),
    }


def _is_cache_fresh(cache_path: Path, source_metas: list[dict]) -> bool:
    if not cache_path.exists():
        return False
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if cached.get("normalizer_version") != NORMALIZER_VERSION:
        return False
    stored = {m["path"]: m for m in cached.get("source_artifacts", [])}
    for meta in source_metas:
        p = meta["path"]
        if p not in stored:
            return False
        if meta.get("mtime_ns") != stored[p].get("mtime_ns"):
            return False
        if meta.get("sha256") != stored[p].get("sha256"):
            return False
    return True


# ── Core rebuild ──────────────────────────────────────────────────────────────

def rebuild_cache(
    outputs_dir: Path | None = None,
    run_ids: list[str] | None = None,
    *,
    force: bool = False,
) -> dict:
    """Rebuild cache for the given run_ids (or all discovered runs).

    Returns {"rebuilt": [...], "skipped": [...], "errors": [...]}
    """
    if outputs_dir is not None:
        scanner.OUTPUTS = outputs_dir
        scanner.MANIFEST_DIR = outputs_dir / "manifest"
        scanner.BREAKS_DIR = outputs_dir / "breaks"
        scanner.DIFF_DIR = outputs_dir / "diff"
        scanner.RUNS_DIR = outputs_dir / "runs"
        cache_dir = outputs_dir / "dashboard_cache"
    else:
        cache_dir = CACHE_DIR

    cache_dir.mkdir(parents=True, exist_ok=True)

    all_rows = scanner.scan_runs()
    if run_ids is not None:
        all_rows = [r for r in all_rows if r["run_id"] in run_ids]

    rebuilt: list[str] = []
    skipped: list[str] = []
    errors: list[dict] = []

    for row in all_rows:
        rid = row["run_id"]
        cache_path = cache_dir / f"{rid}.dashboard.json"
        run_dir = scanner.find_run_dir(rid)

        # Collect source artifact metadata for freshness check
        source_paths: list[Path] = []
        if run_dir and (run_dir / "summary.json").exists():
            source_paths.append(run_dir / "summary.json")
        manifest_path = scanner.MANIFEST_DIR / f"{rid}.json"
        if manifest_path.exists():
            source_paths.append(manifest_path)

        source_metas = [_source_artifact_meta(p) for p in source_paths]

        if not force and _is_cache_fresh(cache_path, source_metas):
            skipped.append(rid)
            continue

        try:
            summary = scanner._summary_for_run(rid) or {}
            tagged = scanner.load_tagged_return_outliers(rid)
            artifacts = {
                "run_id": rid,
                "summary": summary,
                "summary_path": str(run_dir / "summary.json") if run_dir else None,
                "has_diff": row.get("has_diff", False),
                "has_report": row.get("has_report", False),
                "breaks_open": row.get("breaks_open", 0),
                "unattributed": row.get("unattributed", 0),
                "breaks": scanner.load_breaks(rid),
                "changes_sample": scanner._changes_sample(rid),
                "stage_hops": scanner._stage_hops(rid),
                "tagged_return_outliers": tagged,
                "price_adjustments": row.get("price_adjustments"),
                "vol_surface_summary": _vm.load_vol_surface_summary(run_dir) if run_dir else None,
            }
            view_model_data = _vm.build_run_detail_v1(artifacts)
            for k, v in row.items():
                if k not in view_model_data:
                    view_model_data[k] = v

            cache_entry = {
                "cache_schema_version": CACHE_SCHEMA_VERSION,
                "normalizer_version": NORMALIZER_VERSION,
                "run_id": rid,
                "source_artifacts": source_metas,
                "view_model": view_model_data,
            }
            cache_path.write_text(
                json.dumps(cache_entry, indent=2, default=str), encoding="utf-8"
            )
            rebuilt.append(rid)
        except Exception as exc:
            errors.append({"run_id": rid, "error": str(exc)})

    return {"rebuilt": rebuilt, "skipped": skipped, "errors": errors}


def check_cache(outputs_dir: Path | None = None) -> bool:
    """Return True if all cached runs are fresh. Exits nonzero if stale/invalid."""
    if outputs_dir is not None:
        cache_dir = outputs_dir / "dashboard_cache"
        scanner.OUTPUTS = outputs_dir
        scanner.RUNS_DIR = outputs_dir / "runs"
        scanner.MANIFEST_DIR = outputs_dir / "manifest"
        scanner.BREAKS_DIR = outputs_dir / "breaks"
        scanner.DIFF_DIR = outputs_dir / "diff"
    else:
        cache_dir = CACHE_DIR

    if not cache_dir.exists():
        print("Cache directory does not exist — run --all to build.")
        return False

    stale: list[str] = []
    for cache_path in cache_dir.glob("*.dashboard.json"):
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            stale.append(cache_path.stem)
            continue
        if cached.get("normalizer_version") != NORMALIZER_VERSION:
            stale.append(cached.get("run_id", cache_path.stem))
            continue
        for meta in cached.get("source_artifacts", []):
            p = Path(meta["path"])
            if not p.exists():
                continue
            if p.stat().st_mtime_ns != meta.get("mtime_ns"):
                stale.append(cached.get("run_id", cache_path.stem))
                break

    if stale:
        print(f"Stale cache entries: {stale}")
        return False
    print(f"Cache OK — {len(list(cache_dir.glob('*.dashboard.json')))} entries.")
    return True


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild Janus dashboard cache from artifacts.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true",
                       help="Rebuild cache for all discovered runs.")
    group.add_argument("--run-id", metavar="RUN_ID",
                       help="Rebuild cache for a single run.")
    group.add_argument("--check", action="store_true",
                       help="Exit nonzero if any cache entry is stale or invalid.")
    parser.add_argument("--force", action="store_true",
                        help="Force rebuild even if cache is fresh.")
    args = parser.parse_args()

    if args.check:
        return 0 if check_cache() else 1

    run_ids = None if args.all else [args.run_id]
    result = rebuild_cache(run_ids=run_ids, force=args.force)
    print(f"Rebuilt: {len(result['rebuilt'])}  Skipped: {len(result['skipped'])}  "
          f"Errors: {len(result['errors'])}")
    for e in result["errors"]:
        print(f"  ERROR {e['run_id']}: {e['error']}")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
