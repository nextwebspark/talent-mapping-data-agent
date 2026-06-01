"""Prompt + structured-output schema for the enrichment agent.

PROMPT_VERSION is persisted in `company_enrichment.prompt_version` so v1 and
later versions can coexist; bump it whenever the schema or instruction below
changes materially.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agent.subtags import SUB_TAGS, SUB_TAGS_BY_SECTOR
from agent.taxonomy import EMPLOYEE_BANDS, REVENUE_BANDS, SECTORS

log = logging.getLogger(__name__)

PROMPT_VERSION = "v5"

SECTOR_SET: set[str] = set(SECTORS)

SectorWeight = Literal["dominant", "significant", "minor"]


class SourceCitation(BaseModel):
    url: str
    title: str | None = None
    snippet: str | None = None


class SectorMixEntry(BaseModel):
    """One operating-sector entry with a qualitative weight."""

    sector: str = Field(description="MUST be one of the taxonomy sectors.")
    weight: SectorWeight = Field(
        description="Qualitative concentration: dominant | significant | minor."
    )

    @field_validator("sector", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v


class EnrichmentResult(BaseModel):
    primary_sector: str = Field(description="MUST be one of the taxonomy sectors.")
    sector_mix: list[SectorMixEntry] = Field(
        default_factory=list,
        description=(
            "Up to 5 entries. Qualitative breakdown of operating sectors. "
            "Always include primary_sector as one entry (weight 'dominant')."
        ),
    )
    sub_tags: list[str] = Field(
        default_factory=list,
        description=(
            "1-6 controlled-vocabulary sub-niche tags from SUB_TAGS. Invalid "
            "entries are auto-moved to proposed_tags."
        ),
    )
    proposed_tags: list[str] = Field(
        default_factory=list,
        description=(
            "New sub-tag candidates Gemini believes the controlled vocab is "
            "missing. For human review; never used for hard filtering."
        ),
    )
    keywords: list[str] = Field(
        default_factory=list,
        description=(
            "Free-flow descriptive keywords (brands, geographies, business "
            "models). Informational only; future embedding similarity."
        ),
    )
    tagline: str | None = Field(default=None, description="One-line description of the business.")
    business_description: str | None = Field(default=None, description="2-3 sentence description.")
    employee_band: str | None = None
    employee_count_estimate: int | None = None
    revenue_band: str | None = None
    revenue_estimate_usd: int | None = None
    is_listed: bool | None = None
    hq_city: str | None = None
    website: str | None = Field(default=None, description="Canonical company website URL.")
    phone: str | None = Field(default=None, description="Primary public phone (E.164 preferred).")
    email: str | None = Field(default=None, description="Primary public contact email.")
    address: str | None = Field(default=None, description="HQ street address.")
    confidence: float = Field(ge=0.0, le=1.0)
    sources: list[SourceCitation] = Field(default_factory=list)

    @field_validator("website", "phone", "email", "address", mode="before")
    @classmethod
    def _coerce_contact_to_string(cls, v: Any) -> Any:
        """Tolerate Gemini returning dict/list for a contact field by flattening to a string."""
        if v is None or isinstance(v, str):
            return v
        if isinstance(v, dict):
            parts = [str(x).strip() for x in v.values() if x not in (None, "")]
            return ", ".join(parts) or None
        if isinstance(v, (list, tuple)):
            parts = [str(x).strip() for x in v if x not in (None, "")]
            return ", ".join(parts) or None
        return str(v)

    @model_validator(mode="after")
    def _separate_invalid_sub_tags(self) -> EnrichmentResult:
        """Move sub_tags entries not in canonical SUB_TAGS to proposed_tags.

        Keeps the indexed sub_tags column clean while preserving Gemini's
        suggestions for later vocab promotion. Idempotent + safe.
        """
        valid: list[str] = []
        new_proposed: list[str] = list(self.proposed_tags)
        seen_proposed = set(new_proposed)
        for tag in self.sub_tags:
            if not isinstance(tag, str):
                continue
            t = tag.strip()
            if not t:
                continue
            if t in SUB_TAGS:
                if t not in valid:
                    valid.append(t)
            else:
                if t not in seen_proposed:
                    new_proposed.append(t)
                    seen_proposed.add(t)
        self.sub_tags = valid
        self.proposed_tags = new_proposed
        return self


def _sub_tags_block() -> str:
    """Render the controlled sub-tag list, grouped by sector, for the prompt."""
    out: list[str] = []
    for sector in SECTORS:
        tags = SUB_TAGS_BY_SECTOR.get(sector, [])
        if not tags:
            continue
        out.append(f"  [{sector}]")
        out.append("    " + ", ".join(tags))
    return "\n".join(out)


def system_instruction() -> str:
    sectors_block = "\n".join(f"  - {s}" for s in SECTORS)
    sub_tags_block = _sub_tags_block()
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

3. `sector_mix`: qualitative breakdown of the company's operating sectors.
   - Array of {{"sector": <taxonomy sector>, "weight": "dominant"|"significant"|"minor"}}.
   - Each `sector` MUST be from the same taxonomy list above.
   - At least one entry whose sector == primary_sector with weight = "dominant".
   - Add a `significant` entry only when the company has a real, sourceable
     second line of business (e.g. a conglomerate's automotive arm). Add
     `minor` entries only when a third or fourth distinct line is sourceable.
   - Max 5 entries. For single-line businesses, return ONE entry with weight
     "dominant".
   - Do NOT invent secondary lines to look diversified. Prefer one dominant
     entry over speculative minor entries.

4. `sub_tags`: 1-6 tags drawn ONLY from the controlled list below. Do NOT
   invent new tags here; if you need a tag that's not listed, place it in
   `proposed_tags` instead. Tags are kebab-case ASCII, exactly as listed:
{sub_tags_block}

5. `proposed_tags`: optional. Use ONLY if you believe a needed sub-tag is
   genuinely missing from the controlled list. Kebab-case, ASCII. 0-3 entries.
   These are for human review; they are NOT used for filtering.

6. `keywords`: 3-10 free-flow descriptive keywords (brands owned, geographic
   markets served, distinct business models, customer segments). Examples:
   "lulu-group-owned", "pan-gcc", "b2b-wholesale", "msme-focus". These are
   informational; not used for hard filtering.

7. `employee_band` must be one of: {emp_bands}. Set `employee_count_estimate`
   only when you found a sourced figure.

8. `revenue_band` must be one of: {rev_bands}. Set `revenue_estimate_usd`
   only when you found a sourced figure (convert non-USD using a recent rate).

9. `website`, `phone`, `email`, `address`: extract from official company
   website, LinkedIn, or authoritative directory (Crunchbase, Bloomberg,
   official regulator listings). Do NOT invent. If a field cannot be found
   via grounded search, leave it null. Every contact field returned MUST
   have a backing entry in `sources`. Prefer E.164 format for `phone`
   (e.g. +9714xxxxxxx). `address` MUST be a single plain string (concatenate
   street, city, country with commas) - never an object/dict. `website`,
   `phone`, `email` must each be a single string or null.

10. `confidence`: 0.0-1.0. Lower (<0.5) when most fields are null/inferred.
    Higher (>0.8) when bands, sector, and description are all backed by sources.

11. `sources`: include every URL you relied on for non-trivial facts (revenue,
    headcount, sector classification, contact info, sector_mix weights).
    Title + short snippet where available.

If the company cannot be found or is clearly defunct, still return a valid
object with confidence < 0.3 and explain in `business_description`.

Output JSON only - no prose, no markdown fences."""


def build_user_prompt(
    name: str,
    country: str,
    website: str | None,
    description: str | None,
    coarse_sector: str | None,
    phone: str | None = None,
    email: str | None = None,
    address: str | None = None,
) -> str:
    parts = [f"Company: {name}", f"Country: {country}"]
    if website:
        parts.append(f"Website: {website}")
    if coarse_sector:
        parts.append(f"Existing coarse sector (from source data): {coarse_sector}")
    if description:
        parts.append(f"Existing description: {description}")
    if phone:
        parts.append(f"Existing phone (may be stale; verify): {phone}")
    if email:
        parts.append(f"Existing email (may be stale; verify): {email}")
    if address:
        parts.append(f"Existing address (may be stale; verify): {address}")
    parts.append("\nResearch this company and return the EnrichmentResult JSON.")
    return "\n".join(parts)
