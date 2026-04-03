"""ISIN-based LEI resolution logic."""

import logging
from typing import Optional

from .gleif_client import GleifClient
from .matcher import best_name_score, address_match_score, NAME_MATCH_THRESHOLD
from .models import GleifCandidate, InputEntity, LookupResult, MatchType

logger = logging.getLogger(__name__)

# Minimum name score to accept an ISIN-based match
ISIN_NAME_THRESHOLD = 50


async def resolve_via_isin(
    entity: InputEntity,
    client: GleifClient,
    hq_candidate: Optional[GleifCandidate] = None,
) -> Optional[LookupResult]:
    """Try to find LEI via ISIN code.

    Strategy:
    1. Search GLEIF for the ISIN directly (some funds have ISIN in GLEIF)
    2. If we have an HQ-matched candidate, check if ISIN corroborates it

    Conservative approach: always require some name similarity to avoid false positives.
    Skip LAPSED entities unless there's very strong evidence.
    """
    if not entity.isin:
        return None

    logger.info("Attempting ISIN resolution for %s (ISIN: %s)", entity.name, entity.isin)

    # Step 3a: Search ISIN directly in GLEIF
    isin_candidates = await client.search_by_isin(entity.isin)

    for candidate in isin_candidates:
        ns = best_name_score(entity, candidate)

        # Skip LAPSED entities unless name match is very strong
        if candidate.status == "LAPSED" and ns < 80:
            logger.info("Skipping LAPSED candidate %s (name score: %.1f)", candidate.lei, ns)
            continue

        if ns >= ISIN_NAME_THRESHOLD:
            logger.info(
                "ISIN_GLEIF_MATCH: %s -> %s (name score: %.1f)",
                entity.isin, candidate.lei, ns,
            )
            return LookupResult(
                lei=candidate.lei,
                lei_status=candidate.status,
                match_type=MatchType.ISIN_GLEIF_MATCH,
                confidence=min(75 + ns * 0.15, 95),
                gleif_legal_name=candidate.legal_name,
                gleif_legal_address=candidate.legal_address.format() if candidate.legal_address else None,
                gleif_hq_address=candidate.hq_address.format() if candidate.hq_address else None,
                notes=f"ISIN {entity.isin} nalezen v GLEIF, LEI přiřazen i přes neshodu adresy.",
            )

    # Step 3b: If we had an HQ candidate, the ISIN search might corroborate it
    if hq_candidate:
        # Skip LAPSED HQ candidates
        if hq_candidate.status == "LAPSED":
            logger.info("Skipping LAPSED HQ candidate %s", hq_candidate.lei)
            return None

        # Check if any ISIN candidate has the same LEI as our HQ candidate
        for ic in isin_candidates:
            if ic.lei == hq_candidate.lei:
                return LookupResult(
                    lei=hq_candidate.lei,
                    lei_status=hq_candidate.status,
                    match_type=MatchType.ISIN_MATCH,
                    confidence=80,
                    gleif_legal_name=hq_candidate.legal_name,
                    gleif_legal_address=hq_candidate.legal_address.format() if hq_candidate.legal_address else None,
                    gleif_hq_address=hq_candidate.hq_address.format() if hq_candidate.hq_address else None,
                    notes=(
                        f"Dle ISIN {entity.isin} je emitentem {hq_candidate.legal_name} "
                        f"— shoduje se s HQ adresou v GLEIF."
                    ),
                )

        # Only use HQ address as corroboration if ISIN search returned at least some results
        # (even if they didn't directly match the HQ candidate's LEI)
        if isin_candidates:
            hq_score, _ = address_match_score(entity, hq_candidate, "hq")
            if hq_score >= 50:
                return LookupResult(
                    lei=hq_candidate.lei,
                    lei_status=hq_candidate.status,
                    match_type=MatchType.ISIN_MATCH,
                    confidence=min(65 + hq_score * 0.15, 85),
                    gleif_legal_name=hq_candidate.legal_name,
                    gleif_legal_address=hq_candidate.legal_address.format() if hq_candidate.legal_address else None,
                    gleif_hq_address=hq_candidate.hq_address.format() if hq_candidate.hq_address else None,
                    notes=(
                        f"Adresa se shoduje pouze s headquarters. "
                        f"ISIN {entity.isin} použit jako doplňkové ověření."
                    ),
                )

    return None
