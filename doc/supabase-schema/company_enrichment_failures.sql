-- Records per-company enrichment failures so they are auditable and so that
-- repeatedly-failing rows can be skipped (poison pill protection).
--
-- One row PER ATTEMPT. fetch_unenriched_companies counts rows here per
-- (company_id, prompt_version) and skips when attempts >= threshold.

create table if not exists public.company_enrichment_failures (
  id bigserial primary key,
  company_pk bigint not null references public.companies(id) on delete cascade,
  company_id text not null,
  prompt_version text not null,
  attempt integer not null default 1,
  error_class text not null,
  error_message text not null,
  raw_response text,
  failed_at timestamptz not null default now()
);

create index if not exists company_enrichment_failures_company_idx
  on public.company_enrichment_failures (company_id, prompt_version);

create index if not exists company_enrichment_failures_failed_at_idx
  on public.company_enrichment_failures (failed_at desc);
