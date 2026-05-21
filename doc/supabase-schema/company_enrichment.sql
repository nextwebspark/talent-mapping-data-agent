-- Enrichment output for companies. One row per (company_id, prompt_version).
-- company_id is the Zawya identifier; we dedupe per Zawya company, not per
-- companies.id (since the same company_id can appear under multiple sectors).

-- Live-DB migration (idempotent) for contact fields added in prompt_version v2:
alter table if exists public.company_enrichment
  add column if not exists website text,
  add column if not exists phone text,
  add column if not exists email text,
  add column if not exists address text;

-- Live-DB migration (idempotent) for v3 sector model:
--   sector_mix     : qualitative ops weights [{"sector":"...","weight":"dominant|significant|minor"}]
--   sub_tags       : controlled sub-niche tags (from src/agent/subtags.py, closed list)
--   proposed_tags  : Gemini-suggested new sub-tags (escape valve, for human review)
--   keywords       : free-flow descriptors (informational; future embedding similarity)
alter table if exists public.company_enrichment
  add column if not exists sector_mix jsonb not null default '[]'::jsonb,
  add column if not exists sub_tags text[] not null default '{}',
  add column if not exists proposed_tags text[] not null default '{}',
  add column if not exists keywords text[] not null default '{}';

create index if not exists company_enrichment_sub_tags_gin
  on public.company_enrichment using gin (sub_tags);

create index if not exists company_enrichment_sector_mix_gin
  on public.company_enrichment using gin (sector_mix jsonb_path_ops);

create table if not exists public.company_enrichment (
  id bigserial primary key,
  company_pk bigint not null references public.companies(id) on delete cascade,
  company_id text not null,
  primary_sector text not null,
  sector_tags text[] not null default '{}',
  adjacent_sectors text[] not null default '{}',
  tagline text,
  business_description text,
  employee_band text,
  employee_count_estimate integer,
  revenue_band text,
  revenue_estimate_usd bigint,
  is_listed boolean,
  hq_city text,
  website text,
  phone text,
  email text,
  address text,
  confidence numeric(3,2) not null,
  sources jsonb not null default '[]'::jsonb,
  model text not null,
  prompt_version text not null,
  enriched_at timestamptz not null default now(),
  raw_response jsonb,
  unique (company_id, prompt_version)
);

create index if not exists company_enrichment_company_pk_idx
  on public.company_enrichment (company_pk);

create index if not exists company_enrichment_company_id_idx
  on public.company_enrichment (company_id);

create index if not exists company_enrichment_primary_sector_idx
  on public.company_enrichment (primary_sector);

create index if not exists company_enrichment_sector_tags_gin
  on public.company_enrichment using gin (sector_tags);

create index if not exists company_enrichment_adjacent_sectors_gin
  on public.company_enrichment using gin (adjacent_sectors);
