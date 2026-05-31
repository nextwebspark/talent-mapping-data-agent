-- Seed pool of GCC companies harvested via Claude Code's WebSearch/WebFetch.
-- Distinct from public.companies (Zawya scrape). One row per
-- (slug, country, sector, harvest_version). The same name appearing under
-- multiple sectors is allowed and represented as separate rows.

create table if not exists public.company_seed_list (
  id bigserial primary key,
  name text not null,
  slug text not null,
  country text not null,
  sector text not null,
  website text,
  description text,
  source_url text not null,
  source_title text,
  source_query text,
  harvest_version text not null default 'v1',
  captured_at timestamptz not null default now(),
  raw_context jsonb not null default '{}'::jsonb,
  unique (slug, country, sector, harvest_version),
  constraint company_seed_list_country_check check (
    country in (
      'United Arab Emirates',
      'Saudi Arabia',
      'Qatar',
      'Kuwait',
      'Bahrain',
      'Oman'
    )
  )
);

create index if not exists company_seed_list_country_sector_idx
  on public.company_seed_list (country, sector);

create index if not exists company_seed_list_slug_idx
  on public.company_seed_list (slug);

create index if not exists company_seed_list_sector_idx
  on public.company_seed_list (sector);
