"""``janus`` command-line facade — progressive subcommand dispatch.

Commands:
    janus import   SYMBOL FILE            register an external file (+ set active)
    janus run      SYMBOL [dates] [opts]  run the full pipeline
    janus doctor   SYMBOL                 readiness checks, no run
    janus explain  SYMBOL [dates] [opts]  print the resolved plan, no run
    janus list                            known profiles + readiness
    janus show     RUN_ID                 summarize a completed run
    janus data     list|use|import        manage the data-source registry
    janus clean    [--failed|--older-than]  prune generated outputs

Date inputs (run/explain):  --window YYYY|YYYY-MM|YYYYQn   OR
                            --from/--to (aliases --start/--end)

The default happy path needs only ``import`` then ``run`` — advanced pipeline
knobs are reachable through ``--advanced --override key=value`` and are recorded
in summary/manifest.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from cli import doctor as doctor_mod
from cli import inspect_runs
from cli import plan as plan_mod
from cli import presets, registry, resolve
from cli.dates import WindowError, resolve_window


# ── small print helpers ───────────────────────────────────────────────────────

_GLYPH = {"pass": "OK ", "warn": "!! ", "fail": "XX "}


def _err(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 2


def _print_guards(guards: list[dict]) -> None:
    for g in guards:
        print(f"  {_GLYPH.get(g['status'], '   ')}{g['name']}: {g['detail']}")
        if g.get("next_action"):
            print(f"       -> {g['next_action']}")


def _gen_run_id(symbol: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{symbol.lower()}_{stamp}"


# ── date arg wiring ───────────────────────────────────────────────────────────

def _add_date_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--window", help="YYYY | YYYY-MM | YYYYQn (e.g. 2024Q4)")
    p.add_argument("--from", dest="from_", help="range start YYYY-MM-DD")
    p.add_argument("--to", dest="to", help="range end YYYY-MM-DD")
    p.add_argument("--start", help="alias for --from")
    p.add_argument("--end", help="alias for --to")


def _resolve_dates(args) -> tuple[str, str]:
    return resolve_window(
        from_=args.from_, to=args.to, start=args.start, end=args.end, window=args.window
    )


def _add_run_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--preset", default=presets.DEFAULT_PRESET,
                   choices=sorted(presets.RUN_PRESETS),
                   help="trust posture (default: official)")
    p.add_argument("--universe", default=presets.DEFAULT_UNIVERSE,
                   help="option universe: all | liquid | near-term | custom:<name>")
    p.add_argument("--advanced", action="store_true",
                   help="enable advanced --override pipeline knobs")
    p.add_argument("--override", action="append", default=[], metavar="KEY=VALUE",
                   help="advanced dotted config override (requires --advanced)")


# ── commands ──────────────────────────────────────────────────────────────────

def cmd_import(args) -> int:
    try:
        rec = registry.import_source(
            args.symbol, args.file, provider=args.provider, use=True,
            registry_path=args.registry,
        )
    except registry.RegistryError as exc:
        return _err(str(exc))
    rng = f"{rec.date_range[0]}..{rec.date_range[1]}" if rec.date_range else "n/a"
    print(f"Imported {args.symbol.upper()} <- {rec.path}")
    print(f"  id={rec.source_id}  format={rec.format}  rows={rec.rows}  dates={rng}")
    print(f"  sha256={rec.sha256}")
    print(f"  active source for {args.symbol.upper()} set.")
    print(f"\nNext:\n  janus run {args.symbol.upper()} --window 2024Q4")
    return 0


def cmd_data(args) -> int:
    if args.data_cmd == "import":
        ns = argparse.Namespace(
            symbol=args.ticker, file=args.file, provider=args.provider,
            registry=args.registry,
        )
        return cmd_import(ns)
    if args.data_cmd == "list":
        info = registry.list_sources(args.ticker, registry_path=args.registry)
        if not info["sources"]:
            print(f"No data sources registered for {args.ticker.upper()}.")
            print(f"  janus import {args.ticker.upper()} path/to/file.csv")
            return 0
        print(f"Data sources for {args.ticker.upper()} (active: {info['active']}):")
        for sid, rec in sorted(info["sources"].items()):
            mark = "*" if sid == info["active"] else " "
            rng = f"{rec['date_range'][0]}..{rec['date_range'][1]}" if rec.get("date_range") else "n/a"
            print(f" {mark} {sid}: {rec['format']} {rec['rows']} rows "
                  f"sha={rec['sha256'][:12]}... dates={rng}")
            print(f"      {rec['path']}")
        return 0
    if args.data_cmd == "use":
        try:
            rec = registry.use_source(args.ticker, args.source_id, registry_path=args.registry)
        except registry.RegistryError as exc:
            return _err(str(exc))
        print(f"Active source for {args.ticker.upper()} -> {rec.source_id}")
        return 0
    return _err("unknown data subcommand")


def _build_plan_from_args(args, run_id):
    start, end = _resolve_dates(args)
    overrides = args.override if getattr(args, "advanced", False) else []
    if getattr(args, "override", None) and not getattr(args, "advanced", False):
        raise plan_mod.PlanError("--override requires --advanced")
    plan = plan_mod.build_plan(
        args.symbol, start=start, end=end, preset=args.preset,
        universe=args.universe, run_id=run_id, overrides=overrides,
        registry_path=args.registry,
    )
    plan.cfg["progress_mode"] = "plain"
    plan.cfg.setdefault("runtime_overrides", {})["progress"] = "plain"
    return plan


def cmd_explain(args) -> int:
    try:
        plan = _build_plan_from_args(args, run_id="explain")
    except (WindowError, presets.PresetError, plan_mod.PlanError, resolve.ResolveError) as exc:
        return _err(str(exc))

    print(f"Plan for {plan.symbol}:")
    print(f"  profile     : {plan.profile} ({plan.family}, provider={plan.provider})")
    print(f"  window      : {plan.start} .. {plan.end}")
    print(f"  preset      : {plan.preset}   universe: {plan.universe}")
    print(f"  reproducible: {plan.reproducible}")
    if plan.source:
        print(f"  data source : {plan.source.source_id} "
              f"(sha {plan.source.sha256[:12]}..., {plan.source.rows} rows)")
    if plan.advanced_overrides:
        print(f"  overrides   : {plan.advanced_overrides}")
    print(f"  output dir  : {plan.output_dir}")
    print("  guards:")
    _print_guards(plan.guards)
    for w in plan.warnings:
        print(f"  warning: {w}")
    print(f"\n  -> {'READY' if plan.ready else 'NOT READY'} to run.")
    return 0 if plan.ready else 2


def cmd_doctor(args) -> int:
    checks = doctor_mod.run_doctor(args.symbol, registry_path=args.registry)
    print(f"Doctor: {args.symbol.upper()}")
    _print_guards(checks)
    worst = "pass"
    for c in checks:
        if c["status"] == "fail":
            worst = "fail"
            break
        if c["status"] == "warn":
            worst = "warn"
    print(f"\n  -> {worst.upper()}")
    return 2 if worst == "fail" else 0


def cmd_run(args) -> int:
    run_id = args.name or _gen_run_id(args.symbol)
    try:
        plan = _build_plan_from_args(args, run_id=run_id)
    except (WindowError, presets.PresetError, plan_mod.PlanError, resolve.ResolveError) as exc:
        return _err(str(exc))

    if not plan.ready:
        print(f"{plan.symbol} is not ready to run ({plan.preset} preset):", file=sys.stderr)
        for g in plan.guards:
            if g["status"] == "fail":
                print(f"  {g['detail']}", file=sys.stderr)
                if g.get("next_action"):
                    print(f"\n  Run:\n    {g['next_action']}", file=sys.stderr)
        return 2

    # Progress: default to plain log lines (predictable for batch/CI); the user
    # can opt into a tqdm bar. Recorded in runtime_overrides for provenance.
    progress = getattr(args, "progress", "plain") or "plain"
    plan.cfg["progress_mode"] = progress
    plan.cfg.setdefault("runtime_overrides", {})["progress"] = progress

    # Heavy import deferred until we actually run.
    import run_pipeline as rp

    print(f"Running {plan.symbol} [{plan.preset}] {plan.start}..{plan.end} -> {run_id}")
    rp.run_pipeline(plan.cfg, plan.start, plan.end, run_id)
    return 0


def cmd_list(args) -> int:
    rows = inspect_runs.list_profiles(registry_path=args.registry)
    if not rows:
        print("No instrument profiles found under configs/instruments/.")
        return 0
    print(f"{'SYMBOL':<10} {'FAMILY':<18} {'STATUS':<16} NEXT")
    for r in rows:
        print(f"{r['symbol']:<10} {r['family']:<18} {r['status']:<16} {r['next']}")
    return 0


def cmd_show(args) -> int:
    try:
        info = inspect_runs.show_run(args.run_id, outputs_dir=args.outputs)
    except inspect_runs.RunNotFound as exc:
        return _err(str(exc))
    print(f"Run: {info['run_id']}  ({info['run_dir']})")
    print(f"  preset={info.get('preset')}  reproducible={info.get('reproducible')}")
    if info["guards"]:
        print("  guards:")
        for name, g in info["guards"].items():
            print(f"    {_GLYPH.get(g.get('status'), '   ')}{name}: {g.get('status')}")
    for label in ("report", "export", "prepared"):
        if info.get(label):
            print(f"  {label}: {info[label]}")
    return 0


def cmd_clean(args) -> int:
    outputs = Path(args.outputs) / "runs"
    if not outputs.exists():
        print("Nothing to clean.")
        return 0
    targets: list[Path] = []
    for symbol_dir in outputs.iterdir():
        if not symbol_dir.is_dir():
            continue
        for run_dir in symbol_dir.iterdir():
            if not run_dir.is_dir():
                continue
            if args.failed:
                summary = run_dir / "summary.json"
                if summary.exists():
                    continue  # has a summary -> treat as completed
            targets.append(run_dir)
    if not targets:
        print("Nothing matched.")
        return 0
    for t in targets:
        print(("DRY-RUN remove " if args.dry_run else "removing ") + str(t))
    if args.dry_run:
        print(f"\n{len(targets)} run(s) would be removed. Re-run without --dry-run to delete.")
        return 0
    import shutil
    for t in targets:
        shutil.rmtree(t, ignore_errors=True)
    print(f"Removed {len(targets)} run(s).")
    return 0


# ── parser ────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="janus",
        description="Janus market-data validation — progressive CLI. "
                    "Core path: import once, run many times.",
    )
    parser.add_argument("--registry", default=str(registry.DEFAULT_REGISTRY_PATH),
                        help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    p_imp = sub.add_parser("import", help="register an external data file for a symbol")
    p_imp.add_argument("symbol")
    p_imp.add_argument("file")
    p_imp.add_argument("--provider", default="settlement")
    p_imp.set_defaults(func=cmd_import)

    p_run = sub.add_parser("run", help="run the full pipeline for a symbol")
    p_run.add_argument("symbol")
    _add_date_args(p_run)
    _add_run_opts(p_run)
    p_run.add_argument("--name", help="run id (default: <symbol>_<timestamp>)")
    p_run.set_defaults(func=cmd_run)

    p_exp = sub.add_parser("explain", help="print the resolved plan without running")
    p_exp.add_argument("symbol")
    _add_date_args(p_exp)
    _add_run_opts(p_exp)
    p_exp.set_defaults(func=cmd_explain)

    p_doc = sub.add_parser("doctor", help="readiness checks for a symbol")
    p_doc.add_argument("symbol")
    p_doc.set_defaults(func=cmd_doctor)

    p_list = sub.add_parser("list", help="list known profiles and readiness")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="summarize a completed run")
    p_show.add_argument("run_id")
    p_show.add_argument("--outputs", default="outputs")
    p_show.set_defaults(func=cmd_show)

    p_data = sub.add_parser("data", help="manage the data-source registry")
    data_sub = p_data.add_subparsers(dest="data_cmd", required=True)
    d_imp = data_sub.add_parser("import")
    d_imp.add_argument("--ticker", required=True)
    d_imp.add_argument("--file", required=True)
    d_imp.add_argument("--provider", default="settlement")
    d_imp.add_argument("--use", action="store_true")
    d_list = data_sub.add_parser("list")
    d_list.add_argument("ticker")
    d_use = data_sub.add_parser("use")
    d_use.add_argument("ticker")
    d_use.add_argument("source_id")
    p_data.set_defaults(func=cmd_data)

    p_clean = sub.add_parser("clean", help="prune generated run outputs")
    p_clean.add_argument("--failed", action="store_true", help="only runs without a summary.json")
    p_clean.add_argument("--older-than", dest="older_than", help="(reserved) age filter e.g. 30d")
    p_clean.add_argument("--dry-run", action="store_true")
    p_clean.add_argument("--outputs", default="outputs")
    p_clean.set_defaults(func=cmd_clean)

    return parser


def main(argv=None) -> int:
    # Windows consoles default to cp1252; force UTF-8 so any stray glyph in
    # help text or detail strings prints instead of raising UnicodeEncodeError.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
