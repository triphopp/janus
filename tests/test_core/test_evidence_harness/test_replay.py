"""Tests for replay mode — reproducibility contract."""

import json
from pathlib import Path

import pytest

from core.evidence_harness.schema import OutlierCasePackage
from core.evidence_harness.config import load_harness_config
from core.evidence_harness.controller import run_harness
from core.evidence_harness.replay import run_replay, verify_replay
from core.evidence_harness.cache import ReplayCacheMiss
from core.evidence_harness.fetch import FixtureFetchProvider
from core.evidence_harness.search import FixtureSearchProvider

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "evidence_harness"


def _search_provider():
    return FixtureSearchProvider(fixture_dir=str(FIXTURES / "search"))


def _fetch_provider():
    return FixtureFetchProvider(fixture_dir=str(FIXTURES / "pages"))


def _wti_case() -> OutlierCasePackage:
    data = json.loads((FIXTURES / "cases" / "wti_inventory_supported.json").read_text())
    return OutlierCasePackage.from_dict(data)


class TestReplayMode:
    def test_replay_does_not_call_live_provider(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path / "original")
        cfg.cache_dir = str(tmp_path / "cache")

        original = run_harness(
            _wti_case(), cfg,
            search_provider=_search_provider(),
            fetch_provider=_fetch_provider(),
        )
        manifest_path = Path(original.artifact_paths["replay_manifest"])

        # replay must not call any live provider — ReplayCacheMiss would surface
        replay = run_replay(manifest_path, artifact_dir_override=str(tmp_path / "replay"))
        assert replay.verdict is not None

    def test_replay_produces_same_verdict(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path / "original")
        cfg.cache_dir = str(tmp_path / "cache")

        original = run_harness(
            _wti_case(), cfg,
            search_provider=_search_provider(),
            fetch_provider=_fetch_provider(),
        )
        manifest_path = Path(original.artifact_paths["replay_manifest"])
        replay = run_replay(manifest_path, artifact_dir_override=str(tmp_path / "replay"))

        assert replay.verdict == original.verdict

    def test_replay_produces_same_document_ids(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path / "original")
        cfg.cache_dir = str(tmp_path / "cache")

        original = run_harness(
            _wti_case(), cfg,
            search_provider=_search_provider(),
            fetch_provider=_fetch_provider(),
        )
        manifest_path = Path(original.artifact_paths["replay_manifest"])
        replay = run_replay(manifest_path, artifact_dir_override=str(tmp_path / "replay"))

        orig_docs = {d.document_id for d in original.documents}
        replay_docs = {d.document_id for d in replay.documents}
        assert orig_docs == replay_docs

    def test_replay_produces_same_query_ids(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path / "original")
        cfg.cache_dir = str(tmp_path / "cache")

        original = run_harness(
            _wti_case(), cfg,
            search_provider=_search_provider(),
            fetch_provider=_fetch_provider(),
        )
        manifest_path = Path(original.artifact_paths["replay_manifest"])
        replay = run_replay(manifest_path, artifact_dir_override=str(tmp_path / "replay"))

        assert [q.query_id for q in original.queries] == [q.query_id for q in replay.queries]

    def test_replay_writes_all_required_artifacts(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path / "original")
        cfg.cache_dir = str(tmp_path / "cache")

        original = run_harness(
            _wti_case(), cfg,
            search_provider=_search_provider(),
            fetch_provider=_fetch_provider(),
        )
        manifest_path = Path(original.artifact_paths["replay_manifest"])
        replay = run_replay(manifest_path, artifact_dir_override=str(tmp_path / "replay"))

        required = ["verdict", "case_package", "query_log", "sources", "checks"]
        for key in required:
            assert key in replay.artifact_paths
            assert Path(replay.artifact_paths[key]).exists()

    def test_replay_manifest_has_cache_entries(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path / "original")
        cfg.cache_dir = str(tmp_path / "cache")

        original = run_harness(
            _wti_case(), cfg,
            search_provider=_search_provider(),
            fetch_provider=_fetch_provider(),
        )
        manifest_path = Path(original.artifact_paths["replay_manifest"])
        manifest = json.loads(manifest_path.read_text())
        assert isinstance(manifest["cache_entries"], list)

    def test_verify_replay_passes_when_verdicts_match(self, tmp_path):
        cfg = load_harness_config()
        cfg.artifact_dir = str(tmp_path / "original")
        cfg.cache_dir = str(tmp_path / "cache")

        original = run_harness(
            _wti_case(), cfg,
            search_provider=_search_provider(),
            fetch_provider=_fetch_provider(),
        )
        manifest_path = Path(original.artifact_paths["replay_manifest"])
        replay = run_replay(manifest_path, artifact_dir_override=str(tmp_path / "replay"))

        report = verify_replay(original, replay)
        assert report["status"] == "pass"
        assert report["issues"] == []

    def test_replay_raises_on_missing_manifest(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            run_replay(tmp_path / "nonexistent_manifest.json")

    def test_replay_raises_on_missing_case_package(self, tmp_path):
        manifest = {
            "harness_run_id": "h", "case_id": "c", "run_id": "r",
            "config_hash": "sha256:x", "planner_version": "v1",
            "extractor_version": "v1", "scoring_version": "v1",
            "created_at": "2024-01-01T00:00:00Z", "cache_entries": [],
        }
        manifest_path = tmp_path / "replay_manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        # case_package.json is missing
        with pytest.raises(FileNotFoundError, match="case_package"):
            run_replay(manifest_path)


class TestReplaySearchProvider:
    def test_replay_mode_does_not_call_live_provider(self, tmp_path):
        from core.evidence_harness.cache import HarnessCache, ReplaySearchProvider
        from core.evidence_harness.ids import query_id as make_qid
        from core.evidence_harness.schema import SearchQuery

        cache = HarnessCache(str(tmp_path))
        q = SearchQuery(
            query_id=make_qid("c", "WTI price", None, None, []),
            case_id="c", text="WTI price",
        )
        cache.save_search(q, [])

        provider = ReplaySearchProvider(cache)
        results = provider.search(q)
        assert results == []
