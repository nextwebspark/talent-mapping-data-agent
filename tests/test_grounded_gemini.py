"""Unit tests for the grounded Gemini tool.

We do NOT call the real Gemini API. Tests exercise the parse + merge logic
and verify that the public function wires the response through Pydantic.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tools import grounded_gemini


def test_extract_json_plain():
    text = '{"a": 1, "b": "x"}'
    assert grounded_gemini._extract_json(text) == {"a": 1, "b": "x"}


def test_extract_json_fenced_markdown():
    text = 'here is the result:\n```json\n{"primary_sector": "Banking & Financial Services", "confidence": 0.8}\n```'
    out = grounded_gemini._extract_json(text)
    assert out["primary_sector"] == "Banking & Financial Services"
    assert out["confidence"] == 0.8


def test_extract_json_with_leading_text():
    text = 'Here you go: {"primary_sector": "Insurance", "confidence": 0.5} done'
    assert grounded_gemini._extract_json(text)["primary_sector"] == "Insurance"


def test_extract_json_raises_on_garbage():
    with pytest.raises(ValueError):
        grounded_gemini._extract_json("no braces at all")


def test_merge_grounding_sources_adds_new_urls():
    parsed = {"sources": [{"url": "https://a.com", "title": "A"}]}
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                grounding_metadata=SimpleNamespace(
                    grounding_chunks=[
                        SimpleNamespace(web=SimpleNamespace(uri="https://b.com", title="B")),
                        SimpleNamespace(web=SimpleNamespace(uri="https://c.com", title="C")),
                    ]
                )
            )
        ]
    )
    merged = grounded_gemini._merge_grounding_sources(parsed, response)
    urls = {s["url"] for s in merged["sources"]}
    assert urls == {"https://a.com", "https://b.com", "https://c.com"}


def test_merge_grounding_sources_dedupes():
    parsed = {"sources": [{"url": "https://a.com", "title": "A"}]}
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                grounding_metadata=SimpleNamespace(
                    grounding_chunks=[
                        SimpleNamespace(web=SimpleNamespace(uri="https://a.com", title="A2")),
                    ]
                )
            )
        ]
    )
    merged = grounded_gemini._merge_grounding_sources(parsed, response)
    assert len(merged["sources"]) == 1
    # First-write-wins: pre-existing entry retained
    assert merged["sources"][0]["title"] == "A"


def test_merge_grounding_sources_handles_missing_metadata():
    parsed = {"sources": []}
    response = SimpleNamespace(candidates=[SimpleNamespace(grounding_metadata=None)])
    merged = grounded_gemini._merge_grounding_sources(parsed, response)
    assert merged["sources"] == []


def test_enrich_company_grounded_wires_call(monkeypatch, sample_enrichment):
    """Stub out the genai client and verify the full parse path."""
    raw_text = json.dumps(sample_enrichment)

    fake_response = SimpleNamespace(
        text=raw_text,
        candidates=[
            SimpleNamespace(
                grounding_metadata=SimpleNamespace(
                    grounding_chunks=[
                        SimpleNamespace(
                            web=SimpleNamespace(uri="https://grounded.example/x", title="X")
                        )
                    ]
                )
            )
        ],
    )

    captured = {}

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            captured["model"] = model
            captured["contents"] = contents
            captured["config"] = config
            return fake_response

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(grounded_gemini, "_client", lambda: FakeClient())

    # Bypass tenacity retry sleep
    grounded_gemini.enrich_company_grounded.retry.wait = lambda *a, **k: 0  # noqa: E731

    out = grounded_gemini.enrich_company_grounded(
        name="Emaar", country="UAE", website="https://emaar.com"
    )

    assert out["primary_sector"] == sample_enrichment["primary_sector"]
    assert out["confidence"] == sample_enrichment["confidence"]
    urls = {s["url"] for s in out["sources"]}
    assert "https://grounded.example/x" in urls
    assert captured["model"] == "gemini-2.5-pro"
    assert "Emaar" in captured["contents"]
