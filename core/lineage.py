"""Column-level data lineage graph (P3, §5).

Every derived column declares its inputs, transform, tier, and lookback. Static
structure that complements the runtime CDC (§6). Three payoffs:

1. **Impact analysis**: "settlement ``price`` is suspect on date D — which features are
   contaminated?" → walk the graph, don't grep. (`impact_of`)
2. **Auto-purge** (closes leakage L5 / audit C2): ``purge_bars = max(lookback_bars)`` over
   the lineage of a signal feeds the splitter's purge window. (`max_lookback`)
3. **Coverage gate** (§13.6): a derived output column with no lineage record fails gold
   promotion — declarations cannot silently drift from code. (`missing_records`)

Graph format (``lineage/<family>.json``):
    { "<column>": {"inputs": [...], "op": "...", "tier": "bronze|silver|gold",
                   "lookback_bars": <int>} }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

DEFAULT_LINEAGE_DIR = Path("lineage")

# family → lineage file stem
FAMILY_LINEAGE = {
    "futures": "futures_options",
    "futures_options": "futures_options",
    "equity": "equity",
    "equity_options": "equity",
}


def load_lineage(
    family: str,
    lineage_dir: Path | str = DEFAULT_LINEAGE_DIR,
) -> dict:
    """Load the lineage graph for a family, or {} if none defined."""
    stem = FAMILY_LINEAGE.get(family, family)
    path = Path(lineage_dir) / f"{stem}.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def is_root(graph: dict, col: str) -> bool:
    """A raw/source column (not a derived node in the graph)."""
    return col not in graph


def direct_consumers(graph: dict, col: str) -> list[str]:
    """Columns that list ``col`` directly among their inputs."""
    return [c for c, spec in graph.items() if col in (spec.get("inputs") or [])]


def impact_of(graph: dict, suspect: str) -> list[str]:
    """All columns transitively derived from ``suspect`` (downstream contamination set)."""
    seen: set[str] = set()
    stack = [suspect]
    while stack:
        cur = stack.pop()
        for consumer in direct_consumers(graph, cur):
            if consumer not in seen:
                seen.add(consumer)
                stack.append(consumer)
    return sorted(seen)


def upstream_inputs(graph: dict, col: str) -> list[str]:
    """All transitive inputs feeding ``col`` (its provenance set)."""
    seen: set[str] = set()
    stack = list((graph.get(col, {}) or {}).get("inputs") or [])
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend((graph.get(cur, {}) or {}).get("inputs") or [])
    return sorted(seen)


def max_lookback(graph: dict, target: Optional[str] = None) -> int:
    """Max lookback_bars across the graph (or across the provenance of ``target``).

    This IS the correct purge window by definition: a feature's lookback is how far
    its window reaches back, so train/val cannot overlap if purged by it (§ leakage L5).
    """
    if target is not None:
        nodes = set(upstream_inputs(graph, target)) | {target}
        relevant = {c: graph[c] for c in nodes if c in graph}
    else:
        relevant = graph
    return max((int(spec.get("lookback_bars", 0) or 0) for spec in relevant.values()), default=0)


def missing_records(graph: dict, derived_cols: list[str], roots: Optional[set[str]] = None) -> list[str]:
    """Derived columns that have no lineage record (§13.6 coverage gate).

    ``roots`` are known raw/source columns to exclude from the requirement.
    """
    roots = roots or set()
    return sorted(c for c in derived_cols if c not in graph and c not in roots)


def validate_coverage(
    graph: dict,
    df_columns: list[str],
    roots: set[str],
    *,
    ignore_prefixes: tuple[str, ...] = ("_",),
) -> dict:
    """Coverage report: which derived columns lack a lineage record.

    Columns starting with an ignored prefix (e.g. ``_outlier_flag`` audit flags) are
    exempt. Returns {covered: [...], missing: [...], ok: bool}.
    """
    derived = [
        c for c in df_columns
        if c not in roots and not any(c.startswith(p) for p in ignore_prefixes)
    ]
    missing = missing_records(graph, derived, roots)
    covered = [c for c in derived if c in graph]
    return {"covered": covered, "missing": missing, "ok": not missing}
