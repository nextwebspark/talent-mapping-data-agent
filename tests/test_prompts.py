import pytest
from pydantic import ValidationError

from agent.prompts import (
    PROMPT_VERSION,
    EnrichmentResult,
    SectorMixEntry,
    build_user_prompt,
    system_instruction,
)
from agent.subtags import SUB_TAGS
from agent.taxonomy import SECTORS


def test_prompt_version_set():
    assert PROMPT_VERSION == "v3"


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
    assert obj.sub_tags == []
    assert obj.proposed_tags == []
    assert obj.keywords == []
    assert obj.sector_mix == []
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
    assert "Existing phone" not in prompt
    assert "Existing email" not in prompt
    assert "Existing address" not in prompt


def test_build_user_prompt_includes_contact_hints():
    prompt = build_user_prompt(
        name="ACME",
        country="UAE",
        website=None,
        description=None,
        coarse_sector=None,
        phone="+97141234567",
        email="info@acme.ae",
        address="P.O. Box 1234, Dubai",
    )
    assert "+97141234567" in prompt
    assert "info@acme.ae" in prompt
    assert "P.O. Box 1234, Dubai" in prompt


def test_system_instruction_mentions_contact_extraction():
    text = system_instruction().lower()
    assert "website" in text
    assert "phone" in text
    assert "email" in text
    assert "address" in text


def test_system_instruction_mentions_v3_fields():
    text = system_instruction()
    assert "sub_tags" in text
    assert "sector_mix" in text
    assert "proposed_tags" in text
    assert "keywords" in text
    assert "dominant" in text and "significant" in text and "minor" in text
    # at least one canonical sub_tag from the controlled list appears in prompt
    assert "retail-banking" in text
    assert "fintech-lending" in text


def test_sector_mix_entry_validates_weight():
    SectorMixEntry.model_validate({"sector": "Banking & Financial Services", "weight": "dominant"})
    with pytest.raises(ValidationError):
        SectorMixEntry.model_validate({"sector": "Banking & Financial Services", "weight": "huge"})


def test_enrichment_result_separates_invalid_sub_tags():
    sample = next(iter(SUB_TAGS))  # any valid canonical tag
    obj = EnrichmentResult.model_validate(
        {
            "primary_sector": "Banking & Financial Services",
            "confidence": 0.7,
            "sub_tags": [sample, "totally-made-up-niche", "another-bogus-tag"],
            "proposed_tags": [],
        }
    )
    assert obj.sub_tags == [sample]
    assert "totally-made-up-niche" in obj.proposed_tags
    assert "another-bogus-tag" in obj.proposed_tags


def test_enrichment_result_preserves_existing_proposed_tags():
    sample = next(iter(SUB_TAGS))
    obj = EnrichmentResult.model_validate(
        {
            "primary_sector": "Banking & Financial Services",
            "confidence": 0.7,
            "sub_tags": [sample, "new-niche"],
            "proposed_tags": ["already-flagged"],
        }
    )
    assert obj.sub_tags == [sample]
    assert obj.proposed_tags == ["already-flagged", "new-niche"]


def test_enrichment_result_rejects_invalid_sector_mix_weight():
    with pytest.raises(ValidationError):
        EnrichmentResult.model_validate(
            {
                "primary_sector": "Banking & Financial Services",
                "confidence": 0.7,
                "sector_mix": [
                    {"sector": "Banking & Financial Services", "weight": "huge"},
                ],
            }
        )


def test_enrichment_result_strips_blank_and_dup_sub_tags():
    sample = next(iter(SUB_TAGS))
    obj = EnrichmentResult.model_validate(
        {
            "primary_sector": "Banking & Financial Services",
            "confidence": 0.7,
            "sub_tags": [sample, "  ", sample, ""],
        }
    )
    # blanks dropped, duplicates collapsed
    assert obj.sub_tags == [sample]
    assert obj.proposed_tags == []


def test_enrichment_result_sector_mix_round_trip():
    obj = EnrichmentResult.model_validate(
        {
            "primary_sector": "Conglomerates / Family Groups / Holdings",
            "confidence": 0.8,
            "sector_mix": [
                {"sector": "Conglomerates / Family Groups / Holdings", "weight": "dominant"},
                {"sector": "Retail & Consumer Goods", "weight": "significant"},
                {"sector": "Real Estate Development", "weight": "minor"},
            ],
        }
    )
    assert len(obj.sector_mix) == 3
    assert obj.sector_mix[0].weight == "dominant"


def test_enrichment_result_accepts_contact_fields():
    obj = EnrichmentResult.model_validate(
        {
            "primary_sector": "Real Estate Development",
            "confidence": 0.7,
            "website": "https://acme.ae",
            "phone": "+97141234567",
            "email": "info@acme.ae",
            "address": "P.O. Box 1234, Dubai",
        }
    )
    assert obj.website == "https://acme.ae"
    assert obj.phone == "+97141234567"
    assert obj.email == "info@acme.ae"
    assert obj.address == "P.O. Box 1234, Dubai"
