-- Migration 006 — evidence_case_events (append-only audit trail)

create table if not exists evidence_case_events (
  event_id   bigserial primary key,
  case_id    text not null references evidence_cases(case_id) on delete cascade,
  actor      text not null,
  action     text not null,
  created_at timestamptz not null default now(),
  payload    jsonb not null default '{}'::jsonb
);

create index if not exists evidence_case_events_case_idx
  on evidence_case_events(case_id);
create index if not exists evidence_case_events_action_idx
  on evidence_case_events(action);
