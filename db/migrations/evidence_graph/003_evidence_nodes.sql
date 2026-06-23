-- Migration 003 — evidence_nodes

create table if not exists evidence_nodes (
  node_id      text primary key,
  case_id      text not null references evidence_cases(case_id) on delete cascade,
  source_id    text references evidence_sources(source_id),
  node_type    text not null,
  source_tier  text,
  title        text,
  observed_at  timestamptz,
  published_at timestamptz,
  effective_at timestamptz,
  confidence   numeric,
  summary      text,
  payload      jsonb not null default '{}'::jsonb,
  created_at   timestamptz not null default now()
);

create index if not exists evidence_nodes_case_idx
  on evidence_nodes(case_id);
create index if not exists evidence_nodes_type_idx
  on evidence_nodes(node_type);
create index if not exists evidence_nodes_time_idx
  on evidence_nodes(coalesce(published_at, observed_at, effective_at));
create index if not exists evidence_nodes_payload_idx
  on evidence_nodes using gin(payload);
