-- Migration 005 — evidence_checks

create table if not exists evidence_checks (
  check_id   text primary key,
  case_id    text not null references evidence_cases(case_id) on delete cascade,
  name       text not null,
  status     text not null,
  score      numeric,
  rationale  text,
  payload    jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists evidence_checks_case_idx
  on evidence_checks(case_id);
create index if not exists evidence_checks_status_idx
  on evidence_checks(status);
create index if not exists evidence_checks_name_idx
  on evidence_checks(name);
