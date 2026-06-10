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
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any

from config import Settings, load_settings
from tools.grounded_gemini import enrich_company_grounded
from tools.supabase_tool import (
    build_enrichment_payload,
    fetch_failed_companies,
    fetch_unenriched_companies,
    write_enrichment,
    write_failure,
)

log = logging.getLogger(__name__)


def _process_company(
    row: dict[str, Any],
    i: int,
    total: int,
    settings: Settings,
    dry_run: bool,
) -> tuple[str, dict[str, Any]]:
    """Enrich one company and (unless dry-run) write it.

    Runs inside a worker thread. Returns a structured outcome instead of
    mutating shared state so the caller can aggregate single-threaded:
      - ("enriched", {"primary_sector": ..., "confidence": ...})
      - ("failed", {"error": exc})

    Programmer errors (KeyError, AttributeError, TypeError, NameError) are
    re-raised so a malformed row crashes the batch rather than being recorded
    as a legitimate enrichment failure (which would pollute the poison-pill
    counter). Failures are NOT written here — the caller writes them serially
    to avoid the attempt-number race in write_failure under concurrency.
    """
    name = row["name"]
    log.info("[%d/%d] %s (%s)", i, total, name, row["company_id"])
    try:
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
        if dry_run:
            print(json.dumps({"company": name, "result": result}, indent=2, default=str))
        else:
            payload = build_enrichment_payload(row, result, settings.model, settings.prompt_version)
            write_enrichment(payload)
        return "enriched", result
    except (KeyError, AttributeError, TypeError, NameError):
        raise
    except Exception as exc:
        log.exception("Failed to enrich %s", name)
        return "failed", {"error": exc}


def run(
    limit: int,
    country: str | None,
    sector: str | None,
    top_company_only: bool,
    dry_run: bool,
    sleep_s: float,
    max_failures_per_row: int,
    max_failures_before_stop: int | None,
    concurrency: int = 5,
    retry_failed: bool = False,
) -> int:
    settings = load_settings()
    mode = "retry-failed" if retry_failed else "pending"
    log.info(
        "Enrichment model: %s (prompt_version=%s, mode=%s)",
        settings.model,
        settings.prompt_version,
        mode,
    )
    log.info(
        "Fetching up to %d %s companies (country=%s, sector=%s, top_only=%s, version=%s)",
        limit,
        "failed" if retry_failed else "unenriched",
        country,
        sector,
        top_company_only,
        settings.prompt_version,
    )
    fetch = fetch_failed_companies if retry_failed else fetch_unenriched_companies
    rows = fetch(
        limit=limit,
        country=country,
        sector=sector,
        top_company_only=top_company_only,
        prompt_version=settings.prompt_version,
        max_failures_per_row=max_failures_per_row,
    )
    total = len(rows)
    log.info("Fetched %d companies (concurrency=%d)", total, concurrency)

    enriched = 0
    failed = 0
    sector_counts: Counter[str] = Counter()
    low_confidence = 0
    aborted = False

    workers = max(1, concurrency)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        # Map each future back to its source row so failures (written serially
        # in the main thread) carry the right company.
        futures = {
            executor.submit(_process_company, row, i, total, settings, dry_run): row
            for i, row in enumerate(rows, 1)
        }
        pending = set(futures)
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for fut in done:
                row = futures[fut]
                status, payload = fut.result()
                if status == "enriched":
                    enriched += 1
                    sector_counts[payload["primary_sector"]] += 1
                    if payload.get("confidence", 0) < 0.5:
                        low_confidence += 1
                else:  # failed
                    failed += 1
                    if not dry_run:
                        try:
                            write_failure(
                                row,
                                payload["error"],
                                settings.prompt_version,
                                max_failures_per_row=max_failures_per_row,
                            )
                        except Exception:
                            log.exception("Also failed to record failure for %s", row["name"])
                    if max_failures_before_stop is not None and failed >= max_failures_before_stop:
                        log.error(
                            "Reached max_failures_before_stop=%d; aborting batch.",
                            max_failures_before_stop,
                        )
                        aborted = True
                        break
            if aborted:
                # Cancel not-yet-started work; in-flight calls finish on their own.
                for fut in pending:
                    fut.cancel()
                break

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
        "--retry-failed",
        action="store_true",
        help="Re-run companies stamped enrichment_status='failed' (e.g. for a "
        "Gemini Pro retry sweep via ENRICHMENT_MODEL=gemini-2.5-pro) instead of "
        "the normal pending queue.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Number of companies enriched in parallel (in-flight Gemini calls). "
        "Set 1 for strictly-serial behavior; lower it if you hit quota/429 errors.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.5,
        help="Deprecated no-op. Throttling is now governed by --concurrency.",
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
        concurrency=args.concurrency,
        retry_failed=args.retry_failed,
    )


if __name__ == "__main__":
    sys.exit(main())
