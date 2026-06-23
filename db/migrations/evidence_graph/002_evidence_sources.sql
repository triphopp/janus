-- Migration 002 — evidence_sources

create table if not exists evidence_sources (
  source_id      text primary key,
  url            text not null,
  canonical_url  text,
  domain         text,
  source_tier    text,
  title          text,
  published_at   timestamptz,
  fetched_at     timestamptz not null default now(),
  accessed_at    timestamptz not null default now(),
  content_hash   text,
  extracted_text text,
  summary        text,
  payload        jsonb not null default '{}'::jsonb,
  search_text    tsvector generated always as (
    to_tsvector('english',
      coalesce(title, '') || ' ' ||
      coalesce(summary, '') || ' ' ||
      coalesce(extracted_text, '')
    )
  ) stored
);

drop index if exists evidence_sources_url_idx;
create unique index if not exists evidence_sources_url_content_idx
  on evidence_sources(canonical_url, content_hash);
create index if not exists evidence_sources_url_idx
  on evidence_sources(url);
create index if not exists evidence_sources_canonical_url_idx
  on evidence_sources(canonical_url);
create index if not exists evidence_sources_hash_idx
  on evidence_sources(content_hash);
create index if not exists evidence_sources_domain_idx
  on evidence_sources(domain);
create index if not exists evidence_sources_tier_idx
  on evidence_sources(source_tier);
create index if not exists evidence_sources_fts_idx
  on evidence_sources using gin(search_text);
create index if not exists evidence_sources_payload_idx
  on evidence_sources using gin(payload);
