import pytest
from pydantic import ValidationError

from agent.prompts import (
    PROMPT_VERSION,
    EnrichmentResult,
    build_user_prompt,
    system_instruction,
)
from agent.taxonomy import SECTORS


def test_prompt_version_set():
    assert PROMPT_VERSION == "v1"


def test_system_instruction_lists_all_sectors():
    text = system_instruction()
    for s in SECTORS:
        assert s in text, f"sector missing from system prompt: {s}"


def test_system_instruction_mentions_grounding_and_json():
    text = system_instruction().lower()
    assert "google search" in text
    assert "json" in text
    assert "confidence" in text


def test_enrichment_result_minimal_valid(sample_enrichment):
    obj = EnrichmentResult.model_validate(sample_enrichment)
    assert obj.primary_sector == "Real Estate Development"
    assert obj.confidence == 0.92


def test_enrichment_result_rejects_bad_confidence(sample_enrichment):
    sample_enrichment["confidence"] = 1.5
    with pytest.raises(ValidationError):
        EnrichmentResult.model_validate(sample_enrichment)


def test_enrichment_result_allows_null_optional_fields():
    obj = EnrichmentResult.model_validate(
        {"primary_sector": "Banking & Financial Services", "confidence": 0.4}
    )
    assert obj.tagline is None
    assert obj.sector_tags == []
    assert obj.sources == []


def test_build_user_prompt_includes_fields():
    prompt = build_user_prompt(
        name="ACME",
        country="UAE",
        website="https://acme.ae",
        description="industrial supplier",
        coarse_sector="Industrial",
    )
    assert "ACME" in prompt
    assert "UAE" in prompt
    assert "acme.ae" in prompt
    assert "Industrial" in prompt
    assert "industrial supplier" in prompt


def test_build_user_prompt_omits_missing():
    prompt = build_user_prompt(
        name="Tiny Co", country="UAE", website=None, description=None, coarse_sector=None
    )
    assert "Tiny Co" in prompt
    assert "Website" not in prompt
    assert "Existing description" not in prompt
