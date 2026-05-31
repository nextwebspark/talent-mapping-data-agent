"""Grounded Gemini enrichment tool.

Calls Gemini with the Google Search grounding tool, extracts structured JSON,
and merges grounding citations from the response into `sources`.

Note: when `google_search` grounding is enabled, the Gemini API does NOT accept
a `response_schema` constraint simultaneously. We therefore instruct strict JSON
output via the system instruction and parse + validate with Pydantic ourselves.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from google.genai import Client
from google.genai.types import (
    GenerateContentConfig,
    GoogleSearch,
    HttpOptions,
    Tool,
)
from pydantic import ValidationError
from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from agent.prompts import EnrichmentResult, build_user_prompt, system_instruction
from config import load_settings

log = logging.getLogger(__name__)

# Greedy match (with DOTALL) so nested `}` inside the JSON object don't
# truncate the capture early.
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def _client() -> Client:
    s = load_settings()
    if s.use_vertex_ai:
        return Client(
            vertexai=True,
            project=s.gcp_project,
            location=s.gcp_location,
            http_options=HttpOptions(api_version="v1"),
        )
    return Client(api_key=s.google_api_key)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    match = _JSON_FENCE_RE.search(text)
    if match:
        text = match.group(1)
    # Tolerate leading "json" or stray prefix
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace == -1 or last_brace == -1:
        raise ValueError(f"No JSON object found in model output: {text[:200]}")
    return json.loads(text[first_brace : last_brace + 1])


def _merge_grounding_sources(parsed: dict[str, Any], response: Any) -> dict[str, Any]:
    """Append grounding chunks from the response onto parsed['sources'].

    Model citations + grounding metadata can each contribute URLs; we dedupe by URL.
    """
    existing = parsed.get("sources") or []
    by_url: dict[str, dict[str, Any]] = {}
    for src in existing:
        url = src.get("url") if isinstance(src, dict) else None
        if url:
            by_url[url] = src

    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        gm = getattr(cand, "grounding_metadata", None)
        chunks = getattr(gm, "grounding_chunks", None) if gm else None
        if not chunks:
            continue
        for chunk in chunks:
            web = getattr(chunk, "web", None)
            if not web:
                continue
            url = getattr(web, "uri", None) or getattr(web, "url", None)
            title = getattr(web, "title", None)
            if not url:
                continue
            if url not in by_url:
                by_url[url] = {"url": url, "title": title, "snippet": None}

    parsed["sources"] = list(by_url.values())
    return parsed


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    # Don't burn 3 Gemini calls on a persistently malformed model output.
    retry=retry_if_not_exception_type((ValidationError, ValueError)),
    reraise=True,
)
def enrich_company_grounded(
    name: str,
    country: str,
    website: str | None = None,
    description: str | None = None,
    coarse_sector: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    address: str | None = None,
) -> dict[str, Any]:
    """Enrich a single company. Returns a dict matching EnrichmentResult.

    Args:
        name: Company name.
        country: Country (free-form, e.g. 'United Arab Emirates').
        website: Optional website URL.
        description: Optional existing description from source data.
        coarse_sector: Optional existing coarse sector label from source data.
        phone: Optional existing phone from source data (may be stale).
        email: Optional existing email from source data (may be stale).
        address: Optional existing address from source data (may be stale).

    Returns:
        Parsed + validated enrichment dict, with grounding URLs merged into
        `sources`.
    """
    settings = load_settings()
    client = _client()
    prompt = build_user_prompt(
        name,
        country,
        website,
        description,
        coarse_sector,
        phone=phone,
        email=email,
        address=address,
    )

    config = GenerateContentConfig(
        system_instruction=system_instruction(),
        tools=[Tool(google_search=GoogleSearch())],
        temperature=0.2,
    )

    response = client.models.generate_content(
        model=settings.model,
        contents=prompt,
        config=config,
    )

    text = response.text or ""
    parsed = _extract_json(text)
    parsed = _merge_grounding_sources(parsed, response)

    try:
        validated = EnrichmentResult.model_validate(parsed)
    except ValidationError:
        log.exception("Validation failed for %s; raw=%s", name, text[:500])
        raise

    return validated.model_dump()
