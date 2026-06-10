# CLAUDE.md

Guidance for Claude Code (and future agents) working in this repository.

## Project Purpose

**HAK Company Enrichment Agent.** Reads companies from Supabase (`public.companies`, scraped from zawya.com), enriches each via grounded Gemini, and writes structured firmographics + multi-tag sectors to `public.company_enrichment`. This is the data backbone for HAK's downstream "company universe builder" — the system that, given a recruiter's role brief, surfaces relevant + adjacent-sector companies.

Project lives in: `/Users/alokkumar/dev/talent-mapping-data-agent`.

Broader product plan: [doc/HAK_MVP_Technical_Plan.md](doc/HAK_MVP_Technical_Plan.md).

## Where This Fits in the HAK Architecture

We implement **Layer 2 — Stage A: Company Universe Construction** (HAK plan §2.2.1), specifically the **Company Data Enrichment** sub-step. Layer 1 (brief ingestion), Layer 2 Stage B (candidate discovery via PDL), and Layer 4 (scoring) are downstream and not in scope here.

```
Layer 1 — Brief → Search Mandate
                 (target_sectors.direct[], adjacent[], company_context.size...)
                                │
                                ▼
Layer 2 Stage A — Company Universe ← THIS PROJECT
   Pulls from company_enrichment using mandate filters.
                                │
                                ▼
Layer 2 Stage B — Candidate Discovery (PDL queries per company)   [not built]
                                │
                                ▼
Layer 4 — 4-dimension candidate scoring                            [not built]
```

The enrichment data is consumed by the universe builder via SQL (no LLM at universe time):
- Primary sector match (denormalized): `e.primary_sector = ANY(mandate.direct)`
- Multi-sector ops match (v3): `e.sector_mix @? '$[*] ? (@.sector == "Real Estate Development")'`
- Dominant-only match (v3): `e.sector_mix @? '$[*] ? (@.sector == "Real Estate Development" && @.weight == "dominant")'`
- Sub-tag overlap (v3, controlled vocab): `e.sub_tags && ARRAY['fintech-lending', 'sme-lending']`
- Talent adjacency expansion: resolved at query time via `ADJACENCY` map in `taxonomy.py` — not stored per company
- Filtering: `employee_band`, `revenue_band`, `is_listed`, `country`, `confidence >= 0.5`

Vector DB is **NOT used yet**. SQL + GIN array indexes handle MVP. When semantic mandate-to-company matching is needed, add `pgvector` extension to the same Supabase Postgres and an `embedding vector(768)` column on `company_enrichment`. Do not introduce Qdrant/Pinecone unless scale demands it.

## Repository Layout

```
.
├── CLAUDE.md                              # this file
├── README.md                              # public README
├── pyproject.toml                         # uv-managed; hatchling build; pytest config
├── .env.example                           # env var template
├── .gitignore
├── doc/
│   ├── HAK_MVP_Technical_Plan.md          # full HAK product plan
│   └── supabase-schema/
│       ├── companies.sql                  # source table (already deployed in Supabase)
│       ├── company_enrichment.sql         # new table (apply this)
│       └── company_enrichment_failures.sql # per-attempt failure log (apply this)
├── src/
│   ├── config.py                          # env loading (Pydantic-free dataclass)
│   ├── agent/
│   │   ├── taxonomy.py                    # SECTORS (22), ADJACENCY map (query-time use), EMPLOYEE_BANDS, REVENUE_BANDS
│   │   ├── subtags.py                     # SUB_TAGS_BY_SECTOR controlled vocab (~246), SUB_TAGS set
│   │   ├── prompts.py                     # PROMPT_VERSION, EnrichmentResult schema, system_instruction()
│   │   └── enrichment_agent.py            # ADK root_agent (interactive use; not used for bulk)
│   ├── tools/
│   │   ├── grounded_gemini.py             # Gemini + Google Search tool; JSON parse + Pydantic validate
│   │   └── supabase_tool.py               # fetch_unenriched_companies, write_enrichment, build_enrichment_payload
│   ├── runner/
│   │   └── batch_run.py                   # CLI workflow (deterministic loop) — primary bulk path
│   └── deploy/
│       └── vertex_deploy.py               # AdkApp wrapper + Vertex Agent Engine create/update/list/delete
└── tests/                                 # unit tests, no API keys needed
    ├── conftest.py                        # env stubs + sample fixtures
    ├── test_taxonomy.py
    ├── test_subtags.py                    # vocab integrity (kebab-case, no dups, sector keys)
    ├── test_prompts.py
    ├── test_grounded_gemini.py
    ├── test_supabase_tool.py
    └── test_batch_run.py
```

## Data Model

**Source table** (`public.companies`) — already exists, do not modify:
- `id` (bigserial PK), `company_id` (Zawya identifier; same `company_id` may appear in multiple rows with different `sector`), `name`, `slug`, `sector` (coarse), `country`, `company_type`, `profile_url`, `description`, `website`, `founded_year`, `address`, `phone`, `email`, `employees_count` (text), `executives` (jsonb), `top_company`.
- Unique index on `(company_id, sector)`.

**Output table** (`public.company_enrichment`) — defined in [doc/supabase-schema/company_enrichment.sql](doc/supabase-schema/company_enrichment.sql):
- Dedupe key: `(company_id, prompt_version)`. **We enrich once per Zawya `company_id`, NOT once per `companies.id` row.** This is intentional — the same company appearing under multiple sectors should not be enriched multiple times.
- `company_pk` is a denormalized FK to one of the matching `companies.id` rows (lowest id, preferring `top_company=true`).
- `primary_sector` is constrained at the application layer to be one of `SECTORS` (no DB enum — easier to evolve taxonomy).
- **v4 sector model** (v3 rows remain in DB, queryable; v4 is current):
  - `sector_mix` (jsonb, GIN `jsonb_path_ops`-indexed): qualitative ops breakdown. Array of `{sector, weight}` where `weight ∈ {dominant, significant, minor}`. At least one entry is `dominant` and matches `primary_sector`. Max 5 entries.
  - `sub_tags` (text[], GIN-indexed): controlled-vocabulary sub-niche tags drawn from [src/agent/subtags.py](src/agent/subtags.py) (~272 entries, 22 sectors). Invalid entries returned by Gemini are auto-moved to `proposed_tags` by the Pydantic `model_validator` in `EnrichmentResult`.
  - `proposed_tags` (text[]): escape valve for Gemini-suggested sub-tags missing from the controlled vocab. NOT used for filtering. Periodically reviewed → frequent suggestions promoted into `SUB_TAGS_BY_SECTOR` and `PROMPT_VERSION` bumped.
  - `keywords` (text[]): free-flow descriptors (brands owned, geographies, business models). Informational only; future embedding-similarity fallback.
  - `sector_tags` (text[]): legacy v1/v2 column. From v3 onward it mirrors `sub_tags` for back-compat. New consumers should use `sub_tags` directly.
- Contact fields (v2+): `website`, `phone`, `email`, `address` (text). LLM-enriched with `sources[]` citation requirement; Pydantic validator flattens dict/list responses to a single string.
- `confidence`: 0.0–1.0. Downstream queries should filter `>= 0.5` for usable rows.
- `sources`: JSONB array of `{url, title, snippet}` from Gemini grounding metadata.
- `model`, `prompt_version`: versioning. Bump `PROMPT_VERSION` in `src/agent/prompts.py` when the prompt or schema changes; old + new versions coexist. **Current: v6** (v6 made `revenue_band` always-required: Gemini now estimates a band from proxies — headcount, listing status, sector norms, peers — when no figure is sourced, leaving `revenue_estimate_usd` null and capping confidence at 0.8 for estimated bands; v5 removed `adjacent_sectors` — now resolved at query time via `ADJACENCY` map).
- `raw_response`: full LLM JSON for audit/debug.

**Failure log table** (`public.company_enrichment_failures`) — defined in [doc/supabase-schema/company_enrichment_failures.sql](doc/supabase-schema/company_enrichment_failures.sql):
- One row per failed attempt at `(company_id, prompt_version)`. `attempt` increments automatically (computed from existing rows).
- Columns: `company_pk`, `company_id`, `prompt_version`, `attempt`, `error_class`, `error_message`, `raw_response`, `failed_at`.
- Used for: audit trail, poison-pill skip (rows with ≥ `max_failures_per_row` failures are excluded from `fetch_unenriched_companies`).

## Taxonomy (22 sectors) + Controlled Sub-tag Vocabulary

Defined in [src/agent/taxonomy.py](src/agent/taxonomy.py). UAE/GCC-flavored. Every sector has an entry in `ADJACENCY` (recruiter-perspective talent adjacency, not business-model adjacency). The `ADJACENCY` map is used at query time by the universe builder — not during enrichment.

Hard validation: `primary_sector` must be one of `SECTORS`. `sector_mix[].sector` must also be from `SECTORS`.

**Sub-tag vocabulary** ([src/agent/subtags.py](src/agent/subtags.py)): closed list of ~246 kebab-case sub-niches grouped by sector. `sub_tags` returned by Gemini must come from this list; out-of-vocab entries are auto-moved to `proposed_tags` and Gemini is told via prompt to use `proposed_tags` for suggestions. Brief parsing (Layer 1) must draw `mandate.sub_tags` from the SAME list so universe-builder array overlap works without vocab drift.

**Promotion loop**: periodically inspect `company_enrichment.proposed_tags` aggregates. Promote frequent + meaningful candidates by adding them to `SUB_TAGS_BY_SECTOR` and bumping `PROMPT_VERSION`.

## Enrichment Workflow (current bulk path)

**Model selection (single knob):** the enrichment model is set once via the `ENRICHMENT_MODEL` env var and read through `settings.model` in `src/config.py`. Every code path follows it — the batch runner, `grounded_gemini.py`, the ADK agent (`enrichment_agent.py`), and the Vertex deploy. No model literal exists in `src/`. **Default is `gemini-2.5-flash`** (chosen for cost — ~10x cheaper than Pro, still supports Google Search grounding). To switch the entire pipeline to Pro for high-stakes / `top_company` runs, set `ENRICHMENT_MODEL=gemini-2.5-pro` — no code change. This is the cheap first step toward the two-tier split (Flash classification + Pro grounded firmographics) in "Future architecture" below.

`src/runner/batch_run.py` is a **deterministic workflow**, not an agentic loop. The ADK agent (`enrichment_agent.py`) is reserved for interactive use and Vertex Agent Engine deployment; bulk runs do not use it because LLM-driven orchestration adds cost + nondeterminism without benefit when the per-row work is itself an LLM call.

Per-company flow:

```
fetch_unenriched_companies(limit, country, sector, top_company_only, prompt_version, max_failures_per_row)
  ↓ dedup by company_id
  ↓ skip rows already enriched at current prompt_version
  ↓ skip rows with >= max_failures_per_row failures (poison pill)
for row in batch:
    try:
        enrich_company_grounded(name, country, website, description, coarse_sector)
            ↓ grounded Gemini call with Google Search tool
            ↓ JSON parse + Pydantic EnrichmentResult validation
            ↓ tenacity retry x3 on transient errors
        write_enrichment(build_enrichment_payload(...))
            ↓ upsert on (company_id, prompt_version)
    except Exception:
        write_failure(row, error, prompt_version)
            ↓ insert into company_enrichment_failures with attempt = existing+1
        if failed >= max_failures_before_stop: break
```

Re-run idempotency:
- `fetch_unenriched_companies` excludes rows already in `company_enrichment` for the current `prompt_version`.
- It also excludes rows that hit the failure threshold (default 3 attempts) — re-running won't loop forever on a poison pill.
- Safe to re-run; safe to crash mid-batch.

## Known Gaps (Important — Future Work)

These were identified but not yet implemented. Do not assume they exist:

1. **No batch API integration.** Currently every call is real-time grounded. User wants Vertex AI Batch Prediction (~50% cheaper, async) for the bulk classification tier. See "Future architecture" below.
2. **No Supabase write retry.** `write_enrichment` is not wrapped in tenacity; a transient Supabase 5xx kills the iteration.
3. **No semantic embeddings.** Plan calls for `pgvector` later; not added yet. Easy `ALTER TABLE` + backfill from `tagline + business_description` when needed.
4. **No `--company-id` / `--min-confidence` flags.** Cannot target a single company or re-enrich low-confidence rows yet. (Failed-batch re-run *does* exist: `--retry-failed` sources the `enrichment_status='failed'` set — used to re-run poison pills on a stronger model. Caveat: a failed row already has ≥3 logged attempts at the same `prompt_version`, so under the default `--max-failures-per-row 3` it gets exactly one Pro attempt before re-locking to `'failed'`. For multiple Pro attempts raise `--max-failures-per-row` since attempts are cumulative across models at one `prompt_version`.)
5. ~~No async / parallel execution.~~ **DONE.** `batch_run.run()` dispatches per-company enrichment across a `ThreadPoolExecutor` (`--concurrency`, default 5). The bottleneck is blocking grounded-Gemini I/O, so threads give near-linear speedup. Enrichments are written per-thread; failures are written serially in the main thread to avoid the attempt-number race in `write_failure`. `--concurrency 1` reproduces the old strictly-serial behavior. (Full asyncio / Cloud Run `--parallelism` remains an option only if volume outgrows threads.)

Already implemented and working:
- Failures table + per-attempt logging + poison-pill skip (≥3 attempts → row excluded).
- CLI filters: `--country`, `--sector`, `--top-company-only`, `--max-failures-per-row`, `--max-failures-before-stop`.
- Idempotent re-runs via `(company_id, prompt_version)` dedup.

## Future Architecture — Two-Tier Batch (Discussed, Not Implemented)

User decided enrichment is batch-only and wants Vertex Batch Prediction for cost. Caveat: Vertex Batch + Google Search grounding has historically been restricted on `gemini-2.5-pro`. Verify before committing.

**Tier 1 — Classification batch (Vertex Batch Prediction, no grounding):**
- Inputs: `description` + `website` text already in `companies`.
- Outputs: `primary_sector`, `sector_tags`, `tagline`, `business_description`.
- Cost: ~50% of real-time. Latency: minutes–hours, async.
- Confidence ceiling: 0.7 (no live web verification).
- Recommended model: `gemini-2.5-flash` (cheaper, fine for classification).

**Tier 2 — Firmographics enrichment (real-time grounded, selective):**
- Inputs: company already classified by Tier 1.
- Outputs: `employee_band`, `employee_count_estimate`, `revenue_band`, `revenue_estimate_usd`, `is_listed`, `hq_city`, `sources[]`.
- Selection criteria: `top_company = true` OR companies appearing in an active search universe.
- Confidence can reach 0.9+ when sourced.
- Recommended model: `gemini-2.5-pro`.

**Schema migration for two-tier**: add `classification_at` and `firmographics_at` nullable timestamps on `company_enrichment` to track which tier has run. Same row, partial updates.

When implementing, build in this order:
1. Tier 1 batch path (`src/tools/batch_gemini.py` — submit JSONL, poll, download).
2. Schema migration for timestamp columns.
3. Add `--mode batch|realtime` and `--tier 1|2|both` flags to `batch_run.py`.
4. Quality audit script (random sample 20 rows → CSV for manual review).
5. Tier 2 selective grounded runner (cron filter `classification_at IS NOT NULL AND firmographics_at IS NULL AND top_company = true`).
6. Failures table.

## Seed Harvesting (GCC Company Seed List)

Separate from the Zawya-scraped `public.companies` table, we maintain a broader GCC seed pool in `public.company_seed_list`, harvested **inside Claude Code chat sessions** using `WebSearch` + `WebFetch` only (no Gemini, no PDL).

- DDL: [doc/supabase-schema/company_seed_list.sql](doc/supabase-schema/company_seed_list.sql). Unique key `(slug, country, sector, harvest_version)`. Country constrained to GCC 6.
- Storage helpers (in [src/tools/supabase_tool.py](src/tools/supabase_tool.py)): `slugify`, `write_seed_companies`, `fetch_seed_count`, `fetch_seed_slugs`. Sector validated against `SECTORS`; country against `GCC_COUNTRIES`.
- Harvest playbook: [doc/SEED_HARVEST_PLAYBOOK.md](doc/SEED_HARVEST_PLAYBOOK.md). Read it at the start of any harvest session.

Per-session workflow (one `(sector, country)` pair):

```
fetch_seed_slugs(country, sector)              # bootstrap dedup set
loop:
    WebSearch templated query
    WebFetch promising results
    parse company names + website
    drop slugs already seen
    every 20 new rows: write_seed_companies(batch)
stop when: 200 reached, 3 dry searches in a row, or budget tight
final flush
```

Re-running the same pair is safe — the upsert key dedupes.

Out of scope today: feeding seed rows into the Gemini enrichment pipeline, reconciling against `public.companies`, embeddings/ranking. These remain follow-ups.

## Running Locally

### Prerequisites

- Python 3.11+ (project uses 3.14 in venv but anything ≥3.11 works).
- [uv](https://docs.astral.sh/uv/) for dependency management. Install: `curl -LsSf https://astral.sh/uv/install.sh | sh`.
- Supabase project with `public.companies` already populated (Zawya scrape).
- Google API key from AI Studio (for local) — fastest path.

### Setup

```bash
cd /Users/alokkumar/dev/talent-mapping-data-agent

# Copy env template, then fill in values
cp .env.example .env
# Required: SUPABASE_URL, SUPABASE_SERVICE_KEY, GOOGLE_API_KEY

# Install all deps (runtime + dev) into .venv
uv sync --all-groups
```

### Apply DDL (first time only)

```bash
# Either via psql with direct connection string from Supabase dashboard:
psql "$SUPABASE_DB_URL" -f doc/supabase-schema/company_enrichment.sql
psql "$SUPABASE_DB_URL" -f doc/supabase-schema/company_enrichment_failures.sql

# Or paste the file contents into the Supabase SQL Editor and run.
```

### Run unit tests (no API keys needed)

```bash
uv run pytest
uv run pytest -k taxonomy           # one module
uv run pytest -v                    # verbose
```

Expected: 31 passed.

### Smoke-test the grounded Gemini call (needs GOOGLE_API_KEY)

```bash
uv run python -c "
from tools.grounded_gemini import enrich_company_grounded
import json
result = enrich_company_grounded(
    name='Emaar Properties',
    country='United Arab Emirates',
    website='https://www.emaar.com'
)
print(json.dumps(result, indent=2, default=str))
"
```

### Dry run (no DB writes; needs Supabase + Gemini keys)

```bash
uv run batch-run --limit 5 --dry-run
# Reads 5 unenriched rows, calls Gemini, prints JSON. Does NOT write to DB.
```

### Real run

```bash
# 100 UAE companies, ~0.5s sleep between calls
uv run batch-run --limit 100 --country "United Arab Emirates" --sleep 0.5

# All countries, 50 rows
uv run batch-run --limit 50

# Single sector: retail across all countries, top companies only
uv run batch-run --limit 50 --sector "Retail" --top-company-only

# Combine filters + abort early if 5 failures in a row
uv run batch-run --limit 200 --country "United Arab Emirates" --sector "Retail" \
                 --max-failures-before-stop 5

# Verbose logging
uv run batch-run --limit 10 --log-level DEBUG

# Re-run poison-pill failures (enrichment_status='failed') with Gemini Pro.
# Model is the single env knob — no code change. --retry-failed swaps the
# fetch source from the pending queue to the failed set.
ENRICHMENT_MODEL=gemini-2.5-pro uv run batch-run --retry-failed --limit 500 --concurrency 3
```

All CLI flags:

| Flag | Default | Meaning |
|---|---|---|
| `--limit` | 10 | Max companies in this run |
| `--country` | none | Exact match on `companies.country` |
| `--sector` | none | Exact match on `companies.sector` (the coarse Zawya sector) |
| `--top-company-only` | false | Only `top_company=true` rows |
| `--dry-run` | false | Print JSON, do not write DB |
| `--retry-failed` | false | Source rows from `enrichment_status='failed'` (poison pills) instead of the pending queue. Pair with `ENRICHMENT_MODEL=gemini-2.5-pro` to re-run failures on Pro. |
| `--concurrency` | 5 | Companies enriched in parallel (in-flight Gemini calls). `1` = strictly serial. Lower if you hit quota/429. |
| `--sleep` | 0.5 | Deprecated no-op. Throttling now governed by `--concurrency`. |
| `--max-failures-per-row` | 3 | Skip companies that already failed ≥ this many times at current prompt_version |
| `--max-failures-before-stop` | none | Abort whole batch once this many failures have occurred (quota guard) |
| `--log-level` | INFO | Logging level |

### Verify results in Supabase

```sql
-- Recent enrichments
SELECT c.name, e.primary_sector, e.sector_tags,
       e.employee_band, e.revenue_band, e.confidence
FROM   companies c
JOIN   company_enrichment e ON e.company_pk = c.id
ORDER BY e.enriched_at DESC
LIMIT 20;

-- Coverage
SELECT
  COUNT(DISTINCT c.company_id) AS total_companies,
  COUNT(DISTINCT e.company_id) AS enriched_companies,
  ROUND(100.0 * COUNT(DISTINCT e.company_id) / COUNT(DISTINCT c.company_id), 1) AS pct
FROM   companies c
LEFT JOIN company_enrichment e ON e.company_id = c.company_id;

-- Sector distribution
SELECT primary_sector, COUNT(*) AS n
FROM   company_enrichment
GROUP BY primary_sector
ORDER BY n DESC;

-- Low-confidence rows that need review
SELECT c.name, e.primary_sector, e.confidence, e.sources
FROM   companies c
JOIN   company_enrichment e ON e.company_pk = c.id
WHERE  e.confidence < 0.5
ORDER BY e.enriched_at DESC;

-- Failures summary
SELECT error_class, COUNT(*) AS n
FROM   company_enrichment_failures
WHERE  prompt_version = 'v1'
GROUP BY error_class
ORDER BY n DESC;

-- Poison-pill rows (will be skipped on re-run)
SELECT company_id, COUNT(*) AS attempts, MAX(failed_at) AS last_failed_at
FROM   company_enrichment_failures
WHERE  prompt_version = 'v1'
GROUP BY company_id
HAVING COUNT(*) >= 3;
```

### Interactive ADK agent (optional — not the primary bulk path)

```bash
uv run adk web src/agent
# Open the printed URL in browser. Then prompt:
# "Fetch 5 unenriched UAE companies, enrich them, and report sector distribution."
```

## Running on Google Cloud

The primary production deployment target is **Vertex AI Agent Engine** (managed). A Cloud Run Job for the batch workflow is the right alternative if you want pure batch without Agent Engine; that path is not built yet.

### One-time GCP setup

```bash
# Authenticate
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID

# Enable required APIs
gcloud services enable aiplatform.googleapis.com
gcloud services enable storage.googleapis.com

# Create a GCS staging bucket (used by Agent Engine for code upload)
gsutil mb -l us-central1 gs://hak-enrichment-staging
```

### Set env vars for Vertex deploy

In `.env`:

```
GOOGLE_GENAI_USE_VERTEXAI=true
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1
GCS_STAGING_BUCKET=gs://hak-enrichment-staging

# Still set:
SUPABASE_URL=...
SUPABASE_SERVICE_KEY=...
# GOOGLE_API_KEY is not needed when GOOGLE_GENAI_USE_VERTEXAI=true (auth via ADC)
```

### Deploy / update / list / delete

```bash
# Create new Agent Engine instance (first time)
uv run vertex-deploy --create
# Prints the resource name, e.g.:
#   projects/123/locations/us-central1/reasoningEngines/4567890

# List existing
uv run vertex-deploy --list

# Update an existing instance (after code changes)
uv run vertex-deploy --update projects/123/locations/us-central1/reasoningEngines/4567890

# Delete
uv run vertex-deploy --delete projects/123/locations/us-central1/reasoningEngines/4567890
```

The deploy bundles `src/` as `extra_packages` and passes Supabase + Gemini env vars to the engine via `env_vars`. Tracing is enabled (`enable_tracing=True`).

### Invoke the deployed agent

After deploy, the engine is callable via the Vertex AI SDK or REST. Example:

```python
from vertexai import agent_engines

remote = agent_engines.get("projects/123/locations/us-central1/reasoningEngines/4567890")
for event in remote.stream_query(
    message="Enrich 20 unenriched UAE companies and report sector distribution.",
    user_id="batch-user-1",
):
    print(event)
```

### Cloud Run Job for pure batch (NOT YET BUILT — pattern for future)

If you want the batch workflow without Agent Engine overhead:

1. Add a `Dockerfile` that installs the project with `uv pip install -e .`.
2. Push to Artifact Registry: `gcloud builds submit --tag us-central1-docker.pkg.dev/PROJECT/repo/enrichment:latest`.
3. Create Cloud Run Job: `gcloud run jobs create enrichment --image ... --set-env-vars ...`.
4. Schedule via Cloud Scheduler.

This is preferred for cost on bulk runs once volume justifies it.

## Testing Strategy

All 31 existing tests use mocks (no real API calls). Mock layers:
- `conftest.py` stubs env vars and clears Supabase client cache.
- `test_grounded_gemini.py` stubs `_client()` and feeds fake `generate_content` responses with synthetic grounding metadata.
- `test_supabase_tool.py` provides a fake Supabase client with controlled rows for the table queries.
- `test_batch_run.py` monkeypatches `fetch_unenriched_companies`, `enrich_company_grounded`, and `write_enrichment` to assert the loop wires them correctly.

When adding new functionality:
- Pure functions (parsing, taxonomy validation, payload mapping) → unit tests.
- External API calls (Gemini, Supabase) → mock at the client boundary.
- Do not write tests that hit real APIs from CI.

## Conventions & Gotchas

- **Caveman mode**: the user has a CLI mode that drops articles/filler in chat responses. Code, docs, commits, and security-sensitive output stay in normal English. See `~/.claude/plugins/cache/caveman/`.
- **uv, not pip**: dependencies managed in `pyproject.toml` `[dependency-groups]`. Use `uv sync --all-groups` and `uv run <cmd>`.
- **`PYTHONPATH=src`** is not needed when using `uv run` because the project is installed in editable mode by `uv sync`.
- **Dedupe is by `company_id` (Zawya ID)**, not `companies.id`. A single Zawya company can have multiple rows under different sectors; we enrich it once.
- **`prompt_version`** lets v1 and future v2 prompts coexist. Bump it in `src/agent/prompts.py` when the prompt or schema changes — old rows remain valid; new rows get re-enriched at the new version.
- **Grounded Gemini + `response_schema`**: the API does not accept both simultaneously. JSON is enforced via prompt and validated with Pydantic after parse. Do not try to set `response_schema` in `GenerateContentConfig` when `google_search` tool is enabled.
- **Confidence**: prompt instructs Gemini to drop confidence below 0.5 when fields are inferred/missing. Downstream queries should filter `>= 0.5` for usable rows, `>= 0.8` for grounded high-quality rows.

## Open Decisions Pending User Input

1. Country filter default — enrich all countries, or UAE-first then expand? Plan supports both via `--country`.
2. Two-tier batch implementation — confirm we want to build it next? (See "Future architecture" above.)
3. Model split: `gemini-2.5-flash` for Tier 1 batch, `gemini-2.5-pro` for Tier 2 grounded?
4. Confidence cap for Tier 1 classification — 0.7 OK?
5. Tier 2 trigger policy — `top_company=true` only, or also "in active search universe"?

## Useful Commands Reference

```bash
# Install / refresh deps
uv sync --all-groups

# Add a dep
uv add <package>
uv add --group dev <package>     # dev-only

# Run anything in the venv
uv run <cmd>

# Tests
uv run pytest
uv run pytest -k <pattern>
uv run pytest tests/test_taxonomy.py -v

# Lint
uv run ruff check src tests
uv run ruff format src tests

# Bulk enrichment
uv run batch-run --limit 100 --country "United Arab Emirates"

# Vertex deploy
uv run vertex-deploy --create
uv run vertex-deploy --list

# Supabase verification (with $SUPABASE_DB_URL set)
psql "$SUPABASE_DB_URL" -c "\d public.company_enrichment"
```

## Reference Files

- HAK MVP plan: [doc/HAK_MVP_Technical_Plan.md](doc/HAK_MVP_Technical_Plan.md)
- Source schema: [doc/supabase-schema/companies.sql](doc/supabase-schema/companies.sql)
- Enrichment schema: [doc/supabase-schema/company_enrichment.sql](doc/supabase-schema/company_enrichment.sql)
- Failures schema: [doc/supabase-schema/company_enrichment_failures.sql](doc/supabase-schema/company_enrichment_failures.sql)
- ADK agent: [src/agent/enrichment_agent.py](src/agent/enrichment_agent.py)
- Prompt + Pydantic schema: [src/agent/prompts.py](src/agent/prompts.py)
- Taxonomy: [src/agent/taxonomy.py](src/agent/taxonomy.py)
- Grounded Gemini tool: [src/tools/grounded_gemini.py](src/tools/grounded_gemini.py)
- Supabase tool: [src/tools/supabase_tool.py](src/tools/supabase_tool.py)
- Batch runner (CLI): [src/runner/batch_run.py](src/runner/batch_run.py)
- Vertex deploy: [src/deploy/vertex_deploy.py](src/deploy/vertex_deploy.py)
- Tests: [tests/](tests/)
