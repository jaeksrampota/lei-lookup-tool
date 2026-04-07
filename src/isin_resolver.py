"""ISIN-based LEI resolution logic."""

import logging
from typing import Optional

from .gleif_client import GleifClient
from .matcher import best_name_score, name_similarity, address_match_score, NAME_MATCH_THRESHOLD
from .models import GleifCandidate, InputEntity, LookupResult, MatchType
from .openfigi_client import resolve_isin_to_name, resolve_isin_to_names

logger = logging.getLogger(__name__)

# Minimum name score to accept an ISIN-based match
ISIN_NAME_THRESHOLD = 50


async def resolve_via_isin(
    entity: InputEntity,
    client: GleifClient,
    hq_candidate: Optional[GleifCandidate] = None,
    name_candidate: Optional[GleifCandidate] = None,
) -> Optional[LookupResult]:
    """Try to find LEI via ISIN code.

    Strategy:
    1. Search GLEIF for the ISIN directly (some funds have ISIN in GLEIF)
    2. If we have an HQ-matched candidate, check if ISIN corroborates it
    3. If we have a strong name-matched candidate, check if ISIN corroborates it

    Conservative approach: always require some name similarity to avoid false positives.
    """
    if not entity.isin:
        return None

    logger.info("Attempting ISIN resolution for %s (ISIN: %s)", entity.name, entity.isin)

    # Step 3a: Search ISIN directly in GLEIF
    isin_candidates = await client.search_by_isin(entity.isin)

    for candidate in isin_candidates:
        ns = best_name_score(entity, candidate)

        # Skip LAPSED entities unless name match is reasonably strong
        if candidate.status == "LAPSED" and ns < 60:
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
        # Check if any ISIN candidate has the same LEI as our HQ candidate
        lapsed_note = ""
        if hq_candidate.status == "LAPSED":
            lapsed_note = " POZOR: LEI má status LAPSED (neudržovaný)."

        for ic in isin_candidates:
            if ic.lei == hq_candidate.lei:
                confidence = 75 if hq_candidate.status == "LAPSED" else 80
                return LookupResult(
                    lei=hq_candidate.lei,
                    lei_status=hq_candidate.status,
                    match_type=MatchType.ISIN_MATCH,
                    confidence=confidence,
                    gleif_legal_name=hq_candidate.legal_name,
                    gleif_legal_address=hq_candidate.legal_address.format() if hq_candidate.legal_address else None,
                    gleif_hq_address=hq_candidate.hq_address.format() if hq_candidate.hq_address else None,
                    notes=(
                        f"Dle ISIN {entity.isin} je emitentem {hq_candidate.legal_name} "
                        f"— shoduje se s HQ adresou v GLEIF.{lapsed_note}"
                    ),
                )

        # Only use HQ address as corroboration if ISIN search returned at least some results
        # (even if they didn't directly match the HQ candidate's LEI)
        if isin_candidates:
            hq_score, _ = address_match_score(entity, hq_candidate, "hq")
            if hq_score >= 50:
                confidence = min(65 + hq_score * 0.15, 85)
                if hq_candidate.status == "LAPSED":
                    confidence = min(confidence, 75)
                return LookupResult(
                    lei=hq_candidate.lei,
                    lei_status=hq_candidate.status,
                    match_type=MatchType.ISIN_MATCH,
                    confidence=confidence,
                    gleif_legal_name=hq_candidate.legal_name,
                    gleif_legal_address=hq_candidate.legal_address.format() if hq_candidate.legal_address else None,
                    gleif_hq_address=hq_candidate.hq_address.format() if hq_candidate.hq_address else None,
                    notes=(
                        f"Adresa se shoduje pouze s headquarters. "
                        f"ISIN {entity.isin} použit jako doplňkové ověření.{lapsed_note}"
                    ),
                )

    # Step 3c: If we have a strong name-matched candidate (no address match),
    # check if ISIN search corroborates it
    if name_candidate and name_candidate != hq_candidate:
        ns = best_name_score(entity, name_candidate)
        if ns >= 85:
            lapsed_note = ""
            if name_candidate.status == "LAPSED":
                lapsed_note = " POZOR: LEI má status LAPSED (neudržovaný)."

            # Check if any ISIN candidate shares the same LEI
            for ic in isin_candidates:
                if ic.lei == name_candidate.lei:
                    confidence = min(70 + ns * 0.1, 85)
                    if name_candidate.status == "LAPSED":
                        confidence = min(confidence, 75)
                    return LookupResult(
                        lei=name_candidate.lei,
                        lei_status=name_candidate.status,
                        match_type=MatchType.ISIN_MATCH,
                        confidence=confidence,
                        gleif_legal_name=name_candidate.legal_name,
                        gleif_legal_address=name_candidate.legal_address.format() if name_candidate.legal_address else None,
                        gleif_hq_address=name_candidate.hq_address.format() if name_candidate.hq_address else None,
                        notes=(
                            f"Silná shoda názvu ({ns:.0f}%) s {name_candidate.legal_name}. "
                            f"ISIN {entity.isin} potvrzuje přiřazení LEI.{lapsed_note}"
                        ),
                    )

    # Step 3d: OpenFIGI fallback — resolve ISIN to ALL names, re-search GLEIF
    figi_names = await resolve_isin_to_names(entity.isin)
    for figi_name in figi_names:
        logger.info("OpenFIGI resolved ISIN %s -> '%s', re-searching GLEIF", entity.isin, figi_name)
        figi_candidates = await client.search_by_name(figi_name, country=None, page_size=5)

        for candidate in figi_candidates:
            # Double check: both input name and OpenFIGI name must match the candidate
            input_ns = best_name_score(entity, candidate)
            figi_ns = name_similarity(figi_name, candidate.legal_name)

            if input_ns >= 65 and figi_ns >= 65:
                lapsed_note = ""
                if candidate.status == "LAPSED":
                    lapsed_note = " POZOR: LEI má status LAPSED (neudržovaný)."

                confidence = min(65 + (input_ns + figi_ns) * 0.05, 85)
                if candidate.status == "LAPSED":
                    confidence = min(confidence, 70)

                return LookupResult(
                    lei=candidate.lei,
                    lei_status=candidate.status,
                    match_type=MatchType.ISIN_GLEIF_MATCH,
                    confidence=confidence,
                    gleif_legal_name=candidate.legal_name,
                    gleif_legal_address=candidate.legal_address.format() if candidate.legal_address else None,
                    gleif_hq_address=candidate.hq_address.format() if candidate.hq_address else None,
                    notes=(
                        f"ISIN {entity.isin} vyhledán přes OpenFIGI ({figi_name}), "
                        f"LEI nalezen v GLEIF.{lapsed_note}"
                    ),
                )

    return None
