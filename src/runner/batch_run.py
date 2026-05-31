"""Batch enrichment CLI.

Direct loop (no LLM orchestration) for cheap, predictable bulk runs. Use this
for the 1K-5K MVP run. The ADK Agent itself is reserved for interactive
sessions and Vertex deployment.

Usage:
    python -m runner.batch_run --limit 100 --country "United Arab Emirates"
    python -m runner.batch_run --limit 5 --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter

from config import load_settings
from tools.grounded_gemini import enrich_company_grounded
from tools.supabase_tool import (
    build_enrichment_payload,
    fetch_unenriched_companies,
    write_enrichment,
    write_failure,
)

log = logging.getLogger(__name__)


def run(
    limit: int,
    country: str | None,
    sector: str | None,
    top_company_only: bool,
    dry_run: bool,
    sleep_s: float,
    max_failures_per_row: int,
    max_failures_before_stop: int | None,
) -> int:
    settings = load_settings()
    log.info(
        "Fetching up to %d unenriched companies (country=%s, sector=%s, top_only=%s, version=%s)",
        limit,
        country,
        sector,
        top_company_only,
        settings.prompt_version,
    )
    rows = fetch_unenriched_companies(
        limit=limit,
        country=country,
        sector=sector,
        top_company_only=top_company_only,
        prompt_version=settings.prompt_version,
        max_failures_per_row=max_failures_per_row,
    )
    log.info("Fetched %d companies", len(rows))

    enriched = 0
    failed = 0
    sector_counts: Counter[str] = Counter()
    low_confidence = 0

    for i, row in enumerate(rows, 1):
        name = row["name"]
        try:
            log.info("[%d/%d] %s (%s)", i, len(rows), name, row["company_id"])
            result = enrich_company_grounded(
                name=name,
                country=row["country"],
                website=row.get("website"),
                description=row.get("description"),
                coarse_sector=row.get("sector"),
                phone=row.get("phone"),
                email=row.get("email"),
                address=row.get("address"),
            )
            sector_counts[result["primary_sector"]] += 1
            if result.get("confidence", 0) < 0.5:
                low_confidence += 1

            if dry_run:
                print(json.dumps({"company": name, "result": result}, indent=2, default=str))
            else:
                payload = build_enrichment_payload(
                    row, result, settings.model, settings.prompt_version
                )
                write_enrichment(payload)
            enriched += 1
        except (KeyError, AttributeError, TypeError, NameError):
            # Programmer errors (malformed row, missing attribute, bad payload
            # shape) should crash the batch rather than be recorded as
            # legitimate enrichment failures and pollute the poison-pill counter.
            raise
        except Exception as exc:
            failed += 1
            log.exception("Failed to enrich %s", name)
            if not dry_run:
                try:
                    write_failure(row, exc, settings.prompt_version)
                except Exception:
                    log.exception("Also failed to record failure for %s", name)
            if max_failures_before_stop is not None and failed >= max_failures_before_stop:
                log.error(
                    "Reached max_failures_before_stop=%d; aborting batch.",
                    max_failures_before_stop,
                )
                break

        if sleep_s and i < len(rows):
            time.sleep(sleep_s)

    log.info("Done. enriched=%d failed=%d low_confidence=%d", enriched, failed, low_confidence)
    log.info("Sector distribution: %s", dict(sector_counts.most_common()))
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enrich companies in batches.")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--country", type=str, default=None)
    parser.add_argument(
        "--sector",
        type=str,
        default=None,
        help="Filter by source Zawya sector (exact match on companies.sector).",
    )
    parser.add_argument(
        "--top-company-only",
        action="store_true",
        help="Only enrich rows with top_company=true.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print results, do not write.")
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.5,
        help="Seconds to wait between companies (rate-limit cushion).",
    )
    parser.add_argument(
        "--max-failures-per-row",
        type=int,
        default=3,
        help="Skip companies with >= this many recorded failures at current prompt_version.",
    )
    parser.add_argument(
        "--max-failures-before-stop",
        type=int,
        default=None,
        help="If set, abort the batch once this many failures occur (quota protection).",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return run(
        limit=args.limit,
        country=args.country,
        sector=args.sector,
        top_company_only=args.top_company_only,
        dry_run=args.dry_run,
        sleep_s=args.sleep,
        max_failures_per_row=args.max_failures_per_row,
        max_failures_before_stop=args.max_failures_before_stop,
    )


if __name__ == "__main__":
    sys.exit(main())
