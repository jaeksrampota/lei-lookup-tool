"""FastAPI web application for the LEI Lookup Tool."""

import asyncio
import json
import io
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import pandas as pd

from .address import country_to_iso, _load_country_map
from . import database as db
from .gleif_client import GleifApiError, GleifClient
from .main import lookup_entity
from .models import InputEntity, LookupResult, MatchType
from .upload_parser import parse_upload

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

@asynccontextmanager
async def lifespan(app):
    await db.init_db()
    yield

app = FastAPI(title="LEI Lookup Tool", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _render(request: Request, name: str, context: dict, status_code: int = 200):
    """Render a Jinja2 template, compatible with Starlette 1.0+."""
    ctx = {**context, "request": request}
    return templates.TemplateResponse(request=request, name=name, context=ctx, status_code=status_code)


# ---------------------------------------------------------------------------
# Job state management (SQLite-backed + in-memory SSE queues)
# ---------------------------------------------------------------------------

@dataclass
class Job:
    id: str
    filename: str
    entities: list[InputEntity]
    results: list[LookupResult] = field(default_factory=list)
    status: str = "pending"  # pending, processing, complete, error
    progress: int = 0
    error_message: str = ""
    created_at: float = field(default_factory=time.time)
    queues: list[asyncio.Queue] = field(default_factory=list)


# In-memory dict for SSE queues and active processing (ephemeral)
_active_jobs: dict[str, Job] = {}

def _job_from_db(row: dict) -> Job:
    """Reconstruct a Job from a database row dict."""
    job = Job(
        id=row["id"],
        filename=row["filename"],
        entities=row["entities"],
        results=row["results"],
        status=row["status"],
        progress=row["progress"],
        error_message=row["error_message"],
        created_at=row["created_at"],
    )
    return job


async def _get_job(job_id: str) -> Job:
    """Get job from in-memory cache or database."""
    # Check in-memory first (for active SSE connections)
    if job_id in _active_jobs:
        return _active_jobs[job_id]
    # Fall back to database
    row = await db.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_from_db(row)


async def _save_job_to_db(job: Job):
    """Persist a job to the database."""
    await db.save_job(
        job_id=job.id,
        filename=job.filename,
        entities=job.entities,
        status=job.status,
        results=job.results,
        progress=job.progress,
        error_message=job.error_message,
        created_at=job.created_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return _render(request, "index.html", {})


@app.get("/api/countries")
async def api_countries():
    mapping = _load_country_map()
    # Invert: collect unique (name, code) pairs
    seen_codes: set[str] = set()
    countries = []
    for name, code in sorted(mapping.items(), key=lambda x: x[0]):
        if code not in seen_codes:
            countries.append({"name": name.title(), "code": code})
            seen_codes.add(code)
    return JSONResponse(countries)


@app.post("/lookup", response_class=HTMLResponse)
async def lookup(
    request: Request,
    name: str = Form(...),
    country: str = Form(""),
    isin: str = Form(""),
    street: str = Form(""),
    town: str = Form(""),
    zip_code: str = Form(""),
):
    if not name.strip():
        return _render(request, "index.html", {
            "error": "Entity name is required.",
            "form": {"name": name, "country": country, "isin": isin, "street": street, "town": town, "zip_code": zip_code},
        })

    entity = InputEntity(
        name=name.strip(),
        isin=isin.strip() or None,
        street=street.strip() or None,
        town=town.strip() or None,
        country=country.strip() or None,
        zip_code=zip_code.strip() or None,
    )

    client = GleifClient()
    try:
        result = await lookup_entity(entity, client)
    except GleifApiError:
        logger.exception("GLEIF API unavailable for %s", name)
        return _render(request, "index.html", {
            "error": "GLEIF API is currently unavailable. Please try again later.",
            "form": {"name": name, "country": country, "isin": isin, "street": street, "town": town, "zip_code": zip_code},
        })
    except Exception:
        logger.exception("Lookup failed for %s", name)
        return _render(request, "index.html", {
            "error": "An unexpected error occurred during lookup. Please try again.",
            "form": {"name": name, "country": country, "isin": isin, "street": street, "town": town, "zip_code": zip_code},
        })
    finally:
        await client.close()

    # Store as a single-entity job for download
    job_id = str(uuid.uuid4())
    job = Job(id=job_id, filename=f"lookup_{name[:30]}", entities=[entity], results=[result], status="complete")
    _active_jobs[job_id] = job
    await _save_job_to_db(job)

    return _render(request, "index.html", {
        "result": result,
        "entity": entity,
        "job_id": job_id,
        "form": {"name": name, "country": country, "isin": isin, "street": street, "town": town, "zip_code": zip_code},
    })


@app.post("/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")

    content = await file.read()

    try:
        entities = parse_upload(file.filename, content)
    except ValueError as e:
        return _render(request, "index.html", {
            "upload_error": str(e),
        }, status_code=400)

    job_id = str(uuid.uuid4())
    job = Job(id=job_id, filename=file.filename, entities=entities)
    _active_jobs[job_id] = job
    await _save_job_to_db(job)

    # Start background processing
    asyncio.create_task(_process_job(job))

    return RedirectResponse(url=f"/results/{job_id}", status_code=303)


async def _process_job(job: Job):
    """Process all entities in a job, pushing SSE events."""
    job.status = "processing"
    await db.update_job_status(job.id, "processing")
    client = GleifClient()

    try:
        for i, entity in enumerate(job.entities):
            job.progress = i
            event = {
                "type": "progress",
                "current": i + 1,
                "total": len(job.entities),
                "entity_name": entity.name,
            }
            _broadcast(job, event)

            try:
                result = await lookup_entity(entity, client)
            except GleifApiError:
                logger.exception("GLEIF API error for entity %s", entity.name)
                result = LookupResult(
                    match_type=MatchType.NO_MATCH,
                    notes="Chyba při komunikaci s GLEIF API: služba nedostupná.",
                )
            except Exception:
                logger.exception("Error processing entity %s", entity.name)
                result = LookupResult(
                    match_type=MatchType.NO_MATCH,
                    notes="Neočekávaná chyba při vyhledávání.",
                )

            job.results.append(result)
            await db.append_result(job.id, result, i + 1)

            event = {
                "type": "result",
                "index": i,
                "current": i + 1,
                "total": len(job.entities),
                "entity_name": entity.name,
                "lei": result.lei or "",
                "match_type": result.match_type.value,
                "confidence": round(result.confidence, 1),
                "notes": result.notes,
            }
            _broadcast(job, event)

        job.status = "complete"
        job.progress = len(job.entities)
        await db.update_job_status(job.id, "complete", progress=len(job.entities))
        _broadcast(job, {"type": "complete", "total": len(job.entities)})

    except Exception as e:
        job.status = "error"
        job.error_message = str(e)
        await db.update_job_status(job.id, "error", error_message=str(e))
        _broadcast(job, {"type": "error", "message": str(e)})
    finally:
        await client.close()
        # Keep completed jobs in memory for 5 minutes so late SSE clients can reconnect
        asyncio.get_event_loop().call_later(300, lambda: _active_jobs.pop(job.id, None))


def _broadcast(job: Job, event: dict):
    for q in job.queues:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


@app.get("/progress/{job_id}")
async def progress(job_id: str):
    job = await _get_job(job_id)

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        job.queues.append(queue)

        # Send any already-completed results as replay
        for i, result in enumerate(job.results):
            entity = job.entities[i]
            event = {
                "type": "result",
                "index": i,
                "current": i + 1,
                "total": len(job.entities),
                "entity_name": entity.name,
                "lei": result.lei or "",
                "match_type": result.match_type.value,
                "confidence": round(result.confidence, 1),
                "notes": result.notes,
            }
            yield f"data: {json.dumps(event)}\n\n"

        if job.status == "complete":
            yield f"data: {json.dumps({'type': 'complete', 'total': len(job.entities)})}\n\n"
            return
        if job.status == "error":
            yield f"data: {json.dumps({'type': 'error', 'message': job.error_message})}\n\n"
            return

        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("type") in ("complete", "error"):
                        break
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        finally:
            if queue in job.queues:
                job.queues.remove(queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/results/{job_id}", response_class=HTMLResponse)
async def results(request: Request, job_id: str):
    job = await _get_job(job_id)

    paired = list(zip(job.entities, job.results)) if job.results else []

    match_counts: dict[str, int] = {}
    for r in job.results:
        mt = r.match_type.value
        match_counts[mt] = match_counts.get(mt, 0) + 1

    return _render(request, "results.html", {
        "job": job,
        "paired": paired,
        "match_counts": match_counts,
    })


@app.get("/download/{job_id}")
async def download(job_id: str, format: str = "xlsx"):
    job = await _get_job(job_id)

    if not job.results:
        raise HTTPException(status_code=400, detail="No results available yet.")

    rows = []
    for entity, result in zip(job.entities, job.results):
        rows.append({
            "Name": entity.name,
            "ISIN": entity.isin or "",
            "Street": entity.street or "",
            "Town": entity.town or "",
            "Country": entity.country or "",
            "ZIP code": entity.zip_code or "",
            "LEI": result.lei or "",
            "LEI_status": result.lei_status or "",
            "Match_type": result.match_type.value if result.match_type else "",
            "Confidence": round(result.confidence, 1),
            "GLEIF_legal_name": result.gleif_legal_name or "",
            "GLEIF_legal_address": result.gleif_legal_address or "",
            "GLEIF_hq_address": result.gleif_hq_address or "",
            "Notes": result.notes or "",
        })

    df = pd.DataFrame(rows)
    safe_name = "".join(c for c in job.filename if c.isalnum() or c in "._- ")[:50] or "results"

    if format == "csv":
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        content = buf.getvalue().encode("utf-8-sig")
        return StreamingResponse(
            io.BytesIO(content),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.csv"'},
        )
    elif format == "json":
        data = []
        for entity, result in zip(job.entities, job.results):
            data.append({
                "entity": entity.model_dump(),
                "result": result.model_dump(),
            })
        content = json.dumps(data, indent=2, ensure_ascii=False, default=str).encode("utf-8")
        return StreamingResponse(
            io.BytesIO(content),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.json"'},
        )
    else:  # xlsx
        buf = io.BytesIO()
        df.to_excel(buf, index=False, engine="openpyxl")
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.xlsx"'},
        )


@app.get("/history", response_class=HTMLResponse)
async def history(request: Request):
    job_rows = await db.get_all_jobs(limit=50)
    sorted_jobs = [_job_from_db(r) for r in job_rows]
    return _render(request, "history.html", {
        "jobs": sorted_jobs,
    })
