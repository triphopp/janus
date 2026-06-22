"""CLI — replay a prior harness run from its cached artifacts.

Usage::

    python tools/replay_evidence_harness.py \\
        --manifest outputs/evidence/harness/<run_id>/<case_id>/<hrn_id>/replay_manifest.json

    # write replay artifacts to a custom dir:
    python tools/replay_evidence_harness.py \\
        --manifest path/to/replay_manifest.json \\
        --artifact-dir outputs/evidence/replay

Exit codes:
    0  replay completed (even if verdict is unsupported or insufficient_evidence)
    1  manifest not found, cache miss, or unrecoverable error
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from project root without install
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.evidence_harness.replay import run_replay, verify_replay
from core.evidence_harness.cache import ReplayCacheMiss


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay a Janus evidence harness run from cached artifacts."
    )
    parser.add_argument(
        "--manifest",
        required=True,
        metavar="PATH",
        help="Path to replay_manifest.json from a prior harness run.",
    )
    parser.add_argument(
        "--artifact-dir",
        default=None,
        metavar="DIR",
        help="Write replay artifacts here (default: original artifact_dir + /replay).",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Compare replay verdict and document IDs against the original verdict.json.",
    )
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    artifact_dir = args.artifact_dir
    if artifact_dir is None:
        artifact_dir = str(manifest_path.parent.parent.parent.parent / "replay")

    try:
        result = run_replay(manifest_path, artifact_dir_override=artifact_dir)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except ReplayCacheMiss as e:
        print(f"ERROR: cache miss — {e}", file=sys.stderr)
        print("The cache is incomplete. Re-run in live or mock mode to repopulate.", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: replay failed — {e}", file=sys.stderr)
        return 1

    verdict_path = Path(result.artifact_paths.get("verdict", ""))
    print(f"verdict : {result.verdict}")
    print(f"confidence : {result.confidence}")
    print(f"documents : {len(result.documents)}")
    print(f"artifacts : {verdict_path.parent if verdict_path.exists() else 'n/a'}")

    if args.verify:
        original_verdict_path = manifest_path.parent / "verdict.json"
        if not original_verdict_path.exists():
            print("WARN: original verdict.json not found, skipping verification", file=sys.stderr)
        else:
            orig_data = json.loads(original_verdict_path.read_text())
            if orig_data.get("verdict") != result.verdict:
                print(
                    f"VERIFY FAIL: original verdict={orig_data['verdict']!r} "
                    f"replay verdict={result.verdict!r}",
                    file=sys.stderr,
                )
                return 1
            print(f"verify  : PASS (verdict matches original)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
