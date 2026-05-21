"""Supabase tools for fetching unenriched companies and writing enrichments.

Dedup is by `company_id` (Zawya identifier), not `companies.id` - the same
Zawya company can have multiple rows in `companies` (one per source sector).
We enrich each Zawya company once per prompt_version; `company_pk` points to
the first (lowest id) matching row.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from supabase import Client, create_client

from config import load_settings

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _client() -> Client:
    s = load_settings()
    return create_client(s.supabase_url, s.supabase_service_key)


def fetch_unenriched_companies(
    limit: int = 50,
    country: str | None = None,
    sector: str | None = None,
    top_company_only: bool = False,
    prompt_version: str | None = None,
    max_failures_per_row: int = 3,
) -> list[dict[str, Any]]:
    """Return up to `limit` companies that have no enrichment for the current
    prompt_version.

    Dedupes by `company_id` so each Zawya company is enriched once. Prefers
    rows with `top_company=true`, then lowest id.

    Args:
        limit: Max rows.
        country: Optional country filter (exact match on companies.country).
        sector: Optional source-sector filter (exact match on companies.sector).
        top_company_only: If True, only return rows with top_company=true.
        prompt_version: If None, uses settings.prompt_version.
        max_failures_per_row: Skip rows that have >= this many recorded
            failures at the current prompt_version (poison pill protection).

    Returns:
        List of {id, company_id, name, country, website, description, sector,
        top_company, phone, email, address}.
    """
    settings = load_settings()
    version = prompt_version or settings.prompt_version
    client = _client()

    enriched = (
        client.table("company_enrichment")
        .select("company_id")
        .eq("prompt_version", version)
        .execute()
    )
    enriched_ids = {row["company_id"] for row in (enriched.data or [])}

    failure_counts = _failure_counts_by_company(version)
    poison_ids = {cid for cid, count in failure_counts.items() if count >= max_failures_per_row}

    query = (
        client.table("companies")
        .select(
            "id, company_id, name, country, website, description, sector, "
            "top_company, phone, email, address"
        )
        .order("top_company", desc=True)
        .order("id", desc=False)
    )
    if country:
        query = query.eq("country", country)
    if sector:
        query = query.eq("sector", sector)
    if top_company_only:
        query = query.eq("top_company", True)

    rows: list[dict[str, Any]] = []
    seen_company_ids: set[str] = set()
    page_size = 200
    offset = 0
    while len(rows) < limit:
        page = query.range(offset, offset + page_size - 1).execute()
        data = page.data or []
        if not data:
            break
        for row in data:
            cid = row["company_id"]
            if cid in enriched_ids or cid in seen_company_ids or cid in poison_ids:
                continue
            seen_company_ids.add(cid)
            rows.append(row)
            if len(rows) >= limit:
                break
        if len(data) < page_size:
            break
        offset += page_size

    return rows


def _failure_counts_by_company(prompt_version: str) -> dict[str, int]:
    """Aggregate failure attempts per company_id at the current prompt_version."""
    client = _client()
    result = (
        client.table("company_enrichment_failures")
        .select("company_id")
        .eq("prompt_version", prompt_version)
        .execute()
    )
    counts: dict[str, int] = {}
    for row in result.data or []:
        cid = row["company_id"]
        counts[cid] = counts.get(cid, 0) + 1
    return counts


def write_enrichment(payload: dict[str, Any]) -> dict[str, Any]:
    """Upsert one row into company_enrichment.

    Conflict target: (company_id, prompt_version). Returns the inserted row.
    """
    client = _client()
    result = (
        client.table("company_enrichment")
        .upsert(payload, on_conflict="company_id,prompt_version")
        .execute()
    )
    data = result.data or []
    return data[0] if data else {}


def write_failure(
    company_row: dict[str, Any],
    error: BaseException,
    prompt_version: str,
    raw_response: str | None = None,
) -> dict[str, Any]:
    """Insert one row into company_enrichment_failures.

    Attempt number is computed from existing failures for the same
    (company_id, prompt_version).
    """
    client = _client()
    existing = (
        client.table("company_enrichment_failures")
        .select("id")
        .eq("company_id", company_row["company_id"])
        .eq("prompt_version", prompt_version)
        .execute()
    )
    attempt = len(existing.data or []) + 1

    payload = {
        "company_pk": company_row["id"],
        "company_id": company_row["company_id"],
        "prompt_version": prompt_version,
        "attempt": attempt,
        "error_class": type(error).__name__,
        "error_message": str(error)[:2000],
        "raw_response": (raw_response or None)[:5000] if raw_response else None,
    }
    result = client.table("company_enrichment_failures").insert(payload).execute()
    data = result.data or []
    return data[0] if data else {}


def build_enrichment_payload(
    company_row: dict[str, Any],
    enrichment: dict[str, Any],
    model: str,
    prompt_version: str,
) -> dict[str, Any]:
    """Map an EnrichmentResult dict + source company row into a DB payload."""
    sub_tags = enrichment.get("sub_tags") or []
    # sector_tags is the legacy v1/v2 array column (NOT NULL). For v3+, mirror
    # sub_tags into it so downstream queries written against the old column
    # name keep working until they migrate.
    legacy_sector_tags = enrichment.get("sector_tags") or sub_tags
    return {
        "company_pk": company_row["id"],
        "company_id": company_row["company_id"],
        "primary_sector": enrichment["primary_sector"],
        "sector_tags": legacy_sector_tags,
        "sub_tags": sub_tags,
        "proposed_tags": enrichment.get("proposed_tags") or [],
        "keywords": enrichment.get("keywords") or [],
        "sector_mix": enrichment.get("sector_mix") or [],
        "adjacent_sectors": enrichment.get("adjacent_sectors") or [],
        "tagline": enrichment.get("tagline"),
        "business_description": enrichment.get("business_description"),
        "employee_band": enrichment.get("employee_band"),
        "employee_count_estimate": enrichment.get("employee_count_estimate"),
        "revenue_band": enrichment.get("revenue_band"),
        "revenue_estimate_usd": enrichment.get("revenue_estimate_usd"),
        "is_listed": enrichment.get("is_listed"),
        "hq_city": enrichment.get("hq_city"),
        "website": enrichment.get("website"),
        "phone": enrichment.get("phone"),
        "email": enrichment.get("email"),
        "address": enrichment.get("address"),
        "confidence": enrichment["confidence"],
        "sources": enrichment.get("sources") or [],
        "model": model,
        "prompt_version": prompt_version,
        "raw_response": enrichment,
    }
