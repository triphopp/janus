-- Migration 001 — evidence_cases
-- Run against: janus (env: JANUS_EVIDENCE_DATABASE_URL)

create table if not exists evidence_cases (
  case_id        text primary key,
  run_id         text not null,
  instrument     text,
  family         text,
  as_of_date     date,
  signal_type    text not null,
  severity       text,
  status         text not null default 'unreviewed',
  verdict        text,
  confidence     text,
  event_type     text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  artifact_path  text,
  payload        jsonb not null default '{}'::jsonb
);

create index if not exists evidence_cases_run_idx
  on evidence_cases(run_id);
create index if not exists evidence_cases_status_idx
  on evidence_cases(status);
create index if not exists evidence_cases_instrument_date_idx
  on evidence_cases(instrument, as_of_date);
create index if not exists evidence_cases_payload_idx
  on evidence_cases using gin(payload);
