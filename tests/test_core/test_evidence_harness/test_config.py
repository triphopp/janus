"""Tests for HarnessConfig loading and validation."""

import pytest
import tempfile
import yaml
from pathlib import Path

from core.evidence_harness.config import HarnessConfig, load_harness_config


class TestHarnessConfigDefaults:
    def test_load_no_path_returns_defaults(self):
        cfg = load_harness_config()
        assert cfg.enabled is False
        assert cfg.mode == "mock"
        assert cfg.search_provider == "fixture"
        assert cfg.fetch_provider == "fixture"
        assert cfg.max_queries == 8
        assert cfg.llm_enabled is False

    def test_allowed_schemes_default(self):
        cfg = load_harness_config()
        assert "https" in cfg.allowed_schemes

    def test_source_tiers_present(self):
        cfg = load_harness_config()
        assert "tier1_official" in cfg.source_tiers
        assert "eia.gov" in cfg.source_tiers["tier1_official"]


class TestHarnessConfigValidation:
    def test_invalid_mode_raises(self):
        cfg = HarnessConfig(mode="browser")
        with pytest.raises(ValueError, match="mode"):
            cfg.validate()

    def test_zero_max_queries_raises(self):
        cfg = HarnessConfig(max_queries=0)
        with pytest.raises(ValueError, match="max_queries"):
            cfg.validate()


class TestLoadFromYaml:
    def test_load_overrides_defaults(self):
        data = {"evidence_search": {"max_queries": 3, "mode": "replay"}}
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            yaml.dump(data, f)
            path = f.name
        cfg = load_harness_config(path)
        assert cfg.max_queries == 3
        assert cfg.mode == "replay"

    def test_missing_file_uses_defaults(self):
        cfg = load_harness_config("/nonexistent/path/evidence_search.yaml")
        assert cfg.mode == "mock"

    def test_partial_yaml_merges_with_defaults(self):
        data = {"evidence_search": {"max_fetches": 5}}
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            yaml.dump(data, f)
            path = f.name
        cfg = load_harness_config(path)
        assert cfg.max_fetches == 5
        assert cfg.max_queries == 8  # default untouched
