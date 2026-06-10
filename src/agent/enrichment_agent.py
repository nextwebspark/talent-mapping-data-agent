"""ADK Agent definition for the enrichment workflow.

Exposed as `root_agent` so `adk run` / `adk web` and the Vertex deploy helper
can discover it. The agent's job is to orchestrate one or more enrichment
cycles by calling `fetch_unenriched_companies`, then `enrich_company_grounded`
per row, then `write_enrichment`.
"""

from __future__ import annotations

from google.adk.agents import Agent

from agent.prompts import PROMPT_VERSION
from config import load_settings
from tools.grounded_gemini import enrich_company_grounded
from tools.supabase_tool import (
    build_enrichment_payload,
    fetch_unenriched_companies,
    write_enrichment,
)

AGENT_INSTRUCTION = f"""You are the Company Enrichment Agent for HAK.

Your goal: enrich companies in the Supabase `companies` table with structured
sector tags, size, revenue, and tagline. Prompt version: {PROMPT_VERSION}.

Workflow per request:
1. Call `fetch_unenriched_companies(limit, country)` to get a batch.
2. For each company, call `enrich_company_grounded(name, country, website,
   description, coarse_sector)`. This performs the grounded Gemini call and
   returns the structured EnrichmentResult JSON.
3. Build the payload via `build_enrichment_payload(company_row, enrichment,
   model, prompt_version)` and write it with `write_enrichment(payload)`.
4. Report a short summary: count enriched, count failed, any sector
   distribution surprises.

If a company fails validation or grounding, log it and continue with the next.
Do not fabricate enrichment values; rely on the tool output."""


root_agent = Agent(
    name="company_enricher",
    model=load_settings().model,  # follows ENRICHMENT_MODEL; default gemini-2.5-flash
    description=(
        "Enriches GCC/MENA companies with multi-tag sectors, size band, revenue band, "
        "and tagline using grounded Gemini + Supabase."
    ),
    instruction=AGENT_INSTRUCTION,
    tools=[
        fetch_unenriched_companies,
        enrich_company_grounded,
        write_enrichment,
        build_enrichment_payload,
    ],
)
