"""End-to-end test of batch_run with all I/O mocked."""

from __future__ import annotations

import pytest

from runner import batch_run


@pytest.fixture
def mock_pipeline(monkeypatch, sample_enrichment):
    rows = [
        {
            "id": 1,
            "company_id": "z-1",
            "name": "Emaar Properties",
            "country": "United Arab Emirates",
            "website": "https://emaar.com",
            "description": "Real estate.",
            "sector": "Real Estate",
            "top_company": True,
        },
        {
            "id": 2,
            "company_id": "z-2",
            "name": "Aldar",
            "country": "United Arab Emirates",
            "website": None,
            "description": None,
            "sector": "Real Estate",
            "top_company": False,
        },
    ]

    monkeypatch.setattr(batch_run, "fetch_unenriched_companies", lambda **kw: rows)
    monkeypatch.setattr(batch_run, "enrich_company_grounded", lambda **kw: dict(sample_enrichment))

    written: list[dict] = []
    failures: list[dict] = []
    monkeypatch.setattr(batch_run, "write_enrichment", lambda payload: written.append(payload))
    monkeypatch.setattr(
        batch_run,
        "write_failure",
        lambda row, exc, prompt_version, raw_response=None, max_failures_per_row=3: failures.append(
            {
                "row": row,
                "error_class": type(exc).__name__,
                "error_message": str(exc),
                "prompt_version": prompt_version,
            }
        ),
    )
    return written, failures, rows


def _default_kwargs(**overrides):
    base = {
        "limit": 10,
        "country": None,
        "sector": None,
        "top_company_only": False,
        "dry_run": False,
        "sleep_s": 0,
        "max_failures_per_row": 3,
        "max_failures_before_stop": None,
    }
    base.update(overrides)
    return base


def test_run_writes_one_payload_per_company(mock_pipeline):
    written, failures, rows = mock_pipeline
    rc = batch_run.run(**_default_kwargs())
    assert rc == 0
    assert len(written) == len(rows)
    assert {p["company_id"] for p in written} == {"z-1", "z-2"}
    assert all(p["prompt_version"] == "v3" for p in written)
    assert failures == []


def test_run_dry_run_does_not_write(mock_pipeline, capsys):
    written, failures, _ = mock_pipeline
    rc = batch_run.run(**_default_kwargs(dry_run=True))
    assert rc == 0
    assert written == []
    assert failures == []  # dry_run also skips failure writes
    out = capsys.readouterr().out
    assert "Emaar Properties" in out or "Aldar" in out


def test_run_failure_continues_and_records(monkeypatch, sample_enrichment):
    rows = [
        {"id": 1, "company_id": "z-1", "name": "Good", "country": "UAE"},
        {"id": 2, "company_id": "z-2", "name": "Bad", "country": "UAE"},
        {"id": 3, "company_id": "z-3", "name": "AlsoGood", "country": "UAE"},
    ]
    monkeypatch.setattr(batch_run, "fetch_unenriched_companies", lambda **kw: rows)

    def fake_enrich(name, **kw):
        if name == "Bad":
            raise RuntimeError("API failure")
        return dict(sample_enrichment)

    monkeypatch.setattr(batch_run, "enrich_company_grounded", fake_enrich)

    written: list[dict] = []
    failures: list[dict] = []
    monkeypatch.setattr(batch_run, "write_enrichment", lambda p: written.append(p))
    monkeypatch.setattr(
        batch_run,
        "write_failure",
        lambda row, exc, prompt_version, raw_response=None, max_failures_per_row=3: failures.append(
            {"company_id": row["company_id"], "error_class": type(exc).__name__}
        ),
    )

    rc = batch_run.run(**_default_kwargs())
    assert rc == 1  # non-zero because one failed
    assert {p["company_id"] for p in written} == {"z-1", "z-3"}
    assert len(failures) == 1
    assert failures[0]["company_id"] == "z-2"
    assert failures[0]["error_class"] == "RuntimeError"


def test_run_aborts_at_max_failures_before_stop(monkeypatch, sample_enrichment):
    rows = [
        {"id": i, "company_id": f"z-{i}", "name": f"Bad{i}", "country": "UAE"} for i in range(1, 6)
    ]
    monkeypatch.setattr(batch_run, "fetch_unenriched_companies", lambda **kw: rows)
    monkeypatch.setattr(
        batch_run,
        "enrich_company_grounded",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("nope")),
    )

    written: list[dict] = []
    failures: list[dict] = []
    monkeypatch.setattr(batch_run, "write_enrichment", lambda p: written.append(p))
    monkeypatch.setattr(
        batch_run,
        "write_failure",
        lambda row, exc, prompt_version, raw_response=None, max_failures_per_row=3: failures.append(row),
    )

    rc = batch_run.run(**_default_kwargs(max_failures_before_stop=2))
    assert rc == 1
    assert len(failures) == 2  # aborted after the 2nd failure
    assert written == []


def test_run_passes_contact_hints_to_enricher(monkeypatch, sample_enrichment):
    rows = [
        {
            "id": 1,
            "company_id": "z-1",
            "name": "ACME",
            "country": "UAE",
            "website": "https://acme.ae",
            "description": "industrial",
            "sector": "Industrial",
            "top_company": True,
            "phone": "+97141234567",
            "email": "info@acme.ae",
            "address": "P.O. Box 1234, Dubai",
        }
    ]
    monkeypatch.setattr(batch_run, "fetch_unenriched_companies", lambda **kw: rows)

    captured_kwargs: dict = {}

    def fake_enrich(**kw):
        captured_kwargs.update(kw)
        return dict(sample_enrichment)

    monkeypatch.setattr(batch_run, "enrich_company_grounded", fake_enrich)
    monkeypatch.setattr(batch_run, "write_enrichment", lambda p: None)

    rc = batch_run.run(**_default_kwargs())
    assert rc == 0
    assert captured_kwargs["phone"] == "+97141234567"
    assert captured_kwargs["email"] == "info@acme.ae"
    assert captured_kwargs["address"] == "P.O. Box 1234, Dubai"
    assert captured_kwargs["website"] == "https://acme.ae"


def test_run_passes_filters_through(monkeypatch, sample_enrichment):
    captured_kwargs: dict = {}

    def fake_fetch(**kw):
        captured_kwargs.update(kw)
        return []

    monkeypatch.setattr(batch_run, "fetch_unenriched_companies", fake_fetch)

    rc = batch_run.run(
        **_default_kwargs(
            limit=25,
            country="United Arab Emirates",
            sector="Retail",
            top_company_only=True,
            max_failures_per_row=5,
        )
    )
    assert rc == 0
    assert captured_kwargs["limit"] == 25
    assert captured_kwargs["country"] == "United Arab Emirates"
    assert captured_kwargs["sector"] == "Retail"
    assert captured_kwargs["top_company_only"] is True
    assert captured_kwargs["max_failures_per_row"] == 5
