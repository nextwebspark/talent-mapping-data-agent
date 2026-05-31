"""Tests for supabase tool. Mocks supabase client; verifies dedup logic and
payload mapping without touching the network."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools import supabase_tool


def _result(data):
    return SimpleNamespace(data=data)


class CompaniesQuery:
    """Mimics PostgREST query builder for the companies table.

    Captures .eq() filters so tests can assert sector/country/top_company
    plumbing without needing real PostgREST semantics.
    """

    def __init__(self, rows, filters: dict | None = None):
        self.rows = rows
        self.filters = filters or {}

    def select(self, *args, **kwargs):
        return self

    def eq(self, column, value):
        new_filters = dict(self.filters)
        new_filters[column] = value
        return CompaniesQuery(self.rows, new_filters)

    def order(self, *args, **kwargs):
        return self

    def _apply_filters(self):
        filtered = self.rows
        for col, val in self.filters.items():
            filtered = [r for r in filtered if r.get(col) == val]
        return filtered

    def range(self, start, end):
        return _ExecResult(self._apply_filters()[start : end + 1])

    def execute(self):
        return _result(self._apply_filters())


class _ExecResult:
    def __init__(self, rows):
        self._rows = rows

    def execute(self):
        return _result(self._rows)


class EnrichmentTable:
    """Routes select/upsert for company_enrichment."""

    def __init__(self, parent: "FakeClient"):
        self._parent = parent

    def select(self, *a, **kw):
        return self

    def eq(self, *a, **kw):
        return self

    def range(self, start, end):
        return _ExecResult(self._parent.enriched_rows[start : end + 1])

    def execute(self):
        return _result(self._parent.enriched_rows)

    def upsert(self, payload, on_conflict=None):
        self._parent.upsert_capture.append({"payload": payload, "on_conflict": on_conflict})
        return _ExecResult([payload])


class FailuresTable:
    """Routes select/insert for company_enrichment_failures."""

    def __init__(self, parent: "FakeClient"):
        self._parent = parent
        self._filters: dict = {}

    def select(self, *a, **kw):
        new = FailuresTable(self._parent)
        new._filters = dict(self._filters)
        return new

    def eq(self, column, value):
        new = FailuresTable(self._parent)
        new._filters = dict(self._filters)
        new._filters[column] = value
        return new

    def _filtered_rows(self):
        rows = self._parent.failure_rows
        for col, val in self._filters.items():
            rows = [r for r in rows if r.get(col) == val]
        return rows

    def range(self, start, end):
        return _ExecResult(self._filtered_rows()[start : end + 1])

    def execute(self):
        return _result(self._filtered_rows())

    def insert(self, payload):
        self._parent.failure_insert_capture.append(payload)
        # Simulate the insert showing up in subsequent selects
        self._parent.failure_rows.append(payload)
        return _ExecResult([payload])


class FakeClient:
    """Routes table() calls to controlled responses."""

    def __init__(
        self,
        *,
        seed_rows,
        enriched_rows,
        failure_rows: list | None = None,
        upsert_capture: list,
        failure_insert_capture: list,
    ):
        self.seed_rows = seed_rows
        self.enriched_rows = enriched_rows
        self.failure_rows = failure_rows or []
        self.upsert_capture = upsert_capture
        self.failure_insert_capture = failure_insert_capture

    def table(self, name):
        if name == "company_seed_list":
            return CompaniesQuery(self.seed_rows)
        if name == "company_enrichment":
            return EnrichmentTable(self)
        if name == "company_enrichment_failures":
            return FailuresTable(self)
        raise AssertionError(f"unexpected table {name}")


@pytest.fixture
def fake_client_factory(monkeypatch):
    captured: list[dict] = []
    failure_captured: list[dict] = []

    def _make(*, seed_rows=None, enriched_rows, failure_rows=None):
        client = FakeClient(
            seed_rows=seed_rows or [],
            enriched_rows=enriched_rows,
            failure_rows=failure_rows or [],
            upsert_capture=captured,
            failure_insert_capture=failure_captured,
        )
        monkeypatch.setattr(supabase_tool, "_client", lambda: client)
        return client, captured, failure_captured

    return _make


def test_build_enrichment_payload_maps_fields(sample_company, sample_enrichment):
    payload = supabase_tool.build_enrichment_payload(
        sample_company, sample_enrichment, model="gemini-2.5-pro", prompt_version="v3"
    )
    assert payload["company_pk"] == sample_company["id"]
    assert payload["company_id"] == sample_company["company_id"]
    assert payload["company_name"] == sample_company["name"]
    assert payload["slug"] == sample_company["slug"]
    assert payload["country"] == sample_company["country"]
    assert payload["primary_sector"] == "Real Estate Development"
    # legacy sector_tags mirrors sub_tags for back-compat
    assert payload["sector_tags"] == sample_enrichment["sub_tags"]
    assert payload["sub_tags"] == sample_enrichment["sub_tags"]
    assert payload["proposed_tags"] == sample_enrichment["proposed_tags"]
    assert payload["keywords"] == sample_enrichment["keywords"]
    assert payload["sector_mix"] == sample_enrichment["sector_mix"]
    assert payload["adjacent_sectors"] == sample_enrichment["adjacent_sectors"]
    assert payload["confidence"] == 0.92
    assert payload["model"] == "gemini-2.5-pro"
    assert payload["prompt_version"] == "v3"
    assert payload["raw_response"] == sample_enrichment


def test_build_enrichment_payload_defaults_for_missing(sample_company):
    minimal = {"primary_sector": "Insurance", "confidence": 0.3}
    payload = supabase_tool.build_enrichment_payload(
        sample_company, minimal, model="gemini-2.5-pro", prompt_version="v3"
    )
    assert payload["sector_tags"] == []
    assert payload["sub_tags"] == []
    assert payload["proposed_tags"] == []
    assert payload["keywords"] == []
    assert payload["sector_mix"] == []
    assert payload["adjacent_sectors"] == []
    assert payload["sources"] == []
    assert payload["tagline"] is None
    assert payload["revenue_estimate_usd"] is None
    assert payload["website"] is None
    assert payload["phone"] is None
    assert payload["email"] is None
    assert payload["address"] is None


def test_build_enrichment_payload_legacy_sector_tags_mirrors_sub_tags(sample_company):
    """v3 rows must populate legacy `sector_tags` (NOT NULL) with sub_tags content."""
    enrichment = {
        "primary_sector": "Retail & Consumer Goods",
        "confidence": 0.8,
        "sub_tags": ["luxury-retail", "jewelry-watches"],
    }
    payload = supabase_tool.build_enrichment_payload(
        sample_company, enrichment, model="gemini-2.5-pro", prompt_version="v3"
    )
    assert payload["sector_tags"] == ["luxury-retail", "jewelry-watches"]
    assert payload["sub_tags"] == ["luxury-retail", "jewelry-watches"]


def test_build_enrichment_payload_legacy_sector_tags_explicit_override(sample_company):
    """If caller passes explicit `sector_tags`, respect it (back-compat path)."""
    enrichment = {
        "primary_sector": "Retail & Consumer Goods",
        "confidence": 0.8,
        "sub_tags": ["luxury-retail"],
        "sector_tags": ["legacy-explicit"],
    }
    payload = supabase_tool.build_enrichment_payload(
        sample_company, enrichment, model="gemini-2.5-pro", prompt_version="v3"
    )
    assert payload["sector_tags"] == ["legacy-explicit"]
    assert payload["sub_tags"] == ["luxury-retail"]


def test_build_enrichment_payload_maps_contact_fields(sample_company):
    enrichment = {
        "primary_sector": "Real Estate Development",
        "confidence": 0.85,
        "website": "https://acme.ae",
        "phone": "+97141234567",
        "email": "info@acme.ae",
        "address": "P.O. Box 1234, Dubai",
    }
    payload = supabase_tool.build_enrichment_payload(
        sample_company, enrichment, model="gemini-2.5-pro", prompt_version="v2"
    )
    assert payload["website"] == "https://acme.ae"
    assert payload["phone"] == "+97141234567"
    assert payload["email"] == "info@acme.ae"
    assert payload["address"] == "P.O. Box 1234, Dubai"


def test_fetch_unenriched_dedupes_by_company_id(fake_client_factory):
    rows = [
        {"id": 1, "slug": "z-1", "name": "A", "country": "UAE", "sector": "Retail"},
        {"id": 2, "slug": "z-1", "name": "A", "country": "UAE", "sector": "Utility"},
        {"id": 3, "slug": "z-2", "name": "B", "country": "UAE", "sector": "Retail"},
    ]
    fake_client_factory(seed_rows=rows, enriched_rows=[])
    out = supabase_tool.fetch_unenriched_companies(limit=10, country="UAE")
    assert [r["company_id"] for r in out] == ["z-1", "z-2"]


def test_fetch_unenriched_skips_already_enriched(fake_client_factory):
    rows = [
        {"id": 1, "slug": "z-1", "name": "A", "country": "UAE", "sector": "Retail"},
        {"id": 2, "slug": "z-2", "name": "B", "country": "UAE", "sector": "Retail"},
        {"id": 3, "slug": "z-3", "name": "C", "country": "UAE", "sector": "Utility"},
    ]
    fake_client_factory(seed_rows=rows, enriched_rows=[{"company_id": "z-2"}])
    out = supabase_tool.fetch_unenriched_companies(limit=10)
    assert [r["company_id"] for r in out] == ["z-1", "z-3"]


def test_fetch_unenriched_respects_limit(fake_client_factory):
    rows = [
        {"id": i, "slug": f"z-{i}", "name": f"N{i}", "country": "UAE", "sector": "Retail"}
        for i in range(1, 20)
    ]
    fake_client_factory(seed_rows=rows, enriched_rows=[])
    out = supabase_tool.fetch_unenriched_companies(limit=5)
    assert len(out) == 5


def test_fetch_unenriched_filters_by_sector(fake_client_factory):
    rows = [
        {"id": 1, "slug": "z-1", "name": "A", "country": "UAE", "sector": "Retail"},
        {"id": 2, "slug": "z-2", "name": "B", "country": "UAE", "sector": "Utility"},
        {"id": 3, "slug": "z-3", "name": "C", "country": "UAE", "sector": "Retail"},
    ]
    fake_client_factory(seed_rows=rows, enriched_rows=[])
    out = supabase_tool.fetch_unenriched_companies(limit=10, sector="Retail")
    assert [r["company_id"] for r in out] == ["z-1", "z-3"]


def test_fetch_unenriched_top_company_only_ignored(fake_client_factory):
    """top_company_only is a no-op for seed list; all rows returned with a warning."""
    rows = [
        {"id": 1, "slug": "z-1", "name": "A", "country": "UAE", "sector": "Retail"},
        {"id": 2, "slug": "z-2", "name": "B", "country": "UAE", "sector": "Retail"},
        {"id": 3, "slug": "z-3", "name": "C", "country": "UAE", "sector": "Retail"},
    ]
    fake_client_factory(seed_rows=rows, enriched_rows=[])
    out = supabase_tool.fetch_unenriched_companies(limit=10, top_company_only=True)
    assert [r["company_id"] for r in out] == ["z-1", "z-2", "z-3"]


def test_fetch_unenriched_skips_poison_pill(fake_client_factory):
    rows = [
        {"id": 1, "slug": "z-1", "name": "A", "country": "UAE", "sector": "Retail"},
        {"id": 2, "slug": "z-2", "name": "B", "country": "UAE", "sector": "Retail"},
    ]
    # z-1 has 3 failures at current version -> should be skipped at threshold=3
    failure_rows = [
        {"company_id": "z-1", "prompt_version": "v3"},
        {"company_id": "z-1", "prompt_version": "v3"},
        {"company_id": "z-1", "prompt_version": "v3"},
    ]
    fake_client_factory(seed_rows=rows, enriched_rows=[], failure_rows=failure_rows)
    out = supabase_tool.fetch_unenriched_companies(limit=10, max_failures_per_row=3)
    assert [r["company_id"] for r in out] == ["z-2"]


def test_fetch_unenriched_keeps_row_below_failure_threshold(fake_client_factory):
    rows = [
        {"id": 1, "slug": "z-1", "name": "A", "country": "UAE", "sector": "Retail"},
    ]
    failure_rows = [
        {"company_id": "z-1", "prompt_version": "v3"},
        {"company_id": "z-1", "prompt_version": "v3"},
    ]
    fake_client_factory(seed_rows=rows, enriched_rows=[], failure_rows=failure_rows)
    out = supabase_tool.fetch_unenriched_companies(limit=10, max_failures_per_row=3)
    assert [r["company_id"] for r in out] == ["z-1"]


def test_write_enrichment_upserts(fake_client_factory):
    _, captured, _ = fake_client_factory(enriched_rows=[])
    payload = {
        "company_id": "z-1",
        "prompt_version": "v1",
        "primary_sector": "Insurance",
        "confidence": 0.5,
    }
    supabase_tool.write_enrichment(payload)
    assert len(captured) == 1
    assert captured[0]["payload"] == payload
    assert captured[0]["on_conflict"] == "company_id,prompt_version"


def test_write_failure_first_attempt(fake_client_factory, sample_company):
    _, _, failure_captured = fake_client_factory(
        enriched_rows=[], failure_rows=[]
    )
    err = ValueError("bad JSON from model")
    supabase_tool.write_failure(sample_company, err, prompt_version="v1")
    assert len(failure_captured) == 1
    row = failure_captured[0]
    assert row["company_id"] == sample_company["company_id"]
    assert row["company_pk"] == sample_company["id"]
    assert row["prompt_version"] == "v1"
    assert row["attempt"] == 1
    assert row["error_class"] == "ValueError"
    assert "bad JSON" in row["error_message"]
    assert row["raw_response"] is None


def test_write_failure_increments_attempt(fake_client_factory, sample_company):
    failure_rows = [
        {"company_id": sample_company["company_id"], "prompt_version": "v1"},
        {"company_id": sample_company["company_id"], "prompt_version": "v1"},
    ]
    _, _, failure_captured = fake_client_factory(
        enriched_rows=[], failure_rows=failure_rows
    )
    err = RuntimeError("boom")
    supabase_tool.write_failure(sample_company, err, prompt_version="v1")
    # 2 existing failures -> attempt=3
    assert failure_captured[-1]["attempt"] == 3
    assert failure_captured[-1]["error_class"] == "RuntimeError"


def test_write_failure_truncates_long_messages(fake_client_factory, sample_company):
    _, _, failure_captured = fake_client_factory(enriched_rows=[])
    big = "x" * 5000
    err = RuntimeError(big)
    supabase_tool.write_failure(sample_company, err, prompt_version="v1", raw_response=big)
    row = failure_captured[0]
    assert len(row["error_message"]) <= 2000
    assert len(row["raw_response"]) <= 5000


# --- Seed-list helpers ------------------------------------------------------


class SeedTable:
    """Routes select/upsert for company_seed_list."""

    def __init__(self, parent: "FakeSeedClient"):
        self._parent = parent
        self._filters: dict = {}

    def select(self, *a, **kw):
        new = SeedTable(self._parent)
        new._filters = dict(self._filters)
        return new

    def eq(self, column, value):
        new = SeedTable(self._parent)
        new._filters = dict(self._filters)
        new._filters[column] = value
        return new

    def _filtered_rows(self):
        rows = self._parent.seed_rows
        for col, val in self._filters.items():
            rows = [r for r in rows if r.get(col) == val]
        return rows

    def range(self, start, end):
        return _ExecResult(self._filtered_rows()[start : end + 1])

    def execute(self):
        return _result(self._filtered_rows())

    def upsert(self, payload, on_conflict=None):
        self._parent.upsert_capture.append({"payload": payload, "on_conflict": on_conflict})
        return _ExecResult(payload if isinstance(payload, list) else [payload])


class FakeSeedClient:
    def __init__(self, *, seed_rows, upsert_capture):
        self.seed_rows = seed_rows
        self.upsert_capture = upsert_capture

    def table(self, name):
        if name == "company_seed_list":
            return SeedTable(self)
        raise AssertionError(f"unexpected table {name}")


@pytest.fixture
def fake_seed_client_factory(monkeypatch):
    captured: list[dict] = []

    def _make(*, seed_rows=None):
        client = FakeSeedClient(seed_rows=seed_rows or [], upsert_capture=captured)
        monkeypatch.setattr(supabase_tool, "_client", lambda: client)
        return client, captured

    return _make


def test_slugify_handles_punctuation_and_case():
    assert supabase_tool.slugify("Emaar Properties PJSC") == "emaar-properties-pjsc"
    assert supabase_tool.slugify("  Al-Futtaim & Sons  ") == "al-futtaim-sons"
    assert supabase_tool.slugify("ADNOC (Abu Dhabi)") == "adnoc-abu-dhabi"
    assert supabase_tool.slugify("") == ""


def test_write_seed_companies_upserts_with_slug_and_validates(fake_seed_client_factory):
    _, captured = fake_seed_client_factory()
    n = supabase_tool.write_seed_companies(
        [
            {
                "name": "Emaar Properties",
                "country": "United Arab Emirates",
                "sector": "Real Estate Development",
                "source_url": "https://example.com/list",
                "source_title": "Top Real Estate UAE",
                "source_query": "top real estate companies UAE",
                "website": "https://www.emaar.com",
            }
        ]
    )
    assert n == 1
    assert len(captured) == 1
    assert captured[0]["on_conflict"] == "slug,country,sector,harvest_version"
    sent = captured[0]["payload"][0]
    assert sent["slug"] == "emaar-properties"
    assert sent["harvest_version"] == "v1"
    assert sent["raw_context"] == {}


def test_write_seed_companies_rejects_unknown_sector(fake_seed_client_factory):
    fake_seed_client_factory()
    with pytest.raises(ValueError, match="not in SECTORS"):
        supabase_tool.write_seed_companies(
            [
                {
                    "name": "X",
                    "country": "United Arab Emirates",
                    "sector": "Made Up Sector",
                    "source_url": "https://example.com",
                }
            ]
        )


def test_write_seed_companies_rejects_non_gcc_country(fake_seed_client_factory):
    fake_seed_client_factory()
    with pytest.raises(ValueError, match="not in GCC_COUNTRIES"):
        supabase_tool.write_seed_companies(
            [
                {
                    "name": "X",
                    "country": "Egypt",
                    "sector": "Retail & Consumer Goods",
                    "source_url": "https://example.com",
                }
            ]
        )


def test_write_seed_companies_drops_rows_missing_name_or_source(fake_seed_client_factory):
    _, captured = fake_seed_client_factory()
    n = supabase_tool.write_seed_companies(
        [
            {
                "name": "",
                "country": "United Arab Emirates",
                "sector": "Retail & Consumer Goods",
                "source_url": "https://example.com",
            },
            {
                "name": "Valid Co",
                "country": "United Arab Emirates",
                "sector": "Retail & Consumer Goods",
                "source_url": "",
            },
        ]
    )
    assert n == 0
    assert captured == []


def test_write_seed_companies_returns_zero_when_no_rows(fake_seed_client_factory):
    _, captured = fake_seed_client_factory()
    n = supabase_tool.write_seed_companies([])
    assert n == 0
    assert captured == []


def test_fetch_seed_slugs_filters_by_country_sector_version(fake_seed_client_factory):
    rows = [
        {
            "slug": "a",
            "country": "United Arab Emirates",
            "sector": "Retail & Consumer Goods",
            "harvest_version": "v1",
        },
        {
            "slug": "b",
            "country": "United Arab Emirates",
            "sector": "Retail & Consumer Goods",
            "harvest_version": "v1",
        },
        {
            "slug": "c",
            "country": "Qatar",
            "sector": "Retail & Consumer Goods",
            "harvest_version": "v1",
        },
        {
            "slug": "d",
            "country": "United Arab Emirates",
            "sector": "Retail & Consumer Goods",
            "harvest_version": "v2",
        },
    ]
    fake_seed_client_factory(seed_rows=rows)
    slugs = supabase_tool.fetch_seed_slugs(
        country="United Arab Emirates",
        sector="Retail & Consumer Goods",
    )
    assert slugs == {"a", "b"}


def test_fetch_seed_count_filters_by_country_sector_version(fake_seed_client_factory):
    rows = [
        {
            "id": 1,
            "country": "United Arab Emirates",
            "sector": "Banking & Financial Services",
            "harvest_version": "v1",
        },
        {
            "id": 2,
            "country": "United Arab Emirates",
            "sector": "Banking & Financial Services",
            "harvest_version": "v1",
        },
        {
            "id": 3,
            "country": "Saudi Arabia",
            "sector": "Banking & Financial Services",
            "harvest_version": "v1",
        },
    ]
    fake_seed_client_factory(seed_rows=rows)
    assert (
        supabase_tool.fetch_seed_count(
            country="United Arab Emirates",
            sector="Banking & Financial Services",
        )
        == 2
    )
