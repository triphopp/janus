-- Migration 007 — evidence_search_queries

create table if not exists evidence_search_queries (
  query_id     text primary key,
  case_id      text references evidence_cases(case_id) on delete cascade,
  query        text not null,
  provider     text,
  window_start date,
  window_end   date,
  result_count integer not null default 0,
  created_at   timestamptz not null default now(),
  payload      jsonb not null default '{}'::jsonb
);

create index if not exists evidence_search_queries_case_idx
  on evidence_search_queries(case_id);
create index if not exists evidence_search_queries_provider_idx
  on evidence_search_queries(provider);
