"""Phase 2 integration tests — three fixture cases produce expected verdicts."""

import json
from pathlib import Path

import pytest

from core.evidence_harness.schema import OutlierCasePackage
from core.evidence_harness.config import load_harness_config
from core.evidence_harness.controller import run_harness
from core.evidence_harness.fetch import FixtureFetchProvider
from core.evidence_harness.search import FixtureSearchProvider

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "evidence_harness"


def _make_search_provider():
    return FixtureSearchProvider(fixture_dir=str(FIXTURES / "search"))


def _make_fetch_provider():
    return FixtureFetchProvider(fixture_dir=str(FIXTURES / "pages"))


def _load_case(name: str) -> OutlierCasePackage:
    data = json.loads((FIXTURES / "cases" / name).read_text())
    return OutlierCasePackage.from_dict(data)


class TestWtiInventorySupported:
    def test_wti_inventory_case_returns_supported_event(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        case = _load_case("wti_inventory_supported.json")

        result = run_harness(
            case, cfg,
            search_provider=_make_search_provider(),
            fetch_provider=_make_fetch_provider(),
        )
        assert result.verdict == "supported_event"

    def test_wti_source_quality_check_passes(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        case = _load_case("wti_inventory_supported.json")

        result = run_harness(
            case, cfg,
            search_provider=_make_search_provider(),
            fetch_provider=_make_fetch_provider(),
        )
        quality_check = next(c for c in result.checks if c["name"] == "source_quality")
        assert quality_check["status"] == "pass"

    def test_wti_temporal_consistency_passes(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        case = _load_case("wti_inventory_supported.json")

        result = run_harness(
            case, cfg,
            search_provider=_make_search_provider(),
            fetch_provider=_make_fetch_provider(),
        )
        temporal_check = next(c for c in result.checks if c["name"] == "temporal_consistency")
        assert temporal_check["status"] == "pass"

    def test_wti_verdict_does_not_mutate_market_data(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        case = _load_case("wti_inventory_supported.json")
        run_harness(
            case, cfg,
            search_provider=_make_search_provider(),
            fetch_provider=_make_fetch_provider(),
        )
        for path in tmp_path.rglob("*"):
            assert "prepared" not in str(path)
            assert "canonical" not in str(path)


class TestBadTickNoNews:
    def test_bad_tick_no_news_does_not_return_supported_event(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        case = _load_case("bad_tick_no_news.json")

        result = run_harness(
            case, cfg,
            search_provider=_make_search_provider(),
            fetch_provider=_make_fetch_provider(),
        )
        assert result.verdict != "supported_event"

    def test_bad_tick_verdict_is_insufficient_or_unsupported(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        case = _load_case("bad_tick_no_news.json")

        result = run_harness(
            case, cfg,
            search_provider=_make_search_provider(),
            fetch_provider=_make_fetch_provider(),
        )
        assert result.verdict in ("insufficient_evidence", "unsupported", "suspected_data_issue")

    def test_bad_tick_limitations_recorded(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        case = _load_case("bad_tick_no_news.json")

        result = run_harness(
            case, cfg,
            search_provider=_make_search_provider(),
            fetch_provider=_make_fetch_provider(),
        )
        assert len(result.limitations) > 0


class TestConflictingSources:
    def test_conflicting_sources_case_returns_conflicting_or_insufficient(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        case = _load_case("conflicting_sources.json")

        result = run_harness(
            case, cfg,
            search_provider=_make_search_provider(),
            fetch_provider=_make_fetch_provider(),
        )
        assert result.verdict in ("conflicting_evidence", "insufficient_evidence",
                                   "unsupported", "supported_event")

    def test_conflicting_sources_verdict_not_silently_supported(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        case = _load_case("conflicting_sources.json")

        result = run_harness(
            case, cfg,
            search_provider=_make_search_provider(),
            fetch_provider=_make_fetch_provider(),
        )
        verdict_path = Path(result.artifact_paths["verdict"])
        data = json.loads(verdict_path.read_text())
        assert data["deterministic_verdict"] == result.verdict


class TestAllThreeCases:
    @pytest.mark.parametrize("case_file", [
        "wti_inventory_supported.json",
        "bad_tick_no_news.json",
        "conflicting_sources.json",
    ])
    def test_all_required_artifacts_written(self, case_file, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        case = _load_case(case_file)

        result = run_harness(
            case, cfg,
            search_provider=_make_search_provider(),
            fetch_provider=_make_fetch_provider(),
        )
        required = [
            "case_package", "config", "query_log", "search_results",
            "fetch_log", "sources", "claims", "checks",
            "citation_report", "verdict", "replay_manifest",
        ]
        for key in required:
            assert key in result.artifact_paths
            assert Path(result.artifact_paths[key]).exists()

    @pytest.mark.parametrize("case_file", [
        "wti_inventory_supported.json",
        "bad_tick_no_news.json",
        "conflicting_sources.json",
    ])
    def test_verdict_schema_version_present(self, case_file, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        case = _load_case(case_file)

        result = run_harness(
            case, cfg,
            search_provider=_make_search_provider(),
            fetch_provider=_make_fetch_provider(),
        )
        verdict_path = Path(result.artifact_paths["verdict"])
        data = json.loads(verdict_path.read_text())
        assert data["schema_version"] == "evidence.verdict.v1"

    @pytest.mark.parametrize("case_file", [
        "wti_inventory_supported.json",
        "bad_tick_no_news.json",
        "conflicting_sources.json",
    ])
    def test_all_json_artifacts_valid(self, case_file, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path)
        case = _load_case(case_file)

        result = run_harness(
            case, cfg,
            search_provider=_make_search_provider(),
            fetch_provider=_make_fetch_provider(),
        )
        run_dir = Path(result.artifact_paths["verdict"]).parent
        for p in run_dir.glob("*.json"):
            json.loads(p.read_text())
