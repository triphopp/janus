"""Run-plan assembly: resolve everything needed to run, without running.

A ``RunPlan`` is the single object ``explain``, ``doctor``, and ``run`` share. It
binds the resolved profile, the active data source, the date window, the run/
universe presets, the enforced guards, and the expected output location into one
inspectable structure. ``explain`` prints it; ``run`` executes it; ``doctor``
reports on its readiness — none of them duplicate resolution logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cli import presets, registry, resolve

__all__ = ["RunPlan", "PlanError", "build_plan"]


class PlanError(ValueError):
    """Raised when a plan cannot be assembled."""


@dataclass
class RunPlan:
    symbol: str
    profile: str
    family: str
    provider: str
    file_backed: bool
    start: str
    end: str
    preset: str
    universe: str
    reproducible: bool
    source: registry.SourceRecord | None
    cfg: dict
    output_dir: str
    advanced_overrides: dict = field(default_factory=dict)
    guards: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return all(g["status"] != "fail" for g in self.guards)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "profile": self.profile,
            "family": self.family,
            "provider": self.provider,
            "file_backed": self.file_backed,
            "window": {"start": self.start, "end": self.end},
            "preset": self.preset,
            "universe": self.universe,
            "reproducible": self.reproducible,
            "source": self.source.to_dict() if self.source else None,
            "output_dir": self.output_dir,
            "advanced_overrides": self.advanced_overrides,
            "guards": self.guards,
            "warnings": self.warnings,
            "ready": self.ready,
        }


def _expected_output_dir(cfg: dict, run_id: str) -> str:
    label = resolve.symbol_label(cfg)
    family = cfg.get("family", "equity")
    try:
        from core import reporting

        return str(reporting.run_output_dir("outputs", run_id, label, family, "", ""))
    except Exception:
        safe = "".join(c if c.isalnum() else "_" for c in str(label))
        return str(Path("outputs") / "runs" / safe / run_id)


def build_plan(
    symbol: str,
    *,
    start: str,
    end: str,
    preset: str = presets.DEFAULT_PRESET,
    universe: str = presets.DEFAULT_UNIVERSE,
    run_id: str | None = None,
    overrides: list[str] | None = None,
    registry_path: str | Path = registry.DEFAULT_REGISTRY_PATH,
    config_dir: Path | None = None,
) -> RunPlan:
    """Assemble a RunPlan and evaluate its guards without running the pipeline."""
    cfg = resolve.resolve_profile(symbol, config_dir=config_dir)
    cfg = presets.apply_run_preset(cfg, preset)
    file_backed = resolve.is_file_backed(cfg)
    spec = presets.RUN_PRESETS[preset]

    if file_backed:
        cfg = presets.apply_universe_preset(cfg, universe)
    cfg, recorded = presets.apply_overrides(cfg, overrides)

    guards: list[dict] = []
    warnings: list[str] = []
    source = None

    if file_backed:
        source = registry.get_active(symbol, registry_path=registry_path)
        if source is None:
            guards.append({
                "name": "data_source",
                "status": "fail" if spec["require_pinned"] else "warn",
                "detail": f"no data source registered for {symbol.upper()}",
                "next_action": f"janus import {symbol.upper()} path/to/file.csv",
            })
        else:
            cfg["data_file"] = source.path
            cfg["data_file_sha256"] = source.sha256
            cfg["data_version"] = f"sha256:{source.sha256}"
            cfg.setdefault("provider", "settlement")
            guards.append(_hash_guard(source, spec, warnings))
    else:
        # Provider-fetch profile (equity). Official runs are not reproducible
        # from a live snapshot, so steer the user to a pinned import.
        status = "warn" if spec["allow_provider"] else "fail"
        guards.append({
            "name": "data_source",
            "status": status,
            "detail": (
                f"{symbol.upper()} uses a live provider ({cfg.get('provider')}); "
                "live snapshots are not reproducible"
            ),
            "next_action": (
                f"janus run {symbol.upper()} --preset diagnostic"
                if not spec["allow_provider"]
                else None
            ),
        })
        if not spec["reproducible"]:
            warnings.append("non-reproducible: provider/unpinned input")

    rid = run_id or "explain"
    out_dir = _expected_output_dir(cfg, rid)

    return RunPlan(
        symbol=symbol.upper(),
        profile=cfg.get("_profile_name", symbol.upper()),
        family=cfg.get("family", "equity"),
        provider=cfg.get("provider", "settlement"),
        file_backed=file_backed,
        start=start,
        end=end,
        preset=preset,
        universe=universe if file_backed else "n/a",
        reproducible=bool(cfg.get("reproducible", False)) and source is not None,
        source=source,
        cfg=cfg,
        output_dir=out_dir,
        advanced_overrides=recorded,
        guards=guards,
        warnings=warnings,
    )


def _hash_guard(source: registry.SourceRecord, spec: dict, warnings: list[str]) -> dict:
    """Verify the on-disk file still matches the hash recorded at import time."""
    path = Path(source.path)
    if not path.exists():
        return {
            "name": "data_source",
            "status": "fail",
            "detail": f"registered file is missing: {source.path}",
            "next_action": f"janus import {{ticker}} {source.path}",
        }
    actual = registry.sha256_file(path)
    if actual != source.sha256:
        return {
            "name": "data_source",
            "status": "fail",
            "detail": (
                "file content changed since import "
                f"(expected {source.sha256[:12]}..., got {actual[:12]}...)"
            ),
            "next_action": "re-import the file to pin the new content",
        }
    return {
        "name": "data_source",
        "status": "pass",
        "detail": f"pinned {source.source_id} ({source.format}, {source.rows} rows)",
        "next_action": None,
    }
