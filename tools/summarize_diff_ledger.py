#!/usr/bin/env python3
"""Backfill diff ledger policy summaries from existing JSONL artifacts.

Never imports or calls run_pipeline.  Reads only *_changes.jsonl files
already on disk and writes <run_id>_summary.json beside them.

Usage:
    python3 tools/summarize_diff_ledger.py --run-id wti_2025
    python3 tools/summarize_diff_ledger.py --all
    python3 tools/summarize_diff_ledger.py --check
    python3 tools/summarize_diff_ledger.py --all --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from core import diff_review

DIFF_DIR = _ROOT / "outputs" / "diff"


def _ledgers(diff_dir: Path) -> list[Path]:
    return sorted(diff_dir.glob("*_changes.jsonl"))


def _summary_path(ledger: Path) -> Path:
    return ledger.parent / ledger.name.replace("_changes.jsonl", "_summary.json")


def run_all(diff_dir: Path, *, force: bool = False) -> dict:
    ledgers = _ledgers(diff_dir)
    rebuilt, skipped, errors = [], [], []
    for ledger in ledgers:
        sp = _summary_path(ledger)
        if not force and diff_review.is_summary_fresh(ledger, sp):
            skipped.append(ledger.stem)
            continue
        try:
            diff_review.write_diff_summary(ledger, out_dir=diff_dir)
            rebuilt.append(ledger.stem)
        except Exception as exc:
            errors.append({"ledger": str(ledger), "error": str(exc)})
    return {"rebuilt": rebuilt, "skipped": skipped, "errors": errors}


def run_one(run_id: str, diff_dir: Path, *, force: bool = False) -> dict:
    ledger = diff_dir / f"{run_id}_changes.jsonl"
    if not ledger.exists():
        return {"rebuilt": [], "skipped": [], "errors": [
            {"ledger": str(ledger), "error": "ledger not found"}]}
    sp = _summary_path(ledger)
    if not force and diff_review.is_summary_fresh(ledger, sp):
        return {"rebuilt": [], "skipped": [run_id], "errors": []}
    try:
        diff_review.write_diff_summary(ledger, run_id=run_id, out_dir=diff_dir)
        return {"rebuilt": [run_id], "skipped": [], "errors": []}
    except Exception as exc:
        return {"rebuilt": [], "skipped": [], "errors": [
            {"ledger": str(ledger), "error": str(exc)}]}


def check(diff_dir: Path) -> bool:
    ledgers = _ledgers(diff_dir)
    if not ledgers:
        print("No diff ledgers found.")
        return True
    stale = [l for l in ledgers if not diff_review.is_summary_fresh(l, _summary_path(l))]
    if stale:
        print(f"Stale or missing summaries: {[l.stem for l in stale]}")
        return False
    print(f"All {len(ledgers)} ledger(s) have fresh summaries.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill diff ledger policy summaries from existing artifacts."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Summarize all ledgers.")
    group.add_argument("--run-id", metavar="RUN_ID", help="Summarize one run.")
    group.add_argument("--check", action="store_true",
                       help="Exit nonzero if any summary is missing or stale.")
    parser.add_argument("--force", action="store_true",
                        help="Force regeneration even when summary is fresh.")
    parser.add_argument("--diff-dir", default=str(DIFF_DIR),
                        help=f"Diff artifacts directory (default: {DIFF_DIR})")
    args = parser.parse_args()

    diff_dir = Path(args.diff_dir)

    if args.check:
        return 0 if check(diff_dir) else 1

    result = run_all(diff_dir, force=args.force) if args.all else \
             run_one(args.run_id, diff_dir, force=args.force)

    print(f"Rebuilt: {len(result['rebuilt'])}  "
          f"Skipped: {len(result['skipped'])}  "
          f"Errors: {len(result['errors'])}")
    for e in result["errors"]:
        print(f"  ERROR {e['ledger']}: {e['error']}")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
