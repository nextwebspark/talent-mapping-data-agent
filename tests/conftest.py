"""Shared fixtures. Stubs env vars so config.load_settings() works without .env."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-api-key")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "false")
    monkeypatch.setenv("ENRICHMENT_MODEL", "gemini-2.5-pro")
    monkeypatch.setenv("PROMPT_VERSION", "v3")
    # Reset cached supabase client between tests
    try:
        from tools.supabase_tool import _client

        _client.cache_clear()
    except Exception:
        pass


@pytest.fixture
def sample_company() -> dict:
    """Seed-list shaped row with synthetic company_id (= slug) as returned by
    fetch_unenriched_companies."""
    return {
        "id": 42,
        "slug": "emaar-properties",
        "company_id": "emaar-properties",  # synthetic field added by fetch
        "name": "Emaar Properties",
        "country": "United Arab Emirates",
        "website": "https://www.emaar.com",
        "description": "Real estate developer.",
        "sector": "Real Estate",
    }


@pytest.fixture
def sample_enrichment() -> dict:
    return {
        "primary_sector": "Real Estate Development",
        "sector_mix": [
            {"sector": "Real Estate Development", "weight": "dominant"},
            {"sector": "Hospitality, Travel & Tourism", "weight": "minor"},
        ],
        "sub_tags": ["luxury-residential", "master-planned-communities"],
        "proposed_tags": [],
        "keywords": ["pan-gcc", "publicly-listed", "branded-developer"],
        "tagline": "UAE-based master-plan developer.",
        "business_description": "Develops large-scale residential and mixed-use communities.",
        "employee_band": "5k-10k",
        "employee_count_estimate": 6000,
        "revenue_band": "$1-10B",
        "revenue_estimate_usd": 7_500_000_000,
        "is_listed": True,
        "hq_city": "Dubai",
        "confidence": 0.92,
        "sources": [
            {"url": "https://www.emaar.com/about", "title": "About", "snippet": None},
        ],
    }
