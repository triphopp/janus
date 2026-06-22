"""ArtifactWriter — writes all required harness artifacts to disk.

Layout per run::

    <artifact_dir>/<run_id>/<case_id>/<harness_run_id>/
        case_package.json
        config.json
        query_log.jsonl
        search_results.jsonl
        fetch_log.jsonl
        sources.jsonl
        claims.jsonl
        checks.jsonl
        citation_report.json
        verdict.json
        replay_manifest.json
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .schema import (
    OutlierCasePackage,
    HarnessRunResult,
    SearchQuery,
    SearchResult,
    FetchResult,
    ExtractedDocument,
    EvidenceClaim,
    SourceRegistryRecord,
    to_json_safe,
)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(to_json_safe(r), ensure_ascii=False) for r in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


class ArtifactWriter:
    def __init__(self, artifact_dir: str, run_id: str, case_id: str, harness_run_id: str) -> None:
        self.run_dir = Path(artifact_dir) / run_id / case_id / harness_run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._paths: dict[str, str] = {}

    def write_case_package(self, pkg: OutlierCasePackage) -> None:
        p = self.run_dir / "case_package.json"
        _write_json(p, to_json_safe(pkg))
        self._paths["case_package"] = str(p)

    def write_config(self, cfg_dict: dict) -> None:
        p = self.run_dir / "config.json"
        _write_json(p, cfg_dict)
        self._paths["config"] = str(p)

    def write_query_log(self, queries: list[SearchQuery]) -> None:
        p = self.run_dir / "query_log.jsonl"
        _write_jsonl(p, queries)
        self._paths["query_log"] = str(p)

    def write_search_results(self, results: list[SearchResult]) -> None:
        p = self.run_dir / "search_results.jsonl"
        _write_jsonl(p, results)
        self._paths["search_results"] = str(p)

    def write_fetch_log(self, fetched: list[FetchResult]) -> None:
        p = self.run_dir / "fetch_log.jsonl"
        _write_jsonl(p, fetched)
        self._paths["fetch_log"] = str(p)

    def write_sources(self, sources: list[SourceRegistryRecord]) -> None:
        p = self.run_dir / "sources.jsonl"
        _write_jsonl(p, sources)
        self._paths["sources"] = str(p)

    def write_claims(self, claims: list[EvidenceClaim]) -> None:
        p = self.run_dir / "claims.jsonl"
        _write_jsonl(p, claims)
        self._paths["claims"] = str(p)

    def write_checks(self, checks: list[dict]) -> None:
        p = self.run_dir / "checks.jsonl"
        _write_jsonl(p, checks)
        self._paths["checks"] = str(p)

    def write_citation_report(self, report: dict) -> None:
        p = self.run_dir / "citation_report.json"
        _write_json(p, report)
        self._paths["citation_report"] = str(p)

    def write_verdict(self, verdict: dict) -> None:
        p = self.run_dir / "verdict.json"
        _write_json(p, verdict)
        self._paths["verdict"] = str(p)

    def write_replay_manifest(self, manifest: dict) -> None:
        p = self.run_dir / "replay_manifest.json"
        _write_json(p, manifest)
        self._paths["replay_manifest"] = str(p)

    def artifact_paths(self) -> dict[str, str]:
        return dict(self._paths)

    def relative_paths(self) -> dict[str, str]:
        return {k: Path(v).name for k, v in self._paths.items()}
