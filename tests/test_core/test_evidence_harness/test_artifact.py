"""Tests for ArtifactWriter."""

import json
from pathlib import Path

import pytest

from core.evidence_harness.artifact import ArtifactWriter
from core.evidence_harness.schema import (
    OutlierCasePackage,
    SearchQuery,
    FetchResult,
    SourceRegistryRecord,
    EvidenceClaim,
)


def _make_case() -> OutlierCasePackage:
    return OutlierCasePackage(
        case_id="case_test", run_id="run_test",
        signal_type="return_outlier", as_of_date="2024-01-25",
    )


class TestArtifactWriter:
    def test_creates_run_directory(self, tmp_path):
        writer = ArtifactWriter(str(tmp_path), "run_1", "case_1", "hrn_1")
        assert (tmp_path / "run_1" / "case_1" / "hrn_1").is_dir()

    def test_writes_case_package_json(self, tmp_path):
        writer = ArtifactWriter(str(tmp_path), "run_1", "case_1", "hrn_1")
        writer.write_case_package(_make_case())
        p = tmp_path / "run_1" / "case_1" / "hrn_1" / "case_package.json"
        assert p.exists()
        data = json.loads(p.read_text())
        assert data["case_id"] == "case_test"

    def test_writes_verdict_json(self, tmp_path):
        writer = ArtifactWriter(str(tmp_path), "run_1", "case_1", "hrn_1")
        verdict = {
            "schema_version": "evidence.verdict.v1",
            "verdict": "insufficient_evidence",
            "deterministic_verdict": "insufficient_evidence",
        }
        writer.write_verdict(verdict)
        p = tmp_path / "run_1" / "case_1" / "hrn_1" / "verdict.json"
        assert p.exists()
        data = json.loads(p.read_text())
        assert data["verdict"] == "insufficient_evidence"

    def test_writes_query_log_jsonl(self, tmp_path):
        writer = ArtifactWriter(str(tmp_path), "r", "c", "h")
        queries = [
            SearchQuery(query_id="q1", case_id="c", text="WTI price move 2024-09-25"),
            SearchQuery(query_id="q2", case_id="c", text="WTI inventory 2024-09-25"),
        ]
        writer.write_query_log(queries)
        p = tmp_path / "r" / "c" / "h" / "query_log.jsonl"
        lines = [l for l in p.read_text().strip().split("\n") if l]
        assert len(lines) == 2
        assert json.loads(lines[0])["query_id"] == "q1"

    def test_artifact_paths_tracks_written_files(self, tmp_path):
        writer = ArtifactWriter(str(tmp_path), "r", "c", "h")
        writer.write_case_package(_make_case())
        writer.write_verdict({"verdict": "test"})
        paths = writer.artifact_paths()
        assert "case_package" in paths
        assert "verdict" in paths

    def test_all_required_artifacts_schema(self, tmp_path):
        writer = ArtifactWriter(str(tmp_path), "r", "c", "h")
        writer.write_case_package(_make_case())
        writer.write_config({"mode": "mock"})
        writer.write_query_log([])
        writer.write_search_results([])
        writer.write_fetch_log([])
        writer.write_sources([])
        writer.write_claims([])
        writer.write_checks([])
        writer.write_citation_report({"status": "skip"})
        writer.write_verdict({"schema_version": "evidence.verdict.v1", "verdict": "insufficient_evidence"})
        writer.write_replay_manifest({"harness_run_id": "h"})

        required = [
            "case_package.json", "config.json", "query_log.jsonl",
            "search_results.jsonl", "fetch_log.jsonl", "sources.jsonl",
            "claims.jsonl", "checks.jsonl", "citation_report.json",
            "verdict.json", "replay_manifest.json",
        ]
        run_dir = tmp_path / "r" / "c" / "h"
        for name in required:
            assert (run_dir / name).exists(), f"missing artifact: {name}"

    def test_all_json_artifacts_are_valid_json(self, tmp_path):
        writer = ArtifactWriter(str(tmp_path), "r", "c", "h")
        writer.write_case_package(_make_case())
        writer.write_config({"mode": "mock"})
        writer.write_citation_report({"status": "skip"})
        writer.write_verdict({"verdict": "insufficient_evidence"})
        writer.write_replay_manifest({"harness_run_id": "h"})

        run_dir = tmp_path / "r" / "c" / "h"
        for p in run_dir.glob("*.json"):
            json.loads(p.read_text())  # must not raise
