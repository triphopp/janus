"""Ticker/symbol -> instrument config resolution for the user-facing CLI.

Mirrors ``run_pipeline.load_config`` but lives in the CLI package so the facade
can resolve a profile without importing the heavy pipeline module. Resolution is
case-insensitive on the config file name and falls back to a synthesized equity
profile for a bare ticker.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from core.config import normalize_config

__all__ = [
    "ResolveError",
    "resolve_profile",
    "is_file_backed",
    "symbol_label",
    "profile_summary",
    "known_profiles",
]

CONFIG_DIR = Path("configs/instruments")
FAMILY_DIR = Path("configs")


class ResolveError(ValueError):
    """Raised when a symbol cannot be resolved to a usable profile."""


def _find_config_file(symbol: str) -> Path | None:
    """Case-insensitive lookup of configs/instruments/<symbol>.yaml."""
    direct = CONFIG_DIR / f"{symbol}.yaml"
    if direct.exists():
        return direct
    lowered = symbol.lower()
    if CONFIG_DIR.exists():
        for candidate in CONFIG_DIR.glob("*.yaml"):
            if candidate.stem.lower() == lowered:
                return candidate
    return None


def resolve_profile(symbol: str, *, config_dir: Path | None = None) -> dict:
    """Resolve a ticker/symbol to a normalized instrument config.

    A YAML under configs/instruments wins; otherwise the symbol is treated as an
    equity ticker and a default equity profile is synthesized from
    configs/equity.yaml.
    """
    global CONFIG_DIR
    if config_dir is not None:
        CONFIG_DIR = Path(config_dir)

    if not symbol or not symbol.strip():
        raise ResolveError("symbol is required")
    symbol = symbol.strip()

    inst_path = _find_config_file(symbol)
    if inst_path is not None:
        with open(inst_path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        cfg["_profile_name"] = inst_path.stem
        cfg["_synthesized"] = False
    else:
        cfg = {
            "family": "equity",
            "provider": "yfinance",
            "symbol": {"ticker": symbol.upper()},
            "_profile_name": symbol.upper(),
            "_synthesized": True,
        }

    family = cfg.get("family", "equity")
    family_path = FAMILY_DIR / f"{family}.yaml"
    if family_path.exists():
        with open(family_path, encoding="utf-8") as fh:
            defaults = yaml.safe_load(fh) or {}
        for k, v in defaults.items():
            cfg.setdefault(k, v)

    return normalize_config(cfg)


def is_file_backed(cfg: dict) -> bool:
    """True when the profile reads a local settlement file (not a live provider)."""
    return cfg.get("provider", "settlement") == "settlement"


def symbol_label(cfg: dict) -> str:
    sym = cfg.get("symbol", {}) or {}
    return sym.get("ticker") or str(sym.get("hub") or sym.get("product_id") or cfg.get("_profile_name", "unknown"))


def profile_summary(cfg: dict) -> dict:
    return {
        "profile": cfg.get("_profile_name", "unknown"),
        "family": cfg.get("family", "equity"),
        "provider": cfg.get("provider", "settlement"),
        "symbol": symbol_label(cfg),
        "file_backed": is_file_backed(cfg),
        "synthesized": bool(cfg.get("_synthesized", False)),
    }


def known_profiles(*, config_dir: Path | None = None) -> list[str]:
    cdir = Path(config_dir) if config_dir is not None else CONFIG_DIR
    if not cdir.exists():
        return []
    names = []
    for candidate in sorted(cdir.glob("*.yaml")):
        if candidate.name.endswith(".example"):
            continue
        names.append(candidate.stem)
    return names
