create table public.companies (
  id bigserial not null,
  company_id text not null,
  name text not null,
  slug text not null,
  sector text not null,
  country text not null,
  company_type text not null,
  profile_url text not null,
  description text null,
  website text null,
  founded_year integer null,
  address text null,
  phone text null,
  email text null,
  employees_count text null,
  executives jsonb null,
  listing_scraped_at timestamp with time zone null,
  detail_scraped_at timestamp with time zone null,
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now(),
  top_company boolean not null default false,
  constraint companies_pkey primary key (id)
) TABLESPACE pg_default;

create index IF not exists companies_company_id_idx on public.companies using btree (company_id) TABLESPACE pg_default;

create index IF not exists companies_detail_pending_idx on public.companies using btree (detail_scraped_at) TABLESPACE pg_default
where
  (detail_scraped_at is null);

create unique INDEX IF not exists companies_company_sector_idx on public.companies using btree (company_id, sector) TABLESPACE pg_default;

create index IF not exists companies_top_company_idx on public.companies using btree (top_company) TABLESPACE pg_default
where
  (top_company = true);

create trigger companies_set_updated_at BEFORE
update on companies for EACH row
execute FUNCTION set_updated_at ();