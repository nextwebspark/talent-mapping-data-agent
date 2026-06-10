"""Tests for supabase tool. Mocks supabase client; verifies dedup logic and
payload mapping without touching the network."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools import supabase_tool


def _result(data):
    return SimpleNamespace(data=data)


class SeedQueryBuilder:
    """Mimics PostgREST query builder for company_seed_list.

    Supports .eq(), .or_(), .order(), .range(), .update(), .execute().
    The .or_() filter for enrichment_status handles the NULL/'pending' logic
    used by fetch_unenriched_companies.
    """

    def __init__(self, parent: "FakeClient", filters: dict | None = None, or_filter: str | None = None):
        self._parent = parent
        self._filters = filters or {}
        self._or_filter = or_filter  # raw PostgREST or_ string, parsed in _apply_filters

    def select(self, *args, **kwargs):
        return SeedQueryBuilder(self._parent, dict(self._filters), self._or_filter)

    def eq(self, column, value):
        new_filters = dict(self._filters)
        new_filters[column] = value
        return SeedQueryBuilder(self._parent, new_filters, self._or_filter)

    def or_(self, condition: str):
        return SeedQueryBuilder(self._parent, dict(self._filters), condition)

    def order(self, *args, **kwargs):
        return SeedQueryBuilder(self._parent, dict(self._filters), self._or_filter)

    def _apply_filters(self):
        filtered = self._parent.seed_rows
        for col, val in self._filters.items():
            filtered = [r for r in filtered if r.get(col) == val]
        # Apply or_ filter for enrichment_status IS NULL OR = 'pending'
        if self._or_filter and "enrichment_status" in self._or_filter:
            filtered = [
                r for r in filtered
                if r.get("enrichment_status") is None or r.get("enrichment_status") == "pending"
            ]
        return filtered

    def range(self, start, end):
        return _ExecResult(self._apply_filters()[start: end + 1])

    def execute(self):
        return _result(self._apply_filters())

    def update(self, data: dict):
        return _SeedUpdateBuilder(self._parent, data, dict(self._filters))

    def upsert(self, payload, on_conflict=None):
        self._parent.upsert_capture.append({"payload": payload, "on_conflict": on_conflict})
        return _ExecResult(payload if isinstance(payload, list) else [payload])


class _SeedUpdateBuilder:
    """Captures UPDATE calls on company_seed_list."""

    def __init__(self, parent: "FakeClient", data: dict, filters: dict):
        self._parent = parent
        self._data = data
        self._filters = filters

    def eq(self, column, value):
        new_filters = dict(self._filters)
        new_filters[column] = value
        return _SeedUpdateBuilder(self._parent, self._data, new_filters)

    def execute(self):
        self._parent.seed_status_updates.append({"data": self._data, "filters": self._filters})
        # Apply update to in-memory rows so subsequent selects see the change
        for row in self._parent.seed_rows:
            if all(row.get(k) == v for k, v in self._filters.items()):
                row.update(self._data)
        return _result([])


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
        return _ExecResult(self._parent.enriched_rows[start: end + 1])

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
        return _ExecResult(self._filtered_rows()[start: end + 1])

    def execute(self):
        return _result(self._filtered_rows())

    def insert(self, payload):
        self._parent.failure_insert_capture.append(payload)
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
        self.seed_status_updates: list[dict] = []

    def table(self, name):
        if name == "company_seed_list":
            return SeedQueryBuilder(self)
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
        sample_company, sample_enrichment, model="gemini-2.5-flash", prompt_version="v3"
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
    assert payload["confidence"] == 0.92
    assert payload["model"] == "gemini-2.5-flash"
    assert payload["prompt_version"] == "v3"
    assert payload["raw_response"] == sample_enrichment


def test_build_enrichment_payload_defaults_for_missing(sample_company):
    minimal = {"primary_sector": "Insurance", "confidence": 0.3}
    payload = supabase_tool.build_enrichment_payload(
        sample_company, minimal, model="gemini-2.5-flash", prompt_version="v3"
    )
    assert payload["sector_tags"] == []
    assert payload["sub_tags"] == []
    assert payload["proposed_tags"] == []
    assert payload["keywords"] == []
    assert payload["sector_mix"] == []
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
        "primary_sector": "Retail & E-Commerce",
        "confidence": 0.8,
        "sub_tags": ["luxury-retail", "jewelry-watches"],
    }
    payload = supabase_tool.build_enrichment_payload(
        sample_company, enrichment, model="gemini-2.5-flash", prompt_version="v3"
    )
    assert payload["sector_tags"] == ["luxury-retail", "jewelry-watches"]
    assert payload["sub_tags"] == ["luxury-retail", "jewelry-watches"]


def test_build_enrichment_payload_legacy_sector_tags_explicit_override(sample_company):
    """If caller passes explicit `sector_tags`, respect it (back-compat path)."""
    enrichment = {
        "primary_sector": "Retail & E-Commerce",
        "confidence": 0.8,
        "sub_tags": ["luxury-retail"],
        "sector_tags": ["legacy-explicit"],
    }
    payload = supabase_tool.build_enrichment_payload(
        sample_company, enrichment, model="gemini-2.5-flash", prompt_version="v3"
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
        sample_company, enrichment, model="gemini-2.5-flash", prompt_version="v2"
    )
    assert payload["website"] == "https://acme.ae"
    assert payload["phone"] == "+97141234567"
    assert payload["email"] == "info@acme.ae"
    assert payload["address"] == "P.O. Box 1234, Dubai"


def test_fetch_unenriched_dedupes_by_slug(fake_client_factory):
    """Same slug under two sectors is returned once."""
    rows = [
        {"id": 1, "slug": "z-1", "name": "A", "country": "UAE", "sector": "Retail", "enrichment_status": None},
        {"id": 2, "slug": "z-1", "name": "A", "country": "UAE", "sector": "Utility", "enrichment_status": None},
        {"id": 3, "slug": "z-2", "name": "B", "country": "UAE", "sector": "Retail", "enrichment_status": None},
    ]
    fake_client_factory(seed_rows=rows, enriched_rows=[])
    out = supabase_tool.fetch_unenriched_companies(limit=10, country="UAE")
    assert [r["company_id"] for r in out] == ["z-1", "z-2"]


def test_fetch_unenriched_skips_enriched_status(fake_client_factory):
    """Rows with enrichment_status='enriched' are excluded by DB query."""
    rows = [
        {"id": 1, "slug": "z-1", "name": "A", "country": "UAE", "sector": "Retail", "enrichment_status": None},
        {"id": 2, "slug": "z-2", "name": "B", "country": "UAE", "sector": "Retail", "enrichment_status": "enriched"},
        {"id": 3, "slug": "z-3", "name": "C", "country": "UAE", "sector": "Utility", "enrichment_status": None},
    ]
    fake_client_factory(seed_rows=rows, enriched_rows=[])
    out = supabase_tool.fetch_unenriched_companies(limit=10)
    assert [r["company_id"] for r in out] == ["z-1", "z-3"]


def test_fetch_unenriched_includes_pending_status(fake_client_factory):
    """Rows with enrichment_status='pending' are included."""
    rows = [
        {"id": 1, "slug": "z-1", "name": "A", "country": "UAE", "sector": "Retail", "enrichment_status": "pending"},
        {"id": 2, "slug": "z-2", "name": "B", "country": "UAE", "sector": "Retail", "enrichment_status": "enriched"},
    ]
    fake_client_factory(seed_rows=rows, enriched_rows=[])
    out = supabase_tool.fetch_unenriched_companies(limit=10)
    assert [r["company_id"] for r in out] == ["z-1"]


def test_fetch_unenriched_skips_failed_status(fake_client_factory):
    """Rows with enrichment_status='failed' (poison pill) are excluded."""
    rows = [
        {"id": 1, "slug": "z-1", "name": "A", "country": "UAE", "sector": "Retail", "enrichment_status": "failed"},
        {"id": 2, "slug": "z-2", "name": "B", "country": "UAE", "sector": "Retail", "enrichment_status": None},
    ]
    fake_client_factory(seed_rows=rows, enriched_rows=[])
    out = supabase_tool.fetch_unenriched_companies(limit=10)
    assert [r["company_id"] for r in out] == ["z-2"]


def test_fetch_failed_returns_only_failed_status(fake_client_factory):
    """fetch_failed_companies returns only enrichment_status='failed' rows."""
    rows = [
        {"id": 1, "slug": "z-1", "name": "A", "country": "UAE", "sector": "Retail", "enrichment_status": "failed"},
        {"id": 2, "slug": "z-2", "name": "B", "country": "UAE", "sector": "Retail", "enrichment_status": None},
        {"id": 3, "slug": "z-3", "name": "C", "country": "UAE", "sector": "Utility", "enrichment_status": "enriched"},
        {"id": 4, "slug": "z-4", "name": "D", "country": "UAE", "sector": "Retail", "enrichment_status": "failed"},
    ]
    fake_client_factory(seed_rows=rows, enriched_rows=[])
    out = supabase_tool.fetch_failed_companies(limit=10)
    assert [r["company_id"] for r in out] == ["z-1", "z-4"]


def test_fetch_failed_dedupes_by_slug(fake_client_factory):
    """Same failed slug under two sectors is returned once."""
    rows = [
        {"id": 1, "slug": "z-1", "name": "A", "country": "UAE", "sector": "Retail", "enrichment_status": "failed"},
        {"id": 2, "slug": "z-1", "name": "A", "country": "UAE", "sector": "Utility", "enrichment_status": "failed"},
        {"id": 3, "slug": "z-2", "name": "B", "country": "UAE", "sector": "Retail", "enrichment_status": "failed"},
    ]
    fake_client_factory(seed_rows=rows, enriched_rows=[])
    out = supabase_tool.fetch_failed_companies(limit=10)
    assert [r["company_id"] for r in out] == ["z-1", "z-2"]


def test_fetch_failed_filters_by_country(fake_client_factory):
    rows = [
        {"id": 1, "slug": "z-1", "name": "A", "country": "UAE", "sector": "Retail", "enrichment_status": "failed"},
        {"id": 2, "slug": "z-2", "name": "B", "country": "Qatar", "sector": "Retail", "enrichment_status": "failed"},
    ]
    fake_client_factory(seed_rows=rows, enriched_rows=[])
    out = supabase_tool.fetch_failed_companies(limit=10, country="UAE")
    assert [r["company_id"] for r in out] == ["z-1"]


def test_fetch_unenriched_respects_limit(fake_client_factory):
    rows = [
        {"id": i, "slug": f"z-{i}", "name": f"N{i}", "country": "UAE", "sector": "Retail", "enrichment_status": None}
        for i in range(1, 20)
    ]
    fake_client_factory(seed_rows=rows, enriched_rows=[])
    out = supabase_tool.fetch_unenriched_companies(limit=5)
    assert len(out) == 5


def test_fetch_unenriched_filters_by_sector(fake_client_factory):
    rows = [
        {"id": 1, "slug": "z-1", "name": "A", "country": "UAE", "sector": "Retail", "enrichment_status": None},
        {"id": 2, "slug": "z-2", "name": "B", "country": "UAE", "sector": "Utility", "enrichment_status": None},
        {"id": 3, "slug": "z-3", "name": "C", "country": "UAE", "sector": "Retail", "enrichment_status": None},
    ]
    fake_client_factory(seed_rows=rows, enriched_rows=[])
    out = supabase_tool.fetch_unenriched_companies(limit=10, sector="Retail")
    assert [r["company_id"] for r in out] == ["z-1", "z-3"]


def test_fetch_unenriched_top_company_only_ignored(fake_client_factory):
    """top_company_only is a no-op for seed list; all rows returned with a warning."""
    rows = [
        {"id": 1, "slug": "z-1", "name": "A", "country": "UAE", "sector": "Retail", "enrichment_status": None},
        {"id": 2, "slug": "z-2", "name": "B", "country": "UAE", "sector": "Retail", "enrichment_status": None},
        {"id": 3, "slug": "z-3", "name": "C", "country": "UAE", "sector": "Retail", "enrichment_status": None},
    ]
    fake_client_factory(seed_rows=rows, enriched_rows=[])
    out = supabase_tool.fetch_unenriched_companies(limit=10, top_company_only=True)
    assert [r["company_id"] for r in out] == ["z-1", "z-2", "z-3"]


def test_write_enrichment_upserts_and_stamps_status(fake_client_factory):
    client, captured, _ = fake_client_factory(
        seed_rows=[{"id": 1, "slug": "z-1", "name": "A", "enrichment_status": None}],
        enriched_rows=[],
    )
    payload = {
        "company_id": "z-1",
        "slug": "z-1",
        "prompt_version": "v1",
        "primary_sector": "Insurance",
        "confidence": 0.5,
    }
    supabase_tool.write_enrichment(payload)
    assert len(captured) == 1
    assert captured[0]["payload"] == payload
    assert captured[0]["on_conflict"] == "company_id,prompt_version"
    # seed row should now be stamped enriched
    assert any(
        u["data"] == {"enrichment_status": "enriched"} and u["filters"].get("slug") == "z-1"
        for u in client.seed_status_updates
    )


def test_write_failure_first_attempt(fake_client_factory, sample_company):
    _, _, failure_captured = fake_client_factory(
        seed_rows=[{"id": sample_company["id"], "slug": sample_company["slug"], "enrichment_status": None}],
        enriched_rows=[],
        failure_rows=[],
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
    client, _, failure_captured = fake_client_factory(
        seed_rows=[{"id": sample_company["id"], "slug": sample_company["slug"], "enrichment_status": None}],
        enriched_rows=[],
        failure_rows=failure_rows,
    )
    err = RuntimeError("boom")
    supabase_tool.write_failure(sample_company, err, prompt_version="v1")
    # 2 existing failures -> attempt=3
    assert failure_captured[-1]["attempt"] == 3
    assert failure_captured[-1]["error_class"] == "RuntimeError"


def test_write_failure_stamps_failed_at_threshold(fake_client_factory, sample_company):
    """When attempt count reaches max_failures_per_row, seed row is stamped 'failed'."""
    failure_rows = [
        {"company_id": sample_company["company_id"], "prompt_version": "v1"},
        {"company_id": sample_company["company_id"], "prompt_version": "v1"},
    ]
    client, _, _ = fake_client_factory(
        seed_rows=[{"id": sample_company["id"], "slug": sample_company["slug"], "enrichment_status": None}],
        enriched_rows=[],
        failure_rows=failure_rows,
    )
    err = RuntimeError("persistent error")
    supabase_tool.write_failure(sample_company, err, prompt_version="v1", max_failures_per_row=3)
    # attempt=3 == max_failures_per_row → should stamp 'failed'
    assert any(
        u["data"] == {"enrichment_status": "failed"} and u["filters"].get("slug") == sample_company["slug"]
        for u in client.seed_status_updates
    )


def test_write_failure_does_not_stamp_below_threshold(fake_client_factory, sample_company):
    """Below the threshold, seed status is not updated."""
    client, _, _ = fake_client_factory(
        seed_rows=[{"id": sample_company["id"], "slug": sample_company["slug"], "enrichment_status": None}],
        enriched_rows=[],
        failure_rows=[],
    )
    err = RuntimeError("first error")
    supabase_tool.write_failure(sample_company, err, prompt_version="v1", max_failures_per_row=3)
    # attempt=1 < 3 → no status update
    assert client.seed_status_updates == []


def test_write_failure_truncates_long_messages(fake_client_factory, sample_company):
    _, _, failure_captured = fake_client_factory(
        seed_rows=[{"id": sample_company["id"], "slug": sample_company["slug"], "enrichment_status": None}],
        enriched_rows=[],
    )
    big = "x" * 5000
    err = RuntimeError(big)
    supabase_tool.write_failure(sample_company, err, prompt_version="v1", raw_response=big)
    row = failure_captured[0]
    assert len(row["error_message"]) <= 2000
    assert len(row["raw_response"]) <= 5000


# --- Seed-list helpers ------------------------------------------------------


class SeedTable:
    """Routes select/upsert for company_seed_list (used by FakeSeedClient)."""

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
        return _ExecResult(self._filtered_rows()[start: end + 1])

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
                    "sector": "Retail & E-Commerce",
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
                "sector": "Retail & E-Commerce",
                "source_url": "https://example.com",
            },
            {
                "name": "Valid Co",
                "country": "United Arab Emirates",
                "sector": "Retail & E-Commerce",
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
            "sector": "Retail & E-Commerce",
            "harvest_version": "v1",
        },
        {
            "slug": "b",
            "country": "United Arab Emirates",
            "sector": "Retail & E-Commerce",
            "harvest_version": "v1",
        },
        {
            "slug": "c",
            "country": "Qatar",
            "sector": "Retail & E-Commerce",
            "harvest_version": "v1",
        },
        {
            "slug": "d",
            "country": "United Arab Emirates",
            "sector": "Retail & E-Commerce",
            "harvest_version": "v2",
        },
    ]
    fake_seed_client_factory(seed_rows=rows)
    slugs = supabase_tool.fetch_seed_slugs(
        country="United Arab Emirates",
        sector="Retail & E-Commerce",
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
