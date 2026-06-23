"""HarnessConfig — load and validate evidence search configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class HarnessConfig:
    enabled: bool = False
    mode: str = "mock"                    # mock | replay | live
    search_provider: str = "fixture"      # fixture | searxng | api
    fetch_provider: str = "fixture"       # fixture | httpx
    extractor: str = "readability"

    artifact_dir: str = "outputs/evidence/harness"
    cache_dir: str = "outputs/evidence/cache"

    max_queries: int = 8
    max_results_per_query: int = 10
    max_fetches: int = 20
    max_iterations: int = 4
    max_runtime_sec: int = 120
    max_page_bytes: int = 2_000_000
    request_timeout_sec: int = 15
    min_delay_ms_per_domain: int = 1000

    allowed_schemes: list[str] = field(default_factory=lambda: ["https"])
    allow_domains: list[str] = field(default_factory=list)
    deny_domains: list[str] = field(default_factory=list)

    llm_enabled: bool = False
    llm_provider: str = "mock"          # mock | ollama | openai_compat
    llm_model: str = "mock-v1"
    llm_base_url: str = "http://localhost:11434"
    llm_api_key: str | None = None
    llm_timeout_sec: int = 60
    llm_max_tokens: int = 1000
    llm_temperature: float = 0.0
    llm_prompt_version: str = "evidence_harness.prompts.v1"

    source_tiers: dict = field(default_factory=dict)
    query_terms: dict = field(default_factory=dict)

    def validate(self) -> None:
        allowed_modes = {"mock", "replay", "live"}
        if self.mode not in allowed_modes:
            raise ValueError(f"HarnessConfig.mode must be one of {allowed_modes}, got {self.mode!r}")
        allowed_search = {"fixture", "duckduckgo"}
        if self.search_provider not in allowed_search:
            raise ValueError(f"search_provider must be one of {allowed_search}, got {self.search_provider!r}")
        allowed_fetch = {"fixture", "httpx"}
        if self.fetch_provider not in allowed_fetch:
            raise ValueError(f"fetch_provider must be one of {allowed_fetch}, got {self.fetch_provider!r}")
        if self.max_queries < 1:
            raise ValueError("max_queries must be >= 1")
        if self.max_fetches < 0:
            raise ValueError("max_fetches must be >= 0")


_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "mode": "mock",
    "search_provider": "fixture",
    "fetch_provider": "fixture",
    "extractor": "readability",
    "artifact_dir": "outputs/evidence/harness",
    "cache_dir": "outputs/evidence/cache",
    "max_queries": 8,
    "max_results_per_query": 10,
    "max_fetches": 20,
    "max_iterations": 4,
    "max_runtime_sec": 120,
    "max_page_bytes": 2_000_000,
    "request_timeout_sec": 15,
    "min_delay_ms_per_domain": 1000,
    "allowed_schemes": ["https"],
    "allow_domains": [],
    "deny_domains": [],
    "llm_enabled": False,
    "llm_provider": "mock",
    "llm_model": "mock-v1",
    "llm_base_url": "http://localhost:11434",
    "llm_api_key": None,
    "llm_timeout_sec": 60,
    "llm_max_tokens": 1000,
    "llm_temperature": 0.0,
    "llm_prompt_version": "evidence_harness.prompts.v1",
    "source_tiers": {
        "tier1_official": ["sec.gov", "eia.gov", "cmegroup.com", "nasdaqtrader.com"],
        "tier2_reputable": ["reuters.com", "bloomberg.com", "wsj.com", "marketwatch.com"],
        "tier4_social": ["reddit.com", "x.com", "twitter.com"],
    },
    "query_terms": {
        "return_outlier": {
            "high": ["rise", "jump", "surge", "rally", "beat", "upgrade", "approval"],
            "low": ["fall", "drop", "plunge", "selloff", "miss", "downgrade", "guidance cut"],
        },
        "vol_surface_cluster": {
            "high": ["implied volatility spike", "options volume", "skew", "event volatility"],
        },
    },
}


def load_harness_config(path: str | Path | None = None) -> HarnessConfig:
    """Load HarnessConfig from YAML, falling back to defaults.

    String values of the form ``${ENV_VAR}`` or ``${ENV_VAR:-default}`` are
    expanded from the process environment before the config is validated.
    """
    raw: dict[str, Any] = dict(_DEFAULTS)

    if path is not None:
        p = Path(path)
        if p.exists():
            with open(p) as f:
                file_data = yaml.safe_load(f) or {}
            section = file_data.get("evidence_search", file_data)
            _deep_merge(raw, section)

    _interpolate_env(raw)
    fields = HarnessConfig.__dataclass_fields__
    kwargs = {k: raw[k] for k in fields if k in raw}
    cfg = HarnessConfig(**kwargs)
    cfg.validate()
    return cfg


def _deep_merge(base: dict, override: dict) -> None:
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


import os as _os
import re as _re

_ENV_RE = _re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _interpolate_env(obj: Any) -> None:
    """Recursively expand ``${VAR}`` / ``${VAR:-default}`` in string values."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                obj[k] = _expand(v)
            else:
                _interpolate_env(v)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str):
                obj[i] = _expand(item)
            else:
                _interpolate_env(item)


def _expand(s: str) -> str:
    def _sub(m: _re.Match) -> str:
        var, default = m.group(1), m.group(2)
        return _os.environ.get(var, default if default is not None else m.group(0))
    return _ENV_RE.sub(_sub, s)
