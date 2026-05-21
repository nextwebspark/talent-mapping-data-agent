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

    def execute(self):
        return _result(self._parent.enriched_rows)

    def upsert(self, payload, on_conflict=None):
        self._parent.upsert_capture.append(
            {"payload": payload, "on_conflict": on_conflict}
        )
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

    def execute(self):
        rows = self._parent.failure_rows
        for col, val in self._filters.items():
            rows = [r for r in rows if r.get(col) == val]
        return _result(rows)

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
        companies_rows,
        enriched_rows,
        failure_rows: list | None = None,
        upsert_capture: list,
        failure_insert_capture: list,
    ):
        self.companies_rows = companies_rows
        self.enriched_rows = enriched_rows
        self.failure_rows = failure_rows or []
        self.upsert_capture = upsert_capture
        self.failure_insert_capture = failure_insert_capture

    def table(self, name):
        if name == "companies":
            return CompaniesQuery(self.companies_rows)
        if name == "company_enrichment":
            return EnrichmentTable(self)
        if name == "company_enrichment_failures":
            return FailuresTable(self)
        raise AssertionError(f"unexpected table {name}")


@pytest.fixture
def fake_client_factory(monkeypatch):
    captured: list[dict] = []
    failure_captured: list[dict] = []

    def _make(*, companies_rows, enriched_rows, failure_rows=None):
        client = FakeClient(
            companies_rows=companies_rows,
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
        sample_company, sample_enrichment, model="gemini-2.5-pro", prompt_version="v1"
    )
    assert payload["company_pk"] == sample_company["id"]
    assert payload["company_id"] == sample_company["company_id"]
    assert payload["primary_sector"] == "Real Estate Development"
    assert payload["sector_tags"] == sample_enrichment["sector_tags"]
    assert payload["adjacent_sectors"] == sample_enrichment["adjacent_sectors"]
    assert payload["confidence"] == 0.92
    assert payload["model"] == "gemini-2.5-pro"
    assert payload["prompt_version"] == "v1"
    assert payload["raw_response"] == sample_enrichment


def test_build_enrichment_payload_defaults_for_missing(sample_company):
    minimal = {"primary_sector": "Insurance", "confidence": 0.3}
    payload = supabase_tool.build_enrichment_payload(
        sample_company, minimal, model="gemini-2.5-pro", prompt_version="v1"
    )
    assert payload["sector_tags"] == []
    assert payload["adjacent_sectors"] == []
    assert payload["sources"] == []
    assert payload["tagline"] is None
    assert payload["revenue_estimate_usd"] is None


def test_fetch_unenriched_dedupes_by_company_id(fake_client_factory):
    rows = [
        {"id": 1, "company_id": "z-1", "name": "A", "country": "UAE", "sector": "Retail", "top_company": True},
        {"id": 2, "company_id": "z-1", "name": "A", "country": "UAE", "sector": "Utility", "top_company": True},
        {"id": 3, "company_id": "z-2", "name": "B", "country": "UAE", "sector": "Retail", "top_company": False},
    ]
    fake_client_factory(companies_rows=rows, enriched_rows=[])
    out = supabase_tool.fetch_unenriched_companies(limit=10, country="UAE")
    assert [r["company_id"] for r in out] == ["z-1", "z-2"]


def test_fetch_unenriched_skips_already_enriched(fake_client_factory):
    rows = [
        {"id": 1, "company_id": "z-1", "name": "A", "country": "UAE", "sector": "Retail", "top_company": True},
        {"id": 2, "company_id": "z-2", "name": "B", "country": "UAE", "sector": "Retail", "top_company": False},
        {"id": 3, "company_id": "z-3", "name": "C", "country": "UAE", "sector": "Utility", "top_company": False},
    ]
    fake_client_factory(companies_rows=rows, enriched_rows=[{"company_id": "z-2"}])
    out = supabase_tool.fetch_unenriched_companies(limit=10)
    assert [r["company_id"] for r in out] == ["z-1", "z-3"]


def test_fetch_unenriched_respects_limit(fake_client_factory):
    rows = [
        {
            "id": i,
            "company_id": f"z-{i}",
            "name": f"N{i}",
            "country": "UAE",
            "sector": "Retail",
            "top_company": False,
        }
        for i in range(1, 20)
    ]
    fake_client_factory(companies_rows=rows, enriched_rows=[])
    out = supabase_tool.fetch_unenriched_companies(limit=5)
    assert len(out) == 5


def test_fetch_unenriched_filters_by_sector(fake_client_factory):
    rows = [
        {"id": 1, "company_id": "z-1", "name": "A", "country": "UAE", "sector": "Retail", "top_company": False},
        {"id": 2, "company_id": "z-2", "name": "B", "country": "UAE", "sector": "Utility", "top_company": False},
        {"id": 3, "company_id": "z-3", "name": "C", "country": "UAE", "sector": "Retail", "top_company": False},
    ]
    fake_client_factory(companies_rows=rows, enriched_rows=[])
    out = supabase_tool.fetch_unenriched_companies(limit=10, sector="Retail")
    assert [r["company_id"] for r in out] == ["z-1", "z-3"]


def test_fetch_unenriched_top_company_only(fake_client_factory):
    rows = [
        {"id": 1, "company_id": "z-1", "name": "A", "country": "UAE", "sector": "Retail", "top_company": True},
        {"id": 2, "company_id": "z-2", "name": "B", "country": "UAE", "sector": "Retail", "top_company": False},
        {"id": 3, "company_id": "z-3", "name": "C", "country": "UAE", "sector": "Retail", "top_company": True},
    ]
    fake_client_factory(companies_rows=rows, enriched_rows=[])
    out = supabase_tool.fetch_unenriched_companies(limit=10, top_company_only=True)
    assert [r["company_id"] for r in out] == ["z-1", "z-3"]


def test_fetch_unenriched_skips_poison_pill(fake_client_factory):
    rows = [
        {"id": 1, "company_id": "z-1", "name": "A", "country": "UAE", "sector": "Retail", "top_company": False},
        {"id": 2, "company_id": "z-2", "name": "B", "country": "UAE", "sector": "Retail", "top_company": False},
    ]
    # z-1 has 3 failures at v1 -> should be skipped at threshold=3
    failure_rows = [
        {"company_id": "z-1", "prompt_version": "v1"},
        {"company_id": "z-1", "prompt_version": "v1"},
        {"company_id": "z-1", "prompt_version": "v1"},
    ]
    fake_client_factory(
        companies_rows=rows, enriched_rows=[], failure_rows=failure_rows
    )
    out = supabase_tool.fetch_unenriched_companies(
        limit=10, max_failures_per_row=3
    )
    assert [r["company_id"] for r in out] == ["z-2"]


def test_fetch_unenriched_keeps_row_below_failure_threshold(fake_client_factory):
    rows = [
        {"id": 1, "company_id": "z-1", "name": "A", "country": "UAE", "sector": "Retail", "top_company": False},
    ]
    failure_rows = [
        {"company_id": "z-1", "prompt_version": "v1"},
        {"company_id": "z-1", "prompt_version": "v1"},
    ]
    fake_client_factory(
        companies_rows=rows, enriched_rows=[], failure_rows=failure_rows
    )
    out = supabase_tool.fetch_unenriched_companies(
        limit=10, max_failures_per_row=3
    )
    assert [r["company_id"] for r in out] == ["z-1"]


def test_write_enrichment_upserts(fake_client_factory):
    _, captured, _ = fake_client_factory(companies_rows=[], enriched_rows=[])
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
        companies_rows=[], enriched_rows=[], failure_rows=[]
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
        companies_rows=[], enriched_rows=[], failure_rows=failure_rows
    )
    err = RuntimeError("boom")
    supabase_tool.write_failure(sample_company, err, prompt_version="v1")
    # 2 existing failures -> attempt=3
    assert failure_captured[-1]["attempt"] == 3
    assert failure_captured[-1]["error_class"] == "RuntimeError"


def test_write_failure_truncates_long_messages(fake_client_factory, sample_company):
    _, _, failure_captured = fake_client_factory(
        companies_rows=[], enriched_rows=[]
    )
    big = "x" * 5000
    err = RuntimeError(big)
    supabase_tool.write_failure(
        sample_company, err, prompt_version="v1", raw_response=big
    )
    row = failure_captured[0]
    assert len(row["error_message"]) <= 2000
    assert len(row["raw_response"]) <= 5000
