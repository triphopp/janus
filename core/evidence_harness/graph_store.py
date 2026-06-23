"""PostgresGraphStore — low-level repository for evidence graph tables.

Uses psycopg2. Import-guarded: only loaded when postgres backend is configured.
DSN is read from the `dsn` argument or the JANUS_EVIDENCE_DATABASE_URL env var.

All writes use upsert (ON CONFLICT DO UPDATE) so re-running the harness is safe.
"""

from __future__ import annotations

import json
import os
from typing import Any


_MIGRATIONS_DIR = None  # set by run_migrations()


class PostgresGraphStore:
    def __init__(self, dsn: str | None = None) -> None:
        import psycopg2
        import psycopg2.extras
        resolved = dsn or os.environ.get("JANUS_EVIDENCE_DATABASE_URL")
        if not resolved:
            raise ValueError(
                "PostgresGraphStore requires a DSN. "
                "Pass dsn= or set JANUS_EVIDENCE_DATABASE_URL."
            )
        self._conn = psycopg2.connect(resolved)
        self._conn.autocommit = False

    # ── Case ──────────────────────────────────────────────────────────────────

    def upsert_case(self, case: dict) -> None:
        sql = """
            insert into evidence_cases
              (case_id, run_id, instrument, family, as_of_date, signal_type,
               severity, status, verdict, confidence, event_type, artifact_path, payload)
            values
              (%(case_id)s, %(run_id)s, %(instrument)s, %(family)s,
               %(as_of_date)s, %(signal_type)s, %(severity)s,
               %(status)s, %(verdict)s, %(confidence)s, %(event_type)s,
               %(artifact_path)s, %(payload)s::jsonb)
            on conflict (case_id) do update set
              verdict       = excluded.verdict,
              confidence    = excluded.confidence,
              event_type    = excluded.event_type,
              artifact_path = excluded.artifact_path,
              payload       = excluded.payload,
              updated_at    = now()
        """
        self._execute(sql, _jsonb_encode(case, ["payload"]))

    # ── Source ────────────────────────────────────────────────────────────────

    def upsert_source(self, source: dict) -> None:
        sql = """
            insert into evidence_sources
              (source_id, url, canonical_url, domain, source_tier, title,
               published_at, fetched_at, accessed_at, content_hash,
               extracted_text, summary, payload)
            values
              (%(source_id)s, %(url)s, %(canonical_url)s, %(domain)s,
               %(source_tier)s, %(title)s, %(published_at)s, %(fetched_at)s,
               %(accessed_at)s, %(content_hash)s, %(extracted_text)s,
               %(summary)s, %(payload)s::jsonb)
            on conflict (source_id) do update set
              title          = excluded.title,
              extracted_text = excluded.extracted_text,
              summary        = excluded.summary,
              payload        = excluded.payload
        """
        self._execute(sql, _jsonb_encode(source, ["payload"]))

    # ── Node ──────────────────────────────────────────────────────────────────

    def add_node(self, node: dict) -> None:
        sql = """
            insert into evidence_nodes
              (node_id, case_id, source_id, node_type, source_tier, title,
               observed_at, published_at, effective_at, confidence, summary, payload)
            values
              (%(node_id)s, %(case_id)s, %(source_id)s, %(node_type)s,
               %(source_tier)s, %(title)s, %(observed_at)s, %(published_at)s,
               %(effective_at)s, %(confidence)s, %(summary)s, %(payload)s::jsonb)
            on conflict (node_id) do update set
              confidence = excluded.confidence,
              summary    = excluded.summary,
              payload    = excluded.payload
        """
        self._execute(sql, _jsonb_encode(node, ["payload"]))

    # ── Edge ──────────────────────────────────────────────────────────────────

    def add_edge(self, edge: dict) -> None:
        sql = """
            insert into evidence_edges
              (edge_id, case_id, from_node, to_node, relation, confidence,
               check_name, rationale, payload)
            values
              (%(edge_id)s, %(case_id)s, %(from_node)s, %(to_node)s,
               %(relation)s, %(confidence)s, %(check_name)s, %(rationale)s,
               %(payload)s::jsonb)
            on conflict (edge_id) do update set
              confidence = excluded.confidence,
              rationale  = excluded.rationale,
              payload    = excluded.payload
        """
        self._execute(sql, _jsonb_encode(edge, ["payload"]))

    # ── Check ─────────────────────────────────────────────────────────────────

    def add_check(self, check: dict) -> None:
        sql = """
            insert into evidence_checks
              (check_id, case_id, name, status, score, rationale, payload)
            values
              (%(check_id)s, %(case_id)s, %(name)s, %(status)s, %(score)s,
               %(rationale)s, %(payload)s::jsonb)
            on conflict (check_id) do update set
              status   = excluded.status,
              score    = excluded.score,
              rationale = excluded.rationale,
              payload  = excluded.payload
        """
        self._execute(sql, _jsonb_encode(check, ["payload"]))

    # ── Event log ─────────────────────────────────────────────────────────────

    def append_event(self, case_id: str, actor: str, action: str, payload: dict) -> None:
        sql = """
            insert into evidence_case_events (case_id, actor, action, payload)
            values (%s, %s, %s, %s::jsonb)
        """
        with self._conn.cursor() as cur:
            cur.execute(sql, (case_id, actor, action, json.dumps(payload)))
        self._conn.commit()

    # ── Search queries ────────────────────────────────────────────────────────

    def add_search_query(self, query: dict) -> None:
        sql = """
            insert into evidence_search_queries
              (query_id, case_id, query, provider, window_start, window_end,
               result_count, payload)
            values
              (%(query_id)s, %(case_id)s, %(query)s, %(provider)s,
               %(window_start)s, %(window_end)s, %(result_count)s, %(payload)s::jsonb)
            on conflict (query_id) do update set
              result_count = excluded.result_count,
              payload      = excluded.payload
        """
        self._execute(sql, _jsonb_encode(query, ["payload"]))

    # ── Read ──────────────────────────────────────────────────────────────────

    def load_case_graph(self, case_id: str) -> dict:
        import psycopg2.extras
        result: dict = {}
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("select * from evidence_cases where case_id = %s", (case_id,))
            row = cur.fetchone()
            if row is None:
                return {}
            result["case"] = dict(row)

            cur.execute("select * from evidence_nodes where case_id = %s", (case_id,))
            result["nodes"] = [dict(r) for r in cur.fetchall()]

            cur.execute("select * from evidence_edges where case_id = %s", (case_id,))
            result["edges"] = [dict(r) for r in cur.fetchall()]

            cur.execute("select * from evidence_checks where case_id = %s", (case_id,))
            result["checks"] = [dict(r) for r in cur.fetchall()]

            source_ids = [n["source_id"] for n in result["nodes"] if n.get("source_id")]
            if source_ids:
                cur.execute(
                    "select * from evidence_sources where source_id = any(%s)",
                    (source_ids,),
                )
                result["sources"] = [dict(r) for r in cur.fetchall()]
            else:
                result["sources"] = []

        return result

    def list_cases(self, filters: dict | None = None) -> list[dict]:
        import psycopg2.extras
        where_parts: list[str] = []
        params: list = []
        for key in ("status", "run_id", "signal_type", "verdict"):
            if filters and key in filters:
                where_parts.append(f"{key} = %s")
                params.append(filters[key])
        where_clause = ("where " + " and ".join(where_parts)) if where_parts else ""
        sql = f"""
            select case_id, run_id, instrument, as_of_date, signal_type,
                   status, verdict, confidence, created_at
            from evidence_cases
            {where_clause}
            order by created_at desc
            limit 500
        """
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def update_case_status(self, case_id: str, status: str) -> None:
        sql = "update evidence_cases set status = %s, updated_at = now() where case_id = %s"
        with self._conn.cursor() as cur:
            cur.execute(sql, (status, case_id))
        self._conn.commit()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _execute(self, sql: str, params: dict) -> None:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
        self._conn.commit()


# ── Migration runner ──────────────────────────────────────────────────────────

def run_migrations(dsn: str | None = None, migrations_dir: str | None = None) -> list[str]:
    """Apply all SQL migration files in order. Returns list of applied file names."""
    from pathlib import Path
    import psycopg2

    resolved_dsn = dsn or os.environ.get("JANUS_EVIDENCE_DATABASE_URL")
    if not resolved_dsn:
        raise ValueError("run_migrations requires JANUS_EVIDENCE_DATABASE_URL")

    if migrations_dir is None:
        migrations_dir = str(Path(__file__).parent.parent.parent / "db" / "migrations" / "evidence_graph")

    sql_files = sorted(Path(migrations_dir).glob("*.sql"))
    applied: list[str] = []

    conn = psycopg2.connect(resolved_dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for sql_file in sql_files:
                cur.execute(sql_file.read_text(encoding="utf-8"))
                applied.append(sql_file.name)
    finally:
        conn.close()

    return applied


# ── Helpers ───────────────────────────────────────────────────────────────────

def _jsonb_encode(row: dict, jsonb_keys: list[str]) -> dict:
    out = dict(row)
    for key in jsonb_keys:
        if key in out and isinstance(out[key], dict):
            out[key] = json.dumps(out[key])
    return out
