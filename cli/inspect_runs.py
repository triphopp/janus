"""Read-only inspectors for ``janus list`` and ``janus show``.

``list`` reports known profiles and their run-readiness without running
anything. ``show`` summarizes a completed run from its ``summary.json`` — guard
status, report/export paths, downstream artifacts. Neither reruns the pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path

from cli import registry, resolve

__all__ = ["list_profiles", "show_run", "RunNotFound"]


class RunNotFound(FileNotFoundError):
    """Raised when ``janus show`` cannot locate a run."""


def _readiness(symbol: str, cfg: dict, registry_path) -> tuple[str, str]:
    """Return (status, next_command) for one profile."""
    if not resolve.is_file_backed(cfg):
        return "ready (live)", f"janus run {symbol} --preset diagnostic --window 2024"
    source = registry.get_active(symbol, registry_path=registry_path)
    if source is None:
        return "missing data", f"janus import {symbol} path/to/file.csv"
    path = Path(source.path)
    if not path.exists():
        return "file missing", f"janus import {symbol} <new-path>"
    if registry.sha256_file(path) != source.sha256:
        return "hash mismatch", f"janus import {symbol} {source.path}"
    return "ready", f"janus run {symbol} --window 2024Q4"


def list_profiles(
    *,
    registry_path: str | Path = registry.DEFAULT_REGISTRY_PATH,
    config_dir: Path | None = None,
) -> list[dict]:
    rows: list[dict] = []
    for name in resolve.known_profiles(config_dir=config_dir):
        try:
            cfg = resolve.resolve_profile(name, config_dir=config_dir)
        except resolve.ResolveError:
            continue
        status, nxt = _readiness(name.upper(), cfg, registry_path)
        rows.append({
            "symbol": name,
            "family": cfg.get("family", "equity"),
            "provider": cfg.get("provider", "settlement"),
            "status": status,
            "next": nxt,
        })
    return rows


def _locate_run(run_id: str, outputs_dir: Path) -> Path | None:
    runs = outputs_dir / "runs"
    if not runs.exists():
        return None
    for symbol_dir in runs.iterdir():
        if symbol_dir.is_dir():
            candidate = symbol_dir / run_id
            if (candidate / "summary.json").exists():
                return candidate
    return None


def show_run(run_id: str, *, outputs_dir: str | Path = "outputs") -> dict:
    outputs_dir = Path(outputs_dir)
    run_dir = _locate_run(run_id, outputs_dir)
    if run_dir is None:
        raise RunNotFound(
            f"no run named {run_id!r} under {outputs_dir / 'runs'}. "
            "Run `janus list` to see profiles, or check the run id."
        )

    with open(run_dir / "summary.json", encoding="utf-8") as fh:
        summary = json.load(fh)

    report = run_dir / "report" / "final_report.html"
    export = run_dir / "data" / "option_chain_greeks.parquet"
    prepared = run_dir / "data" / "prepared.parquet"

    guards = {
        k: v for k, v in summary.items()
        if isinstance(v, dict) and "status" in v
    }
    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "reproducible": summary.get("reproducible"),
        "preset": summary.get("preset"),
        "guards": guards,
        "report": str(report) if report.exists() else None,
        "export": str(export) if export.exists() else None,
        "prepared": str(prepared) if prepared.exists() else None,
        "summary": summary,
    }
