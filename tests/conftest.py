"""Shared test fixtures for the LEI Lookup Tool."""

import asyncio
import threading
import time
import socket
from unittest.mock import AsyncMock, patch
from pathlib import Path

import pytest

from src.models import InputEntity, LookupResult, MatchType
from src import database as db

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Test DB path used by live server and shared tests
_TEST_DB = Path(__file__).parent / "_test_conftest.db"


@pytest.fixture
def sample_csv():
    return FIXTURES_DIR / "sample.csv"


@pytest.fixture
def sample_xlsx():
    return FIXTURES_DIR / "sample.xlsx"


@pytest.fixture
def sample_docx():
    return FIXTURES_DIR / "sample.docx"


@pytest.fixture
def empty_csv():
    return FIXTURES_DIR / "empty.csv"


@pytest.fixture
def sample_semicolon_csv():
    return FIXTURES_DIR / "sample_semicolon.csv"


def _mock_lookup_result(entity_name: str) -> LookupResult:
    """Return a fake LookupResult based on entity name."""
    if "CAIAC" in entity_name:
        return LookupResult(
            lei="529900PY3KLUDU87D755",
            lei_status="ISSUED",
            match_type=MatchType.FULL_MATCH,
            confidence=92.5,
            gleif_legal_name="CAIAC Fund Management AG",
            gleif_legal_address="Aeulestrasse 5, Vaduz, 9490, LI",
            notes="Plná shoda názvu a legal address.",
        )
    elif "Polar" in entity_name:
        return LookupResult(
            lei="4YW3JKTZ3K1II2GVCK15",
            lei_status="ISSUED",
            match_type=MatchType.FULL_MATCH,
            confidence=89.0,
            gleif_legal_name="Polar Capital LLP",
            gleif_legal_address="16 Palace Street, London, SW1E 5JD, GB",
            notes="Plná shoda názvu a legal address.",
        )
    else:
        return LookupResult(
            match_type=MatchType.NO_MATCH,
            confidence=0.0,
            notes="Žádný LEI nalezen v GLEIF databázi.",
        )


@pytest.fixture
def mock_lookup():
    """Patch lookup_entity to return mock results."""
    async def _mock(entity, client):
        return _mock_lookup_result(entity.name)

    with patch("src.app.lookup_entity", side_effect=_mock) as m:
        yield m


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server_port():
    return _find_free_port()


@pytest.fixture(scope="session")
def live_server_url(live_server_port):
    return f"http://127.0.0.1:{live_server_port}"


@pytest.fixture(scope="session")
def _live_server(live_server_port):
    """Start a live FastAPI server in a background thread with mocked lookup."""
    import uvicorn

    # Use a dedicated test DB for the live server
    live_db = Path(__file__).parent / "_test_live_server.db"
    db.DB_PATH = live_db
    db.init_db_sync()

    async def _fake_lookup(entity, client):
        return _mock_lookup_result(entity.name)

    with patch("src.app.lookup_entity", side_effect=_fake_lookup):
        config = uvicorn.Config(
            "src.app:app",
            host="127.0.0.1",
            port=live_server_port,
            log_level="error",
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        # Wait for server to be ready
        for _ in range(50):
            try:
                import httpx
                httpx.get(f"http://127.0.0.1:{live_server_port}/", timeout=1.0)
                break
            except Exception:
                time.sleep(0.1)

        yield

        server.should_exit = True
        thread.join(timeout=5)

        # Cleanup
        if live_db.exists():
            live_db.unlink()
