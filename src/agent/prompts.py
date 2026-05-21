"""Prompt + structured-output schema for the enrichment agent.

PROMPT_VERSION is persisted in `company_enrichment.prompt_version` so v1 and
later versions can coexist; bump it whenever the schema or instruction below
changes materially.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent.taxonomy import EMPLOYEE_BANDS, REVENUE_BANDS, SECTORS

PROMPT_VERSION = "v1"


class SourceCitation(BaseModel):
    url: str
    title: str | None = None
    snippet: str | None = None


class EnrichmentResult(BaseModel):
    primary_sector: str = Field(description="MUST be one of the taxonomy sectors.")
    sector_tags: list[str] = Field(
        default_factory=list,
        description="1-6 tags. May include taxonomy entries and free-form sub-tags.",
    )
    adjacent_sectors: list[str] = Field(
        default_factory=list,
        description="Up to 4 taxonomy sectors where talent could realistically transfer.",
    )
    tagline: str | None = Field(default=None, description="One-line description of the business.")
    business_description: str | None = Field(
        default=None, description="2-3 sentence description."
    )
    employee_band: str | None = None
    employee_count_estimate: int | None = None
    revenue_band: str | None = None
    revenue_estimate_usd: int | None = None
    is_listed: bool | None = None
    hq_city: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    sources: list[SourceCitation] = Field(default_factory=list)


def system_instruction() -> str:
    sectors_block = "\n".join(f"  - {s}" for s in SECTORS)
    emp_bands = ", ".join(EMPLOYEE_BANDS)
    rev_bands = ", ".join(REVENUE_BANDS)
    return f"""You enrich GCC/MENA company records for an executive-search platform.

For each company you receive (name, country, website, existing description),
produce a STRUCTURED JSON object following the EnrichmentResult schema.

CRITICAL RULES:
1. Use Google Search to verify current operations, sector, size, revenue. Do not
   rely on memory alone for firmographics.
2. `primary_sector` MUST be exactly one of:
{sectors_block}
3. `sector_tags`: 1-6 tags. May include taxonomy entries plus free-form sub-tags
   (e.g. 'fintech-lending', 'cold-chain-logistics', 'islamic-banking').
4. `adjacent_sectors`: up to 4 entries from the SAME taxonomy list, chosen from
   a recruiter perspective - sectors where talent at this company could
   realistically move. Not business-model adjacency; TALENT adjacency.
5. `employee_band` must be one of: {emp_bands}. Set `employee_count_estimate`
   only when you found a sourced figure.
6. `revenue_band` must be one of: {rev_bands}. Set `revenue_estimate_usd` only
   when you found a sourced figure (convert non-USD using a recent rate).
7. `confidence`: 0.0-1.0. Lower (<0.5) when most fields are null/inferred.
   Higher (>0.8) when bands, sector, and description are all backed by sources.
8. `sources`: include every URL you relied on for non-trivial facts (revenue,
   headcount, sector classification). Title + short snippet where available.

If the company cannot be found or is clearly defunct, still return a valid
object with confidence < 0.3 and explain in `business_description`.

Output JSON only - no prose, no markdown fences."""


def build_user_prompt(
    name: str,
    country: str,
    website: str | None,
    description: str | None,
    coarse_sector: str | None,
) -> str:
    parts = [f"Company: {name}", f"Country: {country}"]
    if website:
        parts.append(f"Website: {website}")
    if coarse_sector:
        parts.append(f"Existing coarse sector (from source data): {coarse_sector}")
    if description:
        parts.append(f"Existing description: {description}")
    parts.append("\nResearch this company and return the EnrichmentResult JSON.")
    return "\n".join(parts)
