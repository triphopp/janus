"""Readiness diagnostics for a ticker — cheap checks, no pipeline run.

``doctor`` answers "can I run an official ``janus run`` for this symbol, and if
not, what is the single next command to fix it?" Every check returns a status
(``pass`` / ``warn`` / ``fail``) and an actionable next step. It never runs
expensive pipeline stages or writes heavy artifacts.
"""

from __future__ import annotations

from pathlib import Path

from cli import registry, resolve

__all__ = ["run_doctor", "Check"]


def Check(name: str, status: str, detail: str, next_action: str | None = None) -> dict:
    return {"name": name, "status": status, "detail": detail, "next_action": next_action}


def run_doctor(
    symbol: str,
    *,
    registry_path: str | Path = registry.DEFAULT_REGISTRY_PATH,
    config_dir: Path | None = None,
) -> list[dict]:
    checks: list[dict] = []

    # 1. Profile resolution
    try:
        cfg = resolve.resolve_profile(symbol, config_dir=config_dir)
    except resolve.ResolveError as exc:
        return [Check("profile", "fail", str(exc), "check the symbol spelling")]

    summ = resolve.profile_summary(cfg)
    if summ["synthesized"]:
        checks.append(Check(
            "profile", "warn",
            f"no instrument YAML for {symbol.upper()}; synthesized equity profile",
            "for settlement options add configs/instruments/<symbol>.yaml",
        ))
    else:
        checks.append(Check("profile", "pass", f"resolved {summ['profile']} ({summ['family']})"))

    file_backed = summ["file_backed"]

    # 2/3. Data source + hash
    if file_backed:
        source = registry.get_active(symbol, registry_path=registry_path)
        if source is None:
            checks.append(Check(
                "data_source", "fail",
                f"no data source registered for {symbol.upper()}",
                f"janus import {symbol.upper()} path/to/file.csv",
            ))
        else:
            path = Path(source.path)
            if not path.exists():
                checks.append(Check(
                    "data_source", "fail",
                    f"registered file is missing: {source.path}",
                    f"janus import {symbol.upper()} <new-path>",
                ))
            else:
                actual = registry.sha256_file(path)
                if actual == source.sha256:
                    checks.append(Check(
                        "data_source", "pass",
                        f"active {source.source_id}: hash matches ({source.rows} rows)",
                    ))
                else:
                    checks.append(Check(
                        "hash", "fail",
                        f"file changed since import ({source.sha256[:12]}... -> {actual[:12]}...)",
                        f"janus import {symbol.upper()} {source.path}",
                    ))
    else:
        checks.append(Check(
            "data_source", "warn",
            f"live provider ({summ['provider']}); not reproducible",
            f"janus run {symbol.upper()} --preset diagnostic",
        ))

    # 4. Settlement availability policy (options only)
    if str(cfg.get("family", "")).endswith("_options") and file_backed:
        lag = (cfg.get("available_at_lag") or {}).get("settlement")
        if lag:
            checks.append(Check("settlement_policy", "pass", f"settlement lag = {lag}"))
        else:
            checks.append(Check(
                "settlement_policy", "warn",
                "no available_at_lag.settlement configured",
                "add available_at_lag.settlement (e.g. '3h') to the instrument YAML",
            ))

    # 5. IV unit policy (options only)
    if str(cfg.get("family", "")).endswith("_options"):
        iv_source = cfg.get("iv_source")
        if iv_source:
            checks.append(Check("iv_policy", "pass", f"iv_source = {iv_source}"))
        else:
            checks.append(Check(
                "iv_policy", "warn", "no iv_source configured",
                "set iv_source: provided|solved in the instrument YAML",
            ))

    # 6. Export policy
    pricing = cfg.get("pricing") or {}
    if cfg.get("compute_greeks") or pricing.get("compute_greeks"):
        checks.append(Check("export_policy", "pass", "Greeks enabled -> option-chain export available"))
    else:
        checks.append(Check(
            "export_policy", "warn", "Greeks disabled -> no downstream export",
            "run with --preset export or --override pricing.compute_greeks=true",
        ))

    # 7. Event/calendar references
    calendars = cfg.get("event_calendars") or []
    missing = [c for c in calendars if not Path(c).exists()]
    if not calendars:
        checks.append(Check("calendars", "pass", "no event calendars referenced"))
    elif missing:
        checks.append(Check(
            "calendars", "fail",
            f"missing calendar file(s): {', '.join(missing)}",
            "fix event_calendars paths in the instrument YAML",
        ))
    else:
        checks.append(Check("calendars", "pass", f"{len(calendars)} calendar(s) present"))

    return checks
