# Talent Mapping — Data Agent

> Grounded LLM enrichment pipeline that turns raw scraped company records into a structured, multi-tag firmographic dataset for executive-search talent mapping in the GCC/MENA region.

This repository implements the **company-enrichment** layer of the **HAK Talent Intelligence Platform**: an AI-powered executive search operating system focused on the UAE market. It corresponds to **Layer 2 — Stage A** of the HAK MVP plan: *Company Universe Construction*. See [`doc/HAK_MVP_Technical_Plan.md`](doc/HAK_MVP_Technical_Plan.md) for the broader product context.

## Why this project exists

A recruiter searching for a "Group CFO with GCC real-estate experience" needs more than a coarse sector label. They need:

- A controlled, multi-tag sector taxonomy
- Adjacent-sector mapping (where talent realistically transfers from)
- Firmographic context (size, revenue, listed status)
- Evidence trails so the AI's reasoning is auditable

The source data — scraped from `zawya.com` into a Supabase `companies` table — only carries a single coarse sector string per row (e.g. `"Retailers"`, `"Utility"`). This project enriches each Zawya company once into a clean, structured row that downstream search/matching can query with simple SQL.

## What it does

```
┌──────────────────────────────────────────────────────────────────────┐
│  Source: public.companies   (Zawya scrape — name, sector, country)   │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
                             ▼
             ┌────────────────────────────────────┐
             │  Enrichment Agent                  │
             │                                    │
             │  Vertex AI Gemini 2.5 Pro          │
             │  + Google Search grounding tool    │
             │  + Pydantic schema validation      │
             └────────────────────┬───────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Output: public.company_enrichment                                   │
│  • primary_sector (1 of 20 taxonomy buckets)                         │
│  • sector_tags[]            (taxonomy + free-form sub-tags)          │
│  • adjacent_sectors[]       (recruiter-perspective talent adjacency) │
│  • tagline + business_description                                    │
│  • employee_band + estimate                                          │
│  • revenue_band + estimate                                           │
│  • is_listed, hq_city                                                │
│  • confidence (0-1)                                                  │
│  • sources[] (grounded URLs from Google Search citations)            │
│  • model, prompt_version, raw_response, enriched_at                  │
│                                                                      │
│  Plus: public.company_enrichment_failures (audit trail + poison-     │
│  pill protection on re-runs)                                         │
└──────────────────────────────────────────────────────────────────────┘
```

Downstream consumers query this table with plain SQL (no LLM at query time):

```sql
-- Companies relevant to a "real estate" search mandate, including adjacent talent
SELECT c.name, e.primary_sector, e.sector_tags, e.employee_band, e.confidence
FROM   companies c
JOIN   company_enrichment e ON e.company_pk = c.id
WHERE (e.primary_sector = 'Real Estate Development'
       OR e.adjacent_sectors && ARRAY['Real Estate Development'])
  AND  e.employee_band IN ('1k-5k', '5k-10k', '10k+')
  AND  e.confidence >= 0.7;
```

## Architecture

The repository ships **two entry points** that share the same underlying tools:

| Entry point | Mechanism | Use case |
|---|---|---|
| **Batch runner** (`uv run batch-run …`) | Plain Python loop over `fetch → enrich → write`. No LLM-driven orchestration. | Bulk runs (10–5K rows), scheduled jobs, deterministic re-runs. |
| **ADK agent** (`adk web` / Vertex Agent Engine) | LLM-driven via Google [Agent Development Kit](https://google.github.io/adk-docs/). | Interactive / app-triggered single-company enrichment, exploratory runs. |

Both reuse the same tools: `enrich_company_grounded`, `write_enrichment`, `fetch_unenriched_companies`, `write_failure`, `build_enrichment_payload`. They differ only in *who drives the loop* — code vs. LLM.

### Why no vector database

For the MVP, sector/size/country filtering is categorical and array-based. PostgreSQL with GIN indexes on `sector_tags[]` and `adjacent_sectors[]` handles every documented universe query in milliseconds. A vector database would add an entire system component for no benefit at the current scale.

Semantic mandate-to-company matching (HAK plan §2.2.1 step 3) will need vectors. When that lands, the recommended path is `pgvector` inside the same Supabase Postgres — not a separate Pinecone/Qdrant instance.

### Tech stack

| Layer | Choice | Rationale |
|---|---|---|
| **LLM** | Vertex AI Gemini 2.5 Pro | Strong taxonomy adherence, integrated Google Search grounding, billable to GCP credits |
| **Grounding** | `google_search` tool | Live web verification of revenue / headcount / sector activity |
| **Validation** | Pydantic | Hard-validates LLM output against `EnrichmentResult` schema before DB write |
| **Database** | Supabase Postgres | Already in use for source scrape; GIN array indexes serve all universe queries |
| **Agent runtime** | Google ADK + Vertex AI Agent Engine | Native Gemini integration; managed deploy target |
| **Package manager** | `uv` | Fast resolver, lockfile, modern Python tooling |
| **Tests** | `pytest` + monkeypatch | 40 unit tests, fully mocked, no network |
| **Retries** | `tenacity` | Exponential backoff on Gemini transient errors |

## Sector taxonomy (20 buckets)

UAE/GCC-flavored. Defined in [`src/agent/taxonomy.py`](src/agent/taxonomy.py).

```
Banking & Financial Services        Telecommunications
Insurance                           Technology & Software
Capital Markets & Asset Management  Retail & Consumer Goods
Real Estate Development             Hospitality, Travel & Tourism
Construction & Engineering          Healthcare & Pharmaceuticals
Oil & Gas — Upstream                Logistics, Shipping & Ports
Oil & Gas — Downstream/Petrochem    Aviation & Aerospace
Power & Utilities                   Manufacturing & Industrial
Media, Entertainment & Advertising  Education & Training
Professional Services               Conglomerates / Family Groups / Holdings
```

The taxonomy is paired with an **adjacency map** describing recruiter-perspective talent mobility (e.g. retail talent often moves into hospitality and logistics). The map is used by both the prompt and downstream universe queries.

## Getting started

### Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- [`gcloud` CLI](https://cloud.google.com/sdk/docs/install) (for Vertex AI authentication)
- A Supabase project with `public.companies` populated
- A GCP project with Vertex AI API enabled

### Setup

```bash
git clone https://github.com/nextwebspark/talent-mapping-data-agent.git
cd talent-mapping-data-agent

# Authenticate to GCP (one-time)
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_GCP_PROJECT
gcloud services enable aiplatform.googleapis.com

# Configure env
cp .env.example .env
# Fill in: SUPABASE_URL, SUPABASE_SERVICE_KEY (or SUPABASE_KEY),
#          GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION,
#          GOOGLE_GENAI_USE_VERTEXAI=true

# Install dependencies (creates .venv via uv)
uv sync --all-groups

# Apply database schemas (in Supabase SQL Editor, in order):
#   doc/supabase-schema/company_enrichment.sql
#   doc/supabase-schema/company_enrichment_failures.sql
```

### Run the tests

```bash
uv run pytest                # all 40 tests, no API calls
uv run pytest -k taxonomy    # single module
uv run pytest -v             # verbose
```

### First enrichment

Always start with a dry run:

```bash
# Print JSON for 5 retail companies, do NOT write to DB
uv run batch-run --limit 5 --sector "Retailers" --dry-run
```

When the output looks good, drop the flag to persist:

```bash
uv run batch-run --limit 5 --sector "Retailers"
```

Sample output (real run, 2026-05-21):

```
[1/5] Rivoli Group LLC (4297788558)              → Retail & Consumer Goods    | conf 0.90
[2/5] Jumbo Electronics Co Ltd LLC (4297498740)  → Conglomerates / Holdings   | conf 0.85
[3/5] Axiom Telecom LLC (5000021359)             → Logistics, Shipping & Ports | conf 0.85
[4/5] Al Habtoor Motors Co LLC (5034762771)      → Retail & Consumer Goods    | conf 0.90
[5/5] Paris Gallery LLC (5000043301)             → Retail & Consumer Goods    | conf 0.80
Done. enriched=5 failed=0 low_confidence=0
```

### CLI flags

| Flag | Default | Meaning |
|---|---|---|
| `--limit` | `10` | Max companies in this run |
| `--country` | none | Exact match on `companies.country` |
| `--sector` | none | Exact match on the coarse Zawya sector (input filter, not output) |
| `--top-company-only` | `false` | Only enrich rows flagged `top_company=true` |
| `--dry-run` | `false` | Print results, do not write to DB |
| `--sleep` | `0.5` | Seconds between Gemini calls |
| `--max-failures-per-row` | `3` | Skip rows that already failed this many times at current `prompt_version` |
| `--max-failures-before-stop` | none | Abort batch once this many failures occur (quota guard) |
| `--log-level` | `INFO` | Logging level |

### Inspect results

```sql
SELECT c.name, e.primary_sector, e.sector_tags, e.adjacent_sectors,
       e.employee_band, e.revenue_band, e.confidence
FROM   companies c
JOIN   company_enrichment e ON e.company_pk = c.id
ORDER BY e.enriched_at DESC
LIMIT 20;

-- Sector distribution
SELECT primary_sector, COUNT(*) FROM company_enrichment GROUP BY 1 ORDER BY 2 DESC;

-- Failures summary
SELECT error_class, COUNT(*) FROM company_enrichment_failures
WHERE prompt_version='v1' GROUP BY 1 ORDER BY 2 DESC;
```

## Deploying to Vertex AI Agent Engine

The ADK agent in [`src/agent/enrichment_agent.py`](src/agent/enrichment_agent.py) can be deployed to **Vertex AI Agent Engine** for interactive / app-triggered enrichment.

```bash
# One-time
gsutil mb -l us-central1 gs://hak-enrichment-staging

# In .env, set GCS_STAGING_BUCKET=gs://hak-enrichment-staging

uv run vertex-deploy --create               # create new instance
uv run vertex-deploy --list                 # list existing
uv run vertex-deploy --update <resource>    # update after code changes
uv run vertex-deploy --delete <resource>
```

For pure batch workloads, a future Cloud Run Job wrapping `batch_run.py` is the preferred cost-optimised path (documented as future work in [`CLAUDE.md`](CLAUDE.md)).

## Project structure

```
.
├── CLAUDE.md                              # AI agent / session handoff doc
├── README.md                              # this file
├── pyproject.toml                         # uv-managed deps + pytest config
├── uv.lock
├── .env.example
├── .gitignore                             # blocks .env, *.json keys, etc.
├── doc/
│   ├── HAK_MVP_Technical_Plan.md          # full product plan
│   └── supabase-schema/
│       ├── companies.sql                  # source table (existing)
│       ├── company_enrichment.sql         # enrichment output
│       └── company_enrichment_failures.sql # failure audit log
├── src/
│   ├── config.py                          # env loader
│   ├── agent/
│   │   ├── taxonomy.py                    # SECTORS + ADJACENCY map
│   │   ├── prompts.py                     # PROMPT_VERSION + Pydantic schema
│   │   └── enrichment_agent.py            # ADK root_agent
│   ├── tools/
│   │   ├── grounded_gemini.py             # Gemini + google_search tool
│   │   └── supabase_tool.py               # fetch / write / failure logging
│   ├── runner/
│   │   └── batch_run.py                   # bulk CLI workflow
│   └── deploy/
│       └── vertex_deploy.py               # Vertex Agent Engine deploy
└── tests/                                 # 40 mocked unit tests
```

## Roadmap

| Status | Item |
|---|---|
| ✅ | Single-tier real-time grounded enrichment pipeline |
| ✅ | Failure audit table + poison-pill protection |
| ✅ | Vertex AI integration (gemini-2.5-pro, us-central1) |
| ✅ | 40-test unit suite |
| ⏳ | Two-tier batch architecture (cheap classification batch + selective grounded firmographics) |
| ⏳ | Vertex AI Batch Prediction integration (~50% cost reduction) |
| ⏳ | Prompt v2 to populate `tagline`, `hq_city`, `is_listed` more reliably |
| ⏳ | `pgvector` column + embedding backfill for semantic universe expansion |
| ⏳ | `--company-id` / `--min-confidence` / `--primary-sector` filters |
| ⏳ | Cloud Run Job wrapper for scheduled bulk runs |
| ⏳ | Pre-commit secret scan (`gitleaks` / `detect-secrets`) |

## Security

- `.env` is gitignored. `.env.example` is committed.
- `.gitignore` blocks `*.json` keys, `*.pem`, `*.key`, `service_account*.json`, `application_default_credentials.json`.
- No hardcoded secrets in source.
- Supabase queries use parameterised `.eq()` builders — no SQL string concatenation.
- LLM output validated against Pydantic schema before write.
- For production Vertex deployments, move `SUPABASE_SERVICE_KEY` from `env_vars` to GCP Secret Manager.

If you discover a vulnerability, do not open a public issue; contact the repository owner directly.

## Contributing

This repository follows [Conventional Commits](https://www.conventionalcommits.org/). PRs should:

- Pass `uv run pytest`
- Pass `uv run ruff check src tests`
- Update [`CLAUDE.md`](CLAUDE.md) when changing architecture or conventions
- Bump `PROMPT_VERSION` in `src/agent/prompts.py` when changing the prompt or output schema

## License

Proprietary. See repository owner for licensing terms.

---

For architecture context, run-flow details, GCP setup steps, and open decisions, read [`CLAUDE.md`](CLAUDE.md).
