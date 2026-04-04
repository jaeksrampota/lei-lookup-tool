"""Tests for the FastAPI web application endpoints."""

import io
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import httpx

from src.app import app, _active_jobs, Job
from src.models import InputEntity, LookupResult, MatchType
from src import database as db

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Use in-memory-like temp DB for tests
_TEST_DB = Path(__file__).parent / "_test_app.db"


def _mock_result(name: str = "Test") -> LookupResult:
    if "CAIAC" in name:
        return LookupResult(
            lei="529900PY3KLUDU87D755",
            lei_status="ISSUED",
            match_type=MatchType.FULL_MATCH,
            confidence=92.5,
            gleif_legal_name="CAIAC Fund Management AG",
            gleif_legal_address="Aeulestrasse 5, Vaduz, 9490, LI",
            notes="Plná shoda názvu a legal address.",
        )
    return LookupResult(
        match_type=MatchType.NO_MATCH,
        confidence=0.0,
        notes="Žádný LEI nalezen v GLEIF databázi.",
    )


@pytest.fixture(autouse=True)
async def setup_test_db():
    """Use a temporary test database and clear state between tests."""
    db.DB_PATH = _TEST_DB
    db.init_db_sync()
    _active_jobs.clear()
    await db.delete_all_jobs()
    yield
    _active_jobs.clear()
    await db.delete_all_jobs()


@pytest.fixture
def mock_lookup():
    async def _mock(entity, client):
        return _mock_result(entity.name)
    with patch("src.app.lookup_entity", side_effect=_mock) as m:
        yield m


# Use httpx.AsyncClient with ASGITransport for async testing
@pytest.fixture
async def client(mock_lookup):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
class TestHomePage:
    async def test_home_page_renders(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "LEI Lookup Tool" in resp.text
        assert 'id="lookup-form"' in resp.text
        assert 'id="drop-zone"' in resp.text


@pytest.mark.asyncio
class TestLookup:
    async def test_lookup_returns_result(self, client):
        resp = await client.post("/lookup", data={"name": "CAIAC Fund Management AG"})
        assert resp.status_code == 200
        assert "529900PY3KLUDU87D755" in resp.text
        assert "FULL_MATCH" in resp.text

    async def test_lookup_missing_name(self, client):
        resp = await client.post("/lookup", data={})
        # FastAPI returns 422 when required Form field is missing
        assert resp.status_code == 422

    async def test_lookup_with_all_fields(self, client):
        resp = await client.post("/lookup", data={
            "name": "CAIAC Fund Management AG",
            "country": "Lichtenštejnsko",
            "isin": "TEST123",
            "street": "Aeulestrasse 5",
            "town": "Vaduz",
            "zip_code": "9490",
        })
        assert resp.status_code == 200
        assert "529900PY3KLUDU87D755" in resp.text

    async def test_lookup_no_match(self, client):
        resp = await client.post("/lookup", data={"name": "Nonexistent Entity XYZ"})
        assert resp.status_code == 200
        assert "NO_MATCH" in resp.text

    async def test_lookup_creates_job_for_download(self, client):
        resp = await client.post("/lookup", data={"name": "CAIAC Fund Management AG"})
        assert resp.status_code == 200
        # Job should be persisted in DB
        job_count = await db.count_jobs()
        assert job_count == 1

    async def test_lookup_persists_across_restart(self, client):
        """Verify that looked-up results survive a simulated restart (DB persistence)."""
        resp = await client.post("/lookup", data={"name": "CAIAC Fund Management AG"})
        assert resp.status_code == 200
        # Clear in-memory cache to simulate restart
        _active_jobs.clear()
        # Job should still be retrievable from DB
        job_count = await db.count_jobs()
        assert job_count == 1
        all_jobs = await db.get_all_jobs()
        assert all_jobs[0]["results"][0].lei == "529900PY3KLUDU87D755"


@pytest.mark.asyncio
class TestUpload:
    async def test_upload_xlsx(self, client):
        xlsx_path = FIXTURES_DIR / "sample.xlsx"
        if not xlsx_path.exists():
            pytest.skip("sample.xlsx not found")
        content = xlsx_path.read_bytes()
        resp = await client.post(
            "/upload",
            files={"file": ("test.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/results/" in resp.headers["location"]

    async def test_upload_csv(self, client):
        csv_path = FIXTURES_DIR / "sample.csv"
        if not csv_path.exists():
            pytest.skip("sample.csv not found")
        content = csv_path.read_bytes()
        resp = await client.post(
            "/upload",
            files={"file": ("test.csv", content, "text/csv")},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    async def test_upload_docx(self, client):
        docx_path = FIXTURES_DIR / "sample.docx"
        if not docx_path.exists():
            pytest.skip("sample.docx not found")
        content = docx_path.read_bytes()
        resp = await client.post(
            "/upload",
            files={"file": ("test.docx", content, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    async def test_upload_unsupported_format(self, client):
        resp = await client.post(
            "/upload",
            files={"file": ("test.txt", b"hello world", "text/plain")},
        )
        assert resp.status_code == 400

    async def test_upload_empty_file(self, client):
        resp = await client.post(
            "/upload",
            files={"file": ("test.csv", b"", "text/csv")},
        )
        assert resp.status_code == 400


@pytest.mark.asyncio
class TestDownload:
    def _create_complete_job(self) -> str:
        import time
        entity = InputEntity(name="Test Corp")
        result = LookupResult(
            lei="TESTLEI12345",
            lei_status="ISSUED",
            match_type=MatchType.FULL_MATCH,
            confidence=90.0,
            notes="Test note",
        )
        job = Job(id="test-job-1", filename="test", entities=[entity], results=[result], status="complete")
        _active_jobs["test-job-1"] = job
        # Also persist to DB synchronously for immediate availability
        import sqlite3
        import json as _json
        with sqlite3.connect(str(db.DB_PATH)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job.id, job.filename,
                    _json.dumps([e.model_dump() for e in job.entities]),
                    _json.dumps([r.model_dump() for r in job.results], default=str),
                    job.status, job.progress, job.error_message, job.created_at,
                ),
            )
            conn.commit()
        return "test-job-1"

    async def test_download_xlsx(self, client):
        job_id = self._create_complete_job()
        resp = await client.get(f"/download/{job_id}?format=xlsx")
        assert resp.status_code == 200
        assert "spreadsheetml" in resp.headers["content-type"]
        assert len(resp.content) > 0

    async def test_download_csv(self, client):
        job_id = self._create_complete_job()
        resp = await client.get(f"/download/{job_id}?format=csv")
        assert resp.status_code == 200
        assert "csv" in resp.headers["content-type"]
        text = resp.content.decode("utf-8-sig")
        assert "Name" in text
        assert "TESTLEI12345" in text

    async def test_download_json(self, client):
        job_id = self._create_complete_job()
        resp = await client.get(f"/download/{job_id}?format=json")
        assert resp.status_code == 200
        assert "json" in resp.headers["content-type"]
        data = json.loads(resp.content)
        assert len(data) == 1
        assert data[0]["result"]["lei"] == "TESTLEI12345"

    async def test_download_nonexistent_job(self, client):
        resp = await client.get("/download/nonexistent-id?format=xlsx")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestOther:
    async def test_countries_endpoint(self, client):
        resp = await client.get("/api/countries")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 50
        assert all("name" in c and "code" in c for c in data)

    async def test_history_page(self, client):
        resp = await client.get("/history")
        assert resp.status_code == 200
        assert "History" in resp.text

    async def test_results_page_with_job(self, client):
        import time as _time
        import sqlite3
        import json as _json
        entity = InputEntity(name="Test Corp")
        result = LookupResult(match_type=MatchType.NO_MATCH, notes="Test")
        job = Job(id="test-res-1", filename="test.csv", entities=[entity], results=[result], status="complete")
        _active_jobs["test-res-1"] = job
        with sqlite3.connect(str(db.DB_PATH)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job.id, job.filename,
                    _json.dumps([e.model_dump() for e in job.entities]),
                    _json.dumps([r.model_dump() for r in job.results], default=str),
                    job.status, job.progress, job.error_message, job.created_at,
                ),
            )
            conn.commit()
        resp = await client.get("/results/test-res-1")
        assert resp.status_code == 200
        assert "Test Corp" in resp.text
