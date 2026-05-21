# talent-mapping-data-agent

HAK company enrichment agent. Reads companies from Supabase, enriches each via
grounded Gemini (Google Search tool), and writes structured firmographics +
multi-tag sectors to `public.company_enrichment`. Single-agent design built on
[Google ADK](https://google.github.io/adk-docs/); deployable to Vertex AI
Agent Engine for managed runs.

See [doc/HAK_MVP_Technical_Plan.md](doc/HAK_MVP_Technical_Plan.md) for the
broader product context.

## What gets produced per company

- `primary_sector` (one of 20 taxonomy buckets)
- `sector_tags` (taxonomy + free-form sub-tags, e.g. `fintech-lending`)
- `adjacent_sectors` (recruiter-perspective talent adjacency)
- `tagline`, `business_description`
- `employee_band`, `employee_count_estimate`
- `revenue_band`, `revenue_estimate_usd`
- `is_listed`, `hq_city`
- `confidence` (0-1), `sources[]` (grounded URLs)

## Layout

```
doc/supabase-schema/
  companies.sql              # source table (existing)
  company_enrichment.sql     # new table (apply this)
src/
  agent/
    taxonomy.py              # SECTORS + ADJACENCY map
    prompts.py               # system instruction + Pydantic schema
    enrichment_agent.py      # ADK root_agent
  tools/
    grounded_gemini.py       # Gemini + google_search tool
    supabase_tool.py         # fetch + upsert
  runner/batch_run.py        # CLI for bulk runs
  deploy/vertex_deploy.py    # Vertex AI Agent Engine deploy
```

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/) for dependency
management.

```bash
# install uv if missing
curl -LsSf https://astral.sh/uv/install.sh | sh

cp .env.example .env
# fill SUPABASE_URL, SUPABASE_SERVICE_KEY, GOOGLE_API_KEY (for local dev)

# create venv + install runtime + dev deps from pyproject.toml
uv sync --all-groups
```

Apply the new table:

```bash
psql "$SUPABASE_DB_URL" -f doc/supabase-schema/company_enrichment.sql
```

## Run locally

```bash
# Dry run: print 5 enrichments without writing
uv run batch-run --limit 5 --dry-run

# Real run: 100 UAE companies
uv run batch-run --limit 100 --country "United Arab Emirates"
```

Try the ADK agent interactively:

```bash
uv run adk web src/agent
# open the URL, ask: "Enrich 10 UAE companies and report sector distribution."
```

## Tests

```bash
uv run pytest
uv run pytest -k taxonomy           # one module
uv run pytest --cov=src             # if you add pytest-cov
```

## Deploy to Vertex AI Agent Engine

Set `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GCS_STAGING_BUCKET`,
then:

```bash
PYTHONPATH=src python -m deploy.vertex_deploy --create
PYTHONPATH=src python -m deploy.vertex_deploy --list
PYTHONPATH=src python -m deploy.vertex_deploy --delete <resource_name>
```

## Verify

```sql
select c.name, e.primary_sector, e.sector_tags, e.adjacent_sectors,
       e.employee_band, e.revenue_band, e.confidence
from companies c
join company_enrichment e on e.company_pk = c.id
order by e.enriched_at desc
limit 20;
```

Coverage:

```sql
select
  count(distinct c.company_id) as total_companies,
  count(distinct e.company_id) as enriched_companies,
  round(100.0 * count(distinct e.company_id) / count(distinct c.company_id), 1) as pct
from companies c
left join company_enrichment e on e.company_id = c.company_id;
```

## Notes

- Dedup is by Zawya `company_id`, not `companies.id`. Same company may appear
  in `companies` multiple times (one row per source sector); we enrich once.
- `prompt_version` lets v1 and future v2 prompts coexist. Bump it in
  `src/agent/prompts.py` when the schema or instruction changes.
- Grounded Gemini does not accept `response_schema` simultaneously, so JSON
  is enforced by the prompt and validated with Pydantic in
  `src/tools/grounded_gemini.py`.
