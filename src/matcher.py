"""Fuzzy matching logic for entity names and addresses."""

import logging
from typing import Optional

from rapidfuzz import fuzz

from .address import country_to_iso, extract_zip, normalize_address_part, normalize_name
from .models import GleifCandidate, InputEntity

logger = logging.getLogger(__name__)

# Thresholds
NAME_MATCH_THRESHOLD = 75
CITY_MATCH_THRESHOLD = 70
STREET_MATCH_THRESHOLD = 55


def name_similarity(input_name: str, gleif_name: str) -> float:
    """Compute name similarity score (0-100)."""
    n1 = normalize_name(input_name)
    n2 = normalize_name(gleif_name)

    if not n1 or not n2:
        return 0.0

    # Use token_set_ratio for compound names (handles word reordering)
    score = fuzz.token_set_ratio(n1, n2)

    # Also try partial_ratio for substring matching (short names within longer ones)
    partial = fuzz.partial_ratio(n1, n2)

    # Straight ratio for exact-ish matching
    straight = fuzz.ratio(n1, n2)

    return max(score, partial, straight)


def best_name_score(entity: InputEntity, candidate: GleifCandidate) -> float:
    """Compute the best name match score across legal name and other names."""
    scores = [name_similarity(entity.name, candidate.legal_name)]

    for other_name in candidate.other_names:
        scores.append(name_similarity(entity.name, other_name))

    return max(scores)


def city_similarity(input_city: Optional[str], gleif_city: Optional[str]) -> float:
    """Compare city names with fuzzy matching."""
    c1 = normalize_address_part(input_city)
    c2 = normalize_address_part(gleif_city)

    if not c1 or not c2:
        return 0.0

    # Handle compound cities like "Hradec Kralove, Plotiste nad Labem"
    # Compare the first part (main city)
    c1_main = c1.split(",")[0].strip()
    c2_main = c2.split(",")[0].strip()

    return max(
        fuzz.token_set_ratio(c1, c2),
        fuzz.token_set_ratio(c1_main, c2_main),
        fuzz.partial_ratio(c1_main, c2_main),
    )


def street_similarity(input_street: Optional[str], gleif_street: Optional[str]) -> float:
    """Compare street addresses with fuzzy matching."""
    s1 = normalize_address_part(input_street)
    s2 = normalize_address_part(gleif_street)

    if not s1 or not s2:
        return 0.0

    return max(
        fuzz.token_set_ratio(s1, s2),
        fuzz.partial_ratio(s1, s2),
    )


def _gleif_street(candidate: GleifCandidate, addr_type: str) -> str:
    """Extract street from candidate's address."""
    addr = candidate.legal_address if addr_type == "legal" else candidate.hq_address
    if not addr or not addr.address_lines:
        return ""
    return ", ".join(addr.address_lines)


def _gleif_city(candidate: GleifCandidate, addr_type: str) -> Optional[str]:
    addr = candidate.legal_address if addr_type == "legal" else candidate.hq_address
    return addr.city if addr else None


def _gleif_country(candidate: GleifCandidate, addr_type: str) -> Optional[str]:
    addr = candidate.legal_address if addr_type == "legal" else candidate.hq_address
    return addr.country if addr else None


def address_match_score(
    entity: InputEntity, candidate: GleifCandidate, addr_type: str
) -> tuple[float, dict]:
    """
    Compute address match score for a candidate.
    addr_type: "legal" or "hq"

    Returns (score 0-100, details dict).
    """
    input_country = country_to_iso(entity.country)
    gleif_country = _gleif_country(candidate, addr_type)

    # Country MUST match
    if input_country and gleif_country and input_country != gleif_country:
        return 0.0, {"country_match": False}

    # No country info at all — can't verify
    if not input_country and not gleif_country:
        country_score = 50.0
    elif input_country and gleif_country:
        country_score = 100.0
    else:
        country_score = 30.0

    city_score = city_similarity(entity.town, _gleif_city(candidate, addr_type))
    street_score = street_similarity(entity.street, _gleif_street(candidate, addr_type))

    # Weighted combination: country is pass/fail above, city and street matter most
    overall = city_score * 0.5 + street_score * 0.5

    details = {
        "country_match": input_country == gleif_country if (input_country and gleif_country) else None,
        "city_score": city_score,
        "street_score": street_score,
        "overall": overall,
    }

    return overall, details


def compute_confidence(
    name_score: float,
    address_score: float,
    addr_type: str,
    is_isin_match: bool = False,
) -> float:
    """Compute final confidence score based on name and address match."""
    if addr_type == "legal":
        # FULL_MATCH: weight name 30%, address 70%
        base = name_score * 0.3 + address_score * 0.7
        return min(max(base, 0), 100)
    elif addr_type == "hq":
        # HQ_MATCH: lower confidence
        base = name_score * 0.3 + address_score * 0.7
        return min(max(base * 0.7, 0), 100)  # Scale down by 30%
    elif is_isin_match:
        return min(max(address_score * 0.4 + name_score * 0.3 + 30, 0), 100)
    else:
        return 0.0
