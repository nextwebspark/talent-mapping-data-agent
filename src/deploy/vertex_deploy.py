"""Deploy the enrichment agent to Vertex AI Agent Engine.

Wraps `root_agent` in `reasoning_engines.AdkApp` and creates / updates an
Agent Engine instance. Requires:
  - GOOGLE_CLOUD_PROJECT
  - GOOGLE_CLOUD_LOCATION
  - GCS_STAGING_BUCKET
  - SUPABASE_URL, SUPABASE_SERVICE_KEY (passed through as env_vars so the
    deployed instance can call Supabase)

Usage:
    python -m deploy.vertex_deploy --create
    python -m deploy.vertex_deploy --update <resource_name>
    python -m deploy.vertex_deploy --list
    python -m deploy.vertex_deploy --delete <resource_name>
"""

from __future__ import annotations

import argparse
import logging
import sys

import vertexai
from vertexai import agent_engines
from vertexai.preview import reasoning_engines

from agent.enrichment_agent import root_agent
from config import load_settings

log = logging.getLogger(__name__)

REQUIREMENTS = [
    "google-adk>=0.5.0",
    "google-genai>=0.8.0",
    "google-cloud-aiplatform[adk,agent_engines]>=1.71.0",
    "supabase>=2.7.0",
    "pydantic>=2.7.0",
    "python-dotenv>=1.0.1",
    "tenacity>=8.5.0",
]


def _init_vertex(settings) -> None:
    if not settings.gcp_project or not settings.gcs_staging_bucket:
        raise RuntimeError(
            "GOOGLE_CLOUD_PROJECT and GCS_STAGING_BUCKET must be set for Vertex deploy."
        )
    vertexai.init(
        project=settings.gcp_project,
        location=settings.gcp_location,
        staging_bucket=settings.gcs_staging_bucket,
    )


def _env_vars(settings) -> dict[str, str]:
    return {
        "SUPABASE_URL": settings.supabase_url,
        "SUPABASE_SERVICE_KEY": settings.supabase_service_key,
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "GOOGLE_CLOUD_PROJECT": settings.gcp_project or "",
        "GOOGLE_CLOUD_LOCATION": settings.gcp_location,
        "ENRICHMENT_MODEL": settings.model,
        "PROMPT_VERSION": settings.prompt_version,
    }


def create() -> str:
    settings = load_settings()
    _init_vertex(settings)
    app = reasoning_engines.AdkApp(agent=root_agent, enable_tracing=True)
    remote = agent_engines.create(
        agent_engine=app,
        display_name="hak-company-enricher",
        description="HAK company enrichment agent.",
        requirements=REQUIREMENTS,
        extra_packages=["src"],
        env_vars=_env_vars(settings),
    )
    log.info("Created: %s", remote.resource_name)
    return remote.resource_name


def update(resource_name: str) -> str:
    settings = load_settings()
    _init_vertex(settings)
    app = reasoning_engines.AdkApp(agent=root_agent, enable_tracing=True)
    remote = agent_engines.update(
        resource_name=resource_name,
        agent_engine=app,
        requirements=REQUIREMENTS,
        extra_packages=["src"],
        env_vars=_env_vars(settings),
    )
    log.info("Updated: %s", remote.resource_name)
    return remote.resource_name


def list_engines() -> None:
    settings = load_settings()
    _init_vertex(settings)
    for e in agent_engines.list():
        print(f"{e.resource_name}\t{getattr(e, 'display_name', '')}")


def delete(resource_name: str) -> None:
    settings = load_settings()
    _init_vertex(settings)
    agent_engines.delete(resource_name=resource_name, force=True)
    log.info("Deleted: %s", resource_name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deploy enrichment agent to Vertex.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--create", action="store_true")
    group.add_argument("--update", type=str, metavar="RESOURCE_NAME")
    group.add_argument("--list", action="store_true")
    group.add_argument("--delete", type=str, metavar="RESOURCE_NAME")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.create:
        print(create())
    elif args.update:
        print(update(args.update))
    elif args.list:
        list_engines()
    elif args.delete:
        delete(args.delete)
    return 0


if __name__ == "__main__":
    sys.exit(main())
