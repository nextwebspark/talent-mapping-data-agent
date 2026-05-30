"""Supabase tools for fetching unenriched companies and writing enrichments.

Dedup is by `company_id` (Zawya identifier), not `companies.id` - the same
Zawya company can have multiple rows in `companies` (one per source sector).
We enrich each Zawya company once per prompt_version; `company_pk` points to
the first (lowest id) matching row.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Callable

from supabase import Client, create_client
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import load_settings

log = logging.getLogger(__name__)

# PostgREST returns at most ~1000 rows per request; we page through that limit
# explicitly rather than trusting a single call.
_PAGE_SIZE = 1000


@lru_cache(maxsize=1)
def _client() -> Client:
    s = load_settings()
    return create_client(s.supabase_url, s.supabase_service_key)


def _select_all(make_query: Callable[[], Any], page_size: int = _PAGE_SIZE) -> list[dict[str, Any]]:
    """Page through a PostgREST select until all rows are fetched.

    `make_query` returns a fresh query builder for each page so we don't rely
    on the supabase-py builder being immutable across `.range()` calls.
    """
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = make_query().range(offset, offset + page_size - 1).execute()
        data = page.data or []
        rows.extend(data)
        if len(data) < page_size:
            break
        offset += page_size
    return rows


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

    enriched_rows = _select_all(
        lambda: client.table("company_enrichment")
        .select("company_id")
        .eq("prompt_version", version)
    )
    enriched_ids = {row["company_id"] for row in enriched_rows}

    failure_counts = _failure_counts_by_company(version)
    poison_ids = {cid for cid, count in failure_counts.items() if count >= max_failures_per_row}

    def _make_companies_query() -> Any:
        q = (
            client.table("companies")
            .select(
                "id, company_id, name, country, website, description, sector, "
                "top_company, phone, email, address"
            )
            .order("top_company", desc=True)
            .order("id", desc=False)
        )
        if country:
            q = q.eq("country", country)
        if sector:
            q = q.eq("sector", sector)
        if top_company_only:
            q = q.eq("top_company", True)
        return q

    rows: list[dict[str, Any]] = []
    seen_company_ids: set[str] = set()
    offset = 0
    while len(rows) < limit:
        page = _make_companies_query().range(offset, offset + _PAGE_SIZE - 1).execute()
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
        if len(data) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE

    return rows


def _failure_counts_by_company(prompt_version: str) -> dict[str, int]:
    """Aggregate failure attempts per company_id at the current prompt_version."""
    client = _client()
    rows = _select_all(
        lambda: client.table("company_enrichment_failures")
        .select("company_id")
        .eq("prompt_version", prompt_version)
    )
    counts: dict[str, int] = {}
    for row in rows:
        cid = row["company_id"]
        counts[cid] = counts.get(cid, 0) + 1
    return counts


# Transient Supabase errors (5xx, network blips) are retried; programmer errors
# (TypeError, KeyError) propagate immediately.
_TRANSIENT_WRITE_ERRORS = (ConnectionError, TimeoutError, OSError)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(_TRANSIENT_WRITE_ERRORS),
    reraise=True,
)
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


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(_TRANSIENT_WRITE_ERRORS),
    reraise=True,
)
def write_failure(
    company_row: dict[str, Any],
    error: BaseException,
    prompt_version: str,
    raw_response: str | None = None,
) -> dict[str, Any]:
    """Insert one row into company_enrichment_failures.

    Attempt number is computed from existing failures for the same
    (company_id, prompt_version). NOTE: under concurrent writers, two callers
    can compute the same attempt number. A unique constraint on
    (company_id, prompt_version, attempt) plus retry would close the race;
    we don't currently rely on that because the batch CLI is single-threaded.
    """
    client = _client()
    existing_rows = _select_all(
        lambda: client.table("company_enrichment_failures")
        .select("id")
        .eq("company_id", company_row["company_id"])
        .eq("prompt_version", prompt_version)
    )
    attempt = len(existing_rows) + 1

    payload = {
        "company_pk": company_row["id"],
        "company_id": company_row["company_id"],
        "prompt_version": prompt_version,
        "attempt": attempt,
        "error_class": type(error).__name__,
        "error_message": str(error)[:2000],
        "raw_response": raw_response[:5000] if raw_response else None,
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
