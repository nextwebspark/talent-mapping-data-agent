"""Supabase tools for fetching unenriched companies and writing enrichments.

Dedup is by `slug` (from company_seed_list), treated as `company_id` in the
enrichment table. A seed-list company can appear under multiple sectors; we
enrich each (slug) once per prompt_version. `company_pk` holds the seed list
row id (soft reference, no FK constraint).
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any, Callable

from supabase import Client, create_client
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agent.taxonomy import SECTORS
from config import load_settings

GCC_COUNTRIES: frozenset[str] = frozenset(
    {
        "United Arab Emirates",
        "Saudi Arabia",
        "Qatar",
        "Kuwait",
        "Bahrain",
        "Oman",
    }
)

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
    """Return up to `limit` seed-list companies ready to enrich.

    Source table: company_seed_list (not companies). Filters to rows where
    enrichment_status IS NULL or 'pending' — avoiding the expensive full-table
    load of company_enrichment that the old approach required. Dedupes by
    `slug` so each company is enriched once even if it appears under multiple
    sectors. Each returned row has a synthetic `company_id` key set to slug.

    Args:
        limit: Max rows.
        country: Optional country filter (exact match on company_seed_list.country).
        sector: Optional sector filter (exact match on company_seed_list.sector).
        top_company_only: Ignored — company_seed_list has no top_company field.
        prompt_version: Unused — kept for call-site back-compat.
        max_failures_per_row: Unused — 'failed' status on the seed row already
            excludes poison pills. Kept for call-site back-compat.

    Returns:
        List of {id, company_id, name, slug, country, sector, website, description}.
        `company_id` equals `slug`.
    """
    if top_company_only:
        log.warning("top_company_only ignored: company_seed_list has no top_company field")

    def _apply_status(q: Any) -> Any:
        return q.or_("enrichment_status.is.null,enrichment_status.eq.pending")

    return _fetch_seed_rows(_apply_status, limit=limit, country=country, sector=sector)


def fetch_failed_companies(
    limit: int = 50,
    country: str | None = None,
    sector: str | None = None,
    top_company_only: bool = False,
    prompt_version: str | None = None,
    max_failures_per_row: int = 3,
) -> list[dict[str, Any]]:
    """Return up to `limit` seed-list companies stamped enrichment_status='failed'.

    These are the poison-pill rows (hit the failure threshold) that
    `fetch_unenriched_companies` deliberately excludes. Used by the
    `batch-run --retry-failed` sweep — e.g. to re-run failures with a stronger
    model via ENRICHMENT_MODEL=gemini-2.5-pro. Same dedup-by-slug behavior and
    return shape as `fetch_unenriched_companies`.

    Args:
        limit: Max rows.
        country: Optional country filter (exact match on company_seed_list.country).
        sector: Optional sector filter (exact match on company_seed_list.sector).
        top_company_only: Ignored — company_seed_list has no top_company field.
        prompt_version: Unused — kept for call-site back-compat.
        max_failures_per_row: Unused — kept for call-site back-compat.

    Returns:
        List of {id, company_id, name, slug, country, sector, website, description}.
        `company_id` equals `slug`.
    """
    if top_company_only:
        log.warning("top_company_only ignored: company_seed_list has no top_company field")

    def _apply_status(q: Any) -> Any:
        return q.eq("enrichment_status", "failed")

    return _fetch_seed_rows(_apply_status, limit=limit, country=country, sector=sector)


def _fetch_seed_rows(
    apply_status: Callable[[Any], Any],
    *,
    limit: int,
    country: str | None,
    sector: str | None,
) -> list[dict[str, Any]]:
    """Page + dedup-by-slug seed rows for a given enrichment_status predicate.

    `apply_status` receives a query builder and returns it with the status
    filter applied (NULL/pending for the unenriched queue, 'failed' for the
    retry sweep). Shared by `fetch_unenriched_companies` and
    `fetch_failed_companies` so the paging/dedup loop lives in one place.
    """
    client = _client()

    def _make_seed_query() -> Any:
        q = (
            client.table("company_seed_list")
            .select("id, name, slug, country, sector, website, description")
            .order("id", desc=False)
        )
        q = apply_status(q)
        if country:
            q = q.eq("country", country)
        if sector:
            q = q.eq("sector", sector)
        return q

    rows: list[dict[str, Any]] = []
    seen_slugs: set[str] = set()
    offset = 0
    while len(rows) < limit:
        page = _make_seed_query().range(offset, offset + _PAGE_SIZE - 1).execute()
        data = page.data or []
        if not data:
            break
        for row in data:
            slug = row["slug"]
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            row["company_id"] = slug
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
def update_seed_enrichment_status(slug: str, status: str) -> None:
    """Stamp enrichment_status on all company_seed_list rows for this slug.

    One slug can appear under multiple sectors; all rows get the same status
    because enrichment is once per slug.
    """
    client = _client()
    client.table("company_seed_list").update({"enrichment_status": status}).eq(
        "slug", slug
    ).execute()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(_TRANSIENT_WRITE_ERRORS),
    reraise=True,
)
def write_enrichment(payload: dict[str, Any]) -> dict[str, Any]:
    """Upsert one row into company_enrichment and stamp seed list as enriched.

    Conflict target: (company_id, prompt_version). Returns the inserted row.
    """
    client = _client()
    result = (
        client.table("company_enrichment")
        .upsert(payload, on_conflict="company_id,prompt_version")
        .execute()
    )
    data = result.data or []
    slug = payload.get("slug") or payload.get("company_id")
    if slug:
        update_seed_enrichment_status(slug, "enriched")
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
    max_failures_per_row: int = 3,
) -> dict[str, Any]:
    """Insert one row into company_enrichment_failures.

    Attempt number is computed from existing failures for the same
    (company_id, prompt_version). When attempt count reaches max_failures_per_row,
    stamps enrichment_status = 'failed' on the seed list row (poison pill).

    NOTE: under concurrent writers two callers can compute the same attempt
    number; the batch CLI is single-threaded so this is acceptable.
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

    if attempt >= max_failures_per_row:
        slug = company_row.get("slug") or company_row.get("company_id")
        if slug:
            update_seed_enrichment_status(slug, "failed")

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
        "company_name": company_row.get("name", ""),
        "slug": company_row.get("slug", ""),
        "country": company_row.get("country", ""),
        "primary_sector": enrichment["primary_sector"],
        "sector_tags": legacy_sector_tags,
        "sub_tags": sub_tags,
        "proposed_tags": enrichment.get("proposed_tags") or [],
        "keywords": enrichment.get("keywords") or [],
        "sector_mix": enrichment.get("sector_mix") or [],
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


_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Normalise a company name into a stable dedupe slug.

    Lowercases, replaces runs of non-alphanumerics with single hyphens,
    strips leading/trailing hyphens. Empty input returns empty string.
    """
    if not name:
        return ""
    slug = _SLUG_NON_ALNUM.sub("-", name.lower()).strip("-")
    return slug


def _normalise_seed_row(row: dict[str, Any], harvest_version: str) -> dict[str, Any] | None:
    name = (row.get("name") or "").strip()
    source_url = (row.get("source_url") or "").strip()
    if not name or not source_url:
        return None
    sector = row.get("sector")
    country = row.get("country")
    if sector not in SECTORS:
        raise ValueError(f"sector {sector!r} not in SECTORS taxonomy")
    if country not in GCC_COUNTRIES:
        raise ValueError(f"country {country!r} not in GCC_COUNTRIES")
    slug = row.get("slug") or slugify(name)
    if not slug:
        return None
    return {
        "name": name,
        "slug": slug,
        "country": country,
        "sector": sector,
        "website": row.get("website"),
        "description": row.get("description"),
        "source_url": source_url,
        "source_title": row.get("source_title"),
        "source_query": row.get("source_query"),
        "harvest_version": row.get("harvest_version") or harvest_version,
        "raw_context": row.get("raw_context") or {},
    }


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(_TRANSIENT_WRITE_ERRORS),
    reraise=True,
)
def write_seed_companies(
    rows: list[dict[str, Any]],
    harvest_version: str = "v1",
) -> int:
    """Upsert seed-list rows into company_seed_list.

    Each row needs name, country, sector, source_url. slug auto-filled from
    name if absent. Rows with empty name/source_url are silently dropped.
    Invalid sector or country raises ValueError before any write.

    Returns the number of rows actually sent to the upsert.
    """
    payloads = [
        normalised
        for row in rows
        if (normalised := _normalise_seed_row(row, harvest_version)) is not None
    ]
    if not payloads:
        return 0
    client = _client()
    client.table("company_seed_list").upsert(
        payloads, on_conflict="slug,country,sector,harvest_version"
    ).execute()
    return len(payloads)


def fetch_seed_count(country: str, sector: str, harvest_version: str = "v1") -> int:
    """Count rows already harvested for a (country, sector, version) triple."""
    client = _client()
    rows = _select_all(
        lambda: client.table("company_seed_list")
        .select("id")
        .eq("country", country)
        .eq("sector", sector)
        .eq("harvest_version", harvest_version)
    )
    return len(rows)


def fetch_seed_slugs(
    country: str, sector: str, harvest_version: str = "v1"
) -> set[str]:
    """Return the set of slugs already stored for in-session dedup."""
    client = _client()
    rows = _select_all(
        lambda: client.table("company_seed_list")
        .select("slug")
        .eq("country", country)
        .eq("sector", sector)
        .eq("harvest_version", harvest_version)
    )
    return {r["slug"] for r in rows if r.get("slug")}
