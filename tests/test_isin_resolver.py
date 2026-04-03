"""Tests for ISIN resolver logic."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock

from src.isin_resolver import resolve_via_isin
from src.models import InputEntity, GleifCandidate, GleifAddress, MatchType


@pytest.fixture
def mock_client():
    client = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_no_isin(mock_client):
    entity = InputEntity(name="Test")
    result = await resolve_via_isin(entity, mock_client)
    assert result is None


@pytest.mark.asyncio
async def test_isin_gleif_match_single_result(mock_client):
    entity = InputEntity(name="Birchtech Corp", isin="US59833H2004")
    candidate = GleifCandidate(
        lei="5299002L7VCQITU0A113",
        legal_name="Birchtech Corp",
        status="ISSUED",
        legal_address=GleifAddress(country="US", city="Worthington"),
        hq_address=GleifAddress(country="US", city="Worthington"),
    )
    mock_client.search_by_isin.return_value = [candidate]

    result = await resolve_via_isin(entity, mock_client)
    assert result is not None
    assert result.match_type == MatchType.ISIN_GLEIF_MATCH
    assert result.lei == "5299002L7VCQITU0A113"


@pytest.mark.asyncio
async def test_isin_with_hq_candidate(mock_client):
    entity = InputEntity(
        name="Morgan Stanley Direct Lending Fund",
        isin="US61774A1034",
        street="1585 Broadway",
        town="New York",
        country="USA",
    )
    hq_candidate = GleifCandidate(
        lei="549300QEX22T2J8IB029",
        legal_name="Morgan Stanley Direct Lending Fund",
        status="ISSUED",
        legal_address=GleifAddress(country="DE", city="Frankfurt"),
        hq_address=GleifAddress(
            country="US", city="New York",
            address_lines=["1585 Broadway"],
        ),
    )

    # ISIN search returns same entity
    mock_client.search_by_isin.return_value = [
        GleifCandidate(
            lei="549300QEX22T2J8IB029",
            legal_name="Morgan Stanley Direct Lending Fund",
            status="ISSUED",
        )
    ]

    result = await resolve_via_isin(entity, mock_client, hq_candidate=hq_candidate)
    assert result is not None
    assert result.lei == "549300QEX22T2J8IB029"
    assert result.match_type in (MatchType.ISIN_MATCH, MatchType.ISIN_GLEIF_MATCH)


@pytest.mark.asyncio
async def test_isin_no_results(mock_client):
    entity = InputEntity(name="Unknown Corp", isin="XX0000000000")
    mock_client.search_by_isin.return_value = []

    result = await resolve_via_isin(entity, mock_client)
    assert result is None
