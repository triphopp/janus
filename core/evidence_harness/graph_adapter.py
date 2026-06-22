"""EvidenceGraphSink — write a HarnessRunResult to a graph backend.

Sinks:
  JsonGraphSink     — writes JSON snapshot (always available, no DB required)
  PostgresGraphSink — writes to PostgreSQL via graph_store (import-guarded)

Factory:
  make_graph_sink(config) -> EvidenceGraphSink
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, runtime_checkable

from .schema import HarnessRunResult, to_json_safe


@runtime_checkable
class EvidenceGraphSink(Protocol):
    def write(self, result: HarnessRunResult) -> None: ...
    def close(self) -> None: ...


# ── JsonGraphSink ─────────────────────────────────────────────────────────────

class JsonGraphSink:
    """Write one JSON snapshot per case run to artifact_dir.

    Path: <graph_dir>/<run_id>/<case_id>.json
    """

    name: str = "json"

    def __init__(self, graph_dir: str = "outputs/evidence/graph") -> None:
        self._graph_dir = Path(graph_dir)

    def write(self, result: HarnessRunResult) -> None:
        from .graph_builder import build_graph
        payload = build_graph(result)
        out_dir = self._graph_dir / result.run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{result.case_id}.json"
        out_path.write_text(
            json.dumps(to_json_safe(payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._last_path = str(out_path)

    def last_path(self) -> str | None:
        return getattr(self, "_last_path", None)

    def close(self) -> None:
        pass


# ── NullGraphSink ─────────────────────────────────────────────────────────────

class NullGraphSink:
    """No-op sink — used when graph writing is disabled."""

    name: str = "null"

    def write(self, result: HarnessRunResult) -> None:
        pass

    def close(self) -> None:
        pass


# ── PostgresGraphSink ─────────────────────────────────────────────────────────

class PostgresGraphSink:
    """Write graph rows to PostgreSQL via PostgresGraphStore.

    Also writes a JSON snapshot alongside the DB write for durability.
    Requires JANUS_EVIDENCE_DATABASE_URL or explicit dsn.
    """

    name: str = "postgres"

    def __init__(
        self,
        dsn: str | None = None,
        *,
        graph_dir: str = "outputs/evidence/graph",
        write_json_snapshot: bool = True,
    ) -> None:
        from .graph_store import PostgresGraphStore
        self._store = PostgresGraphStore(dsn=dsn)
        self._json_sink = JsonGraphSink(graph_dir) if write_json_snapshot else None

    def write(self, result: HarnessRunResult) -> None:
        from .graph_builder import build_graph
        payload = build_graph(result)
        self._store.upsert_case(payload["case"])
        for src in payload["sources"]:
            self._store.upsert_source(src)
        for node in payload["nodes"]:
            self._store.add_node(node)
        for edge in payload["edges"]:
            self._store.add_edge(edge)
        for chk in payload["checks"]:
            self._store.add_check(chk)
        for q in payload["queries"]:
            self._store.add_search_query(q)
        self._store.append_event(
            result.case_id,
            actor="harness",
            action="run_completed",
            payload={"verdict": result.verdict, "harness_run_id": result.harness_run_id},
        )
        if self._json_sink:
            self._json_sink.write(result)

    def close(self) -> None:
        self._store.close()
        if self._json_sink:
            self._json_sink.close()


# ── Factory ───────────────────────────────────────────────────────────────────

def make_graph_sink(
    backend: str = "null",
    *,
    dsn: str | None = None,
    graph_dir: str = "outputs/evidence/graph",
    write_json_snapshot: bool = True,
) -> EvidenceGraphSink:
    """Build and return the configured sink.

    backend: "null" | "json" | "postgres"
    """
    if backend == "json":
        return JsonGraphSink(graph_dir)
    if backend == "postgres":
        return PostgresGraphSink(
            dsn=dsn, graph_dir=graph_dir,
            write_json_snapshot=write_json_snapshot,
        )
    return NullGraphSink()
