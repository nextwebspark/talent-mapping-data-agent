-- Enrichment output for companies. One row per (company_id, prompt_version).
-- company_id is the Zawya identifier; we dedupe per Zawya company, not per
-- companies.id (since the same company_id can appear under multiple sectors).

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
