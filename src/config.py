"""Environment config. Loads .env if present; never overrides existing env."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_service_key: str
    google_api_key: str | None
    use_vertex_ai: bool
    gcp_project: str | None
    gcp_location: str
    gcs_staging_bucket: str | None
    model: str
    prompt_version: str


def _get(name: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.getenv(name, default)
    if required and not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def load_settings() -> Settings:
    service_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
    if not service_key:
        raise RuntimeError("Missing required env var: SUPABASE_SERVICE_KEY (or SUPABASE_KEY)")
    return Settings(
        supabase_url=_get("SUPABASE_URL", required=True),  # type: ignore[arg-type]
        supabase_service_key=service_key,
        google_api_key=_get("GOOGLE_API_KEY"),
        use_vertex_ai=_get("GOOGLE_GENAI_USE_VERTEXAI", "false").lower() == "true",
        gcp_project=_get("GOOGLE_CLOUD_PROJECT"),
        gcp_location=_get("GOOGLE_CLOUD_LOCATION", "us-central1") or "us-central1",
        gcs_staging_bucket=_get("GCS_STAGING_BUCKET"),
        model=_get("ENRICHMENT_MODEL", "gemini-2.5-flash") or "gemini-2.5-flash",
        prompt_version=_get("PROMPT_VERSION", "v1") or "v1",
    )
