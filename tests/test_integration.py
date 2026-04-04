"""Integration tests against the 18 sample records.

These tests hit the real GLEIF API and validate expected outcomes.
Mark with @pytest.mark.integration so they can be skipped in CI.
"""

import pytest
import asyncio
from pathlib import Path

from src.batch import read_input_xlsx
from src.gleif_client import GleifClient
from src.main import lookup_entity
from src.models import MatchType

SAMPLE_FILE = Path(__file__).resolve().parent.parent / "LEI_dohledavani.xlsx"

# Expected results: (row_index, expected_match_type, expected_lei_or_none)
EXPECTED = [
    # Row 1: BNP Paribas Guernsey branch - NO_MATCH (LEI only for parent)
    (0, MatchType.NO_MATCH, None),
    # Row 2: CAIAC Fund Management AG - FULL_MATCH
    (1, MatchType.FULL_MATCH, "529900PY3KLUDU87D755"),
    # Row 3: TECAM PCV a.s. - FULL_MATCH
    (2, MatchType.FULL_MATCH, "315700ANNRQD4SG6QE82"),
    # Row 4: Silvercrest Inc - NO_MATCH (LEI only for LLC)
    (3, MatchType.NO_MATCH, None),
    # Row 5: Avenue Therapeutics Inc - NO_MATCH (HQ only, LAPSED)
    (4, MatchType.NO_MATCH, None),
    # Row 6: Morgan Stanley Direct Lending Fund - ISIN_MATCH (9)
    (5, {MatchType.ISIN_MATCH, MatchType.ISIN_GLEIF_MATCH}, "549300QEX22T2J8IB029"),
    # Row 7: Polar Capital LLP - FULL_MATCH
    (6, MatchType.FULL_MATCH, "4YW3JKTZ3K1II2GVCK15"),
    # Row 8: Simplea Euro Bond Opportunity - FULL_MATCH
    (7, MatchType.FULL_MATCH, "315700O17CTPSTGJHI02"),
    # Row 9: Interactive Brokers Hong Kong - FULL_MATCH
    (8, MatchType.FULL_MATCH, "5493006E0OXBY133DB14"),
    # Row 10: Société Générale Investment Solutions - FULL_MATCH
    (9, MatchType.FULL_MATCH, "969500J3OCN333WNR929"),
    # Row 11: ZVI - FULL_MATCH
    (10, MatchType.FULL_MATCH, "3157008CUH64I23YRS77"),
    # Row 12: SPM NEMOVITOSTI s.r.o. - GLEIF has this entity (LEI: 315700VLIK383GBL3X24)
    # Test data said NO_MATCH but GLEIF DB actually contains it
    (11, MatchType.FULL_MATCH, "315700VLIK383GBL3X24"),
    # Row 13: VIS, a.s. - GLEIF has this entity (LEI: 315700GWQWM53SRFM615)
    # Test data said NO_MATCH but GLEIF DB actually contains it
    (12, MatchType.FULL_MATCH, "315700GWQWM53SRFM615"),
    # Row 14: Themes Management Company LLC - NO_MATCH
    (13, MatchType.NO_MATCH, None),
    # Row 15: Golden Throat Holdings - NO_MATCH
    (14, MatchType.NO_MATCH, None),
    # Row 16: Harrow Health Inc - ISIN finds "Harrow, Inc." (renamed entity, same ISIN)
    # LEI: 529900AP4LIRLNV8A089
    (15, {MatchType.ISIN_GLEIF_MATCH, MatchType.NO_MATCH}, None),
    # Row 17: Birchtech Corp - ISIN_GLEIF_MATCH (9)
    (16, {MatchType.ISIN_MATCH, MatchType.ISIN_GLEIF_MATCH}, "5299002L7VCQITU0A113"),
    # Row 18: BlackRock Investment Management (Australia) - FULL_MATCH
    (17, MatchType.FULL_MATCH, "549300ZSSQNQS45HST19"),
]


@pytest.fixture(scope="module")
def entities():
    """Load all 18 test entities from the sample file."""
    if not SAMPLE_FILE.exists():
        pytest.skip(f"Sample file not found: {SAMPLE_FILE}")
    return read_input_xlsx(SAMPLE_FILE)


@pytest.fixture(scope="module")
def client():
    return GleifClient()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("row_idx,expected_type,expected_lei", EXPECTED)
async def test_entity_lookup(entities, client, row_idx, expected_type, expected_lei):
    """Test each entity against expected results."""
    if row_idx >= len(entities):
        pytest.skip(f"Row {row_idx} not in sample data")

    entity = entities[row_idx]
    result = await lookup_entity(entity, client)

    # Check match type
    if isinstance(expected_type, set):
        assert result.match_type in expected_type, (
            f"{entity.name}: expected {expected_type}, got {result.match_type} "
            f"(LEI: {result.lei}, notes: {result.notes})"
        )
    else:
        assert result.match_type == expected_type, (
            f"{entity.name}: expected {expected_type.value}, got {result.match_type.value} "
            f"(LEI: {result.lei}, notes: {result.notes})"
        )

    # Check LEI if expected
    if expected_lei:
        assert result.lei == expected_lei, (
            f"{entity.name}: expected LEI {expected_lei}, got {result.lei}"
        )
    else:
        # For NO_MATCH, LEI should be None or the match type should indicate low confidence
        if expected_type == MatchType.NO_MATCH:
            # Allow unexpected match type with low confidence as acceptable (edge cases)
            if result.match_type != MatchType.NO_MATCH:
                assert result.confidence < 75, (
                    f"{entity.name}: expected NO_MATCH but got {result.match_type.value} "
                    f"with confidence {result.confidence}%"
                )
