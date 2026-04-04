"""Main orchestration logic and CLI entry point for LEI Lookup Tool."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

from .address import country_to_iso, normalize_name
from .batch import read_input_xlsx, write_output_xlsx
from .gleif_client import GleifApiError, GleifClient
from .isin_resolver import resolve_via_isin
from .matcher import (
    NAME_MATCH_THRESHOLD,
    CITY_MATCH_THRESHOLD,
    STREET_MATCH_THRESHOLD,
    address_match_score,
    best_name_score,
)
from .models import GleifCandidate, InputEntity, LookupResult, MatchType

logger = logging.getLogger(__name__)


async def lookup_entity(entity: InputEntity, client: GleifClient) -> LookupResult:
    """Run the full matching pipeline for a single entity.

    Steps:
    1. Search GLEIF by name (with and without country filter)
    2. For each candidate: check legal address match -> FULL_MATCH
    3. If no legal match: check HQ address match -> hold for ISIN verification
    4. If ISIN available: try ISIN resolution
    5. Return best result or NO_MATCH
    """
    logger.info("Looking up: %s (country: %s, ISIN: %s)", entity.name, entity.country, entity.isin)

    iso_country = country_to_iso(entity.country)

    # Step 1: Search GLEIF by name
    try:
        candidates = await client.search_by_name(entity.name, country=iso_country)

        # If no results with country filter, try without
        if not candidates and iso_country:
            logger.info("No results with country filter, trying without...")
            candidates = await client.search_by_name_no_country(entity.name)
    except GleifApiError as e:
        logger.error("GLEIF API unavailable for %s: %s", entity.name, e)
        return LookupResult(
            match_type=MatchType.NO_MATCH,
            notes=f"Chyba při komunikaci s GLEIF API: služba nedostupná po opakovaných pokusech.",
        )

    if not candidates:
        logger.info("No GLEIF candidates found for %s", entity.name)
        # Try ISIN as last resort
        if entity.isin:
            isin_result = await resolve_via_isin(entity, client)
            if isin_result:
                return isin_result
        return LookupResult(
            match_type=MatchType.NO_MATCH,
            notes="Žádný LEI nalezen v GLEIF databázi.",
        )

    # Step 2 & 3: Evaluate candidates
    best_full_match: Optional[LookupResult] = None
    best_hq_candidate: Optional[GleifCandidate] = None
    best_hq_score: float = 0.0
    best_hq_name_score: float = 0.0

    for candidate in candidates:
        ns = best_name_score(entity, candidate)
        if ns < NAME_MATCH_THRESHOLD:
            logger.debug("Skipping %s (name score: %.1f)", candidate.lei, ns)
            continue

        # Check legal address
        legal_score, legal_details = address_match_score(entity, candidate, "legal")
        if (
            legal_details.get("country_match") is not False
            and legal_details.get("city_score", 0) >= CITY_MATCH_THRESHOLD
            and legal_score >= 40
        ):
            confidence = min(85 + ns * 0.1 + legal_score * 0.05, 100)
            result = LookupResult(
                lei=candidate.lei,
                lei_status=candidate.status,
                match_type=MatchType.FULL_MATCH,
                confidence=confidence,
                gleif_legal_name=candidate.legal_name,
                gleif_legal_address=candidate.legal_address.format() if candidate.legal_address else None,
                gleif_hq_address=candidate.hq_address.format() if candidate.hq_address else None,
                notes="Plná shoda názvu a legal address.",
            )
            if best_full_match is None or confidence > best_full_match.confidence:
                best_full_match = result

        # Check HQ address (even if legal matched, we track the best HQ candidate)
        hq_score, hq_details = address_match_score(entity, candidate, "hq")
        if (
            hq_details.get("country_match") is not False
            and hq_details.get("city_score", 0) >= CITY_MATCH_THRESHOLD
            and hq_score > best_hq_score
        ):
            best_hq_candidate = candidate
            best_hq_score = hq_score
            best_hq_name_score = ns

    # If we have a full match, return it
    if best_full_match:
        logger.info("FULL_MATCH found: %s", best_full_match.lei)
        return best_full_match

    # Step 4: Try ISIN resolution (if HQ matched or no match at all)
    if entity.isin:
        isin_result = await resolve_via_isin(entity, client, hq_candidate=best_hq_candidate)
        if isin_result:
            logger.info("ISIN resolution succeeded: %s -> %s", entity.isin, isin_result.lei)
            return isin_result

    # If we have an HQ match but no ISIN confirmation, report as NO_MATCH
    # per requirements: HQ-only matches without ISIN verification should not be assigned
    if best_hq_candidate and best_hq_score >= 40:
        legal_addr_str = ""
        if best_hq_candidate.legal_address:
            legal_addr_str = best_hq_candidate.legal_address.format()
        hq_addr_str = ""
        if best_hq_candidate.hq_address:
            hq_addr_str = best_hq_candidate.hq_address.format()

        status_note = ""
        if best_hq_candidate.status == "LAPSED":
            status_note = f" Status LEI: LAPSED."

        return LookupResult(
            match_type=MatchType.NO_MATCH,
            confidence=0,
            gleif_legal_name=best_hq_candidate.legal_name,
            gleif_legal_address=legal_addr_str or None,
            gleif_hq_address=hq_addr_str or None,
            notes=(
                f"Název odpovídá ({best_hq_candidate.legal_name}), "
                f"adresa se shoduje pouze s headquarters ({hq_addr_str}). "
                f"Legal address: {legal_addr_str}. "
                f"LEI nepřiřazen — shoda pouze s HQ adresou bez potvrzení přes ISIN."
                f"{status_note}"
            ),
        )

    # No match
    return LookupResult(
        match_type=MatchType.NO_MATCH,
        notes="Žádný LEI nalezen v GLEIF databázi.",
    )


async def process_batch(input_path: str, output_path: str) -> None:
    """Process a batch of entities from an Excel file."""
    entities = read_input_xlsx(input_path)
    client = GleifClient()

    results = []
    try:
        for i, entity in enumerate(entities, 1):
            logger.info("Processing %d/%d: %s", i, len(entities), entity.name)
            result = await lookup_entity(entity, client)
            results.append(result)
            logger.info(
                "  -> %s (confidence: %.1f%%) %s",
                result.match_type.value, result.confidence, result.lei or "",
            )
    finally:
        await client.close()

    write_output_xlsx(entities, results, output_path)
    print(f"\nDone! Processed {len(entities)} entities -> {output_path}")

    # Summary
    match_counts = {}
    for r in results:
        mt = r.match_type.value
        match_counts[mt] = match_counts.get(mt, 0) + 1
    print("\nSummary:")
    for mt, count in sorted(match_counts.items()):
        print(f"  {mt}: {count}")


async def single_lookup(name: str, country: Optional[str] = None, isin: Optional[str] = None) -> None:
    """Perform a single entity lookup and print results."""
    entity = InputEntity(name=name, country=country, isin=isin)
    client = GleifClient()

    try:
        result = await lookup_entity(entity, client)
    finally:
        await client.close()

    print(f"\nEntity: {name}")
    print(f"LEI: {result.lei or 'NOT FOUND'}")
    print(f"Status: {result.lei_status or 'N/A'}")
    print(f"Match type: {result.match_type.value}")
    print(f"Confidence: {result.confidence:.1f}%")
    if result.gleif_legal_name:
        print(f"GLEIF name: {result.gleif_legal_name}")
    if result.gleif_legal_address:
        print(f"Legal address: {result.gleif_legal_address}")
    if result.gleif_hq_address:
        print(f"HQ address: {result.gleif_hq_address}")
    print(f"Notes: {result.notes}")


def main():
    parser = argparse.ArgumentParser(description="LEI Lookup Tool")
    parser.add_argument("--input", "-i", help="Input XLSX file path")
    parser.add_argument("--output", "-o", help="Output XLSX file path", default="results.xlsx")
    parser.add_argument("--name", "-n", help="Single entity name to look up")
    parser.add_argument("--country", "-c", help="Country (Czech name or ISO code)")
    parser.add_argument("--isin", help="ISIN code")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.input:
        asyncio.run(process_batch(args.input, args.output))
    elif args.name:
        asyncio.run(single_lookup(args.name, args.country, args.isin))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
