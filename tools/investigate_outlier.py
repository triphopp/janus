"""CLI — investigate an outlier case with the Evidence Search Harness.

Usage::

    # Run from a pre-built case package JSON:
    python tools/investigate_outlier.py \\
        --case-package outputs/evidence/cases/wti_2025/case_001.json \\
        --config configs/evidence_search.yaml \\
        --mode mock

    # Select from existing run artifacts:
    python tools/investigate_outlier.py \\
        --run-id wti_2025 \\
        --case-id case_001 \\
        --mode replay

Exit codes:
    0  harness completed (verdict may still be unsupported or insufficient_evidence)
    1  malformed input, policy violation, or unrecoverable runtime failure
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.evidence_harness.schema import OutlierCasePackage
from core.evidence_harness.config import load_harness_config
from core.evidence_harness.controller import run_harness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Investigate an outlier case with the Janus Evidence Search Harness."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--case-package", metavar="PATH",
        help="Path to a pre-built OutlierCasePackage JSON file.",
    )
    source.add_argument(
        "--run-id", metavar="ID",
        help="Select from existing run artifacts (requires --case-id).",
    )
    parser.add_argument("--case-id", metavar="ID", help="Case ID (required with --run-id).")
    parser.add_argument("--config", default=None, metavar="PATH",
                        help="Path to evidence_search.yaml (default: configs/evidence_search.yaml).")
    parser.add_argument("--mode", default=None, choices=["mock", "replay", "live"],
                        help="Override harness mode.")
    parser.add_argument("--artifact-dir", default=None, metavar="DIR",
                        help="Write artifacts here (overrides config).")
    args = parser.parse_args(argv)

    # ── Load config ────────────────────────────────────────────────────────
    config_path = args.config or "configs/evidence_search.yaml"
    try:
        cfg = load_harness_config(config_path)
    except Exception as e:
        print(f"ERROR loading config: {e}", file=sys.stderr)
        return 1
    if args.mode:
        cfg.mode = args.mode
    if args.artifact_dir:
        cfg.artifact_dir = args.artifact_dir

    # ── Load case ──────────────────────────────────────────────────────────
    if args.case_package:
        pkg_path = Path(args.case_package)
        if not pkg_path.exists():
            print(f"ERROR: case package not found: {pkg_path}", file=sys.stderr)
            return 1
        try:
            case = OutlierCasePackage.from_dict(json.loads(pkg_path.read_text()))
        except Exception as e:
            print(f"ERROR: malformed case package — {e}", file=sys.stderr)
            return 1
    else:
        if not args.case_id:
            print("ERROR: --case-id required with --run-id", file=sys.stderr)
            return 1
        pkg_path = Path(cfg.artifact_dir) / args.run_id / args.case_id
        candidates = sorted(pkg_path.glob("*/case_package.json"))
        if not candidates:
            print(f"ERROR: no case_package.json found under {pkg_path}", file=sys.stderr)
            return 1
        try:
            case = OutlierCasePackage.from_dict(json.loads(candidates[-1].read_text()))
        except Exception as e:
            print(f"ERROR: malformed case package — {e}", file=sys.stderr)
            return 1

    # ── Run harness ────────────────────────────────────────────────────────
    try:
        result = run_harness(case, cfg)
    except ValueError as e:
        print(f"ERROR: invalid case — {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: harness failed — {e}", file=sys.stderr)
        return 1

    from pathlib import Path as _P
    verdict_path = _P(result.artifact_paths.get("verdict", ""))
    print(f"case    : {result.case_id}")
    print(f"verdict : {result.verdict}")
    print(f"confidence : {result.confidence}")
    print(f"sources : {len(result.documents)}")
    print(f"checks  : {len(result.checks)}")
    print(f"artifacts : {verdict_path.parent if verdict_path.exists() else 'n/a'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
