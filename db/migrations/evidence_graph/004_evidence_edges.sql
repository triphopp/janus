-- Migration 004 — evidence_edges

create table if not exists evidence_edges (
  edge_id    text primary key,
  case_id    text not null references evidence_cases(case_id) on delete cascade,
  from_node  text not null references evidence_nodes(node_id) on delete cascade,
  to_node    text not null references evidence_nodes(node_id) on delete cascade,
  relation   text not null,
  confidence numeric,
  check_name text,
  rationale  text,
  payload    jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists evidence_edges_case_idx
  on evidence_edges(case_id);
create index if not exists evidence_edges_from_idx
  on evidence_edges(from_node);
create index if not exists evidence_edges_to_idx
  on evidence_edges(to_node);
create index if not exists evidence_edges_relation_idx
  on evidence_edges(relation);
create index if not exists evidence_edges_payload_idx
  on evidence_edges using gin(payload);
