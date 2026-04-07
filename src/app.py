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
TRANSLATIONS_DIR = BASE_DIR / "translations"

# ---------------------------------------------------------------------------
# i18n — lightweight JSON-based translations
# ---------------------------------------------------------------------------
TRANSLATIONS: dict[str, dict[str, str]] = {}

def _load_translations():
    for lang_file in TRANSLATIONS_DIR.glob("*.json"):
        lang_code = lang_file.stem  # e.g. "cs", "en"
        with open(lang_file, encoding="utf-8") as f:
            TRANSLATIONS[lang_code] = json.load(f)

_load_translations()

def _get_lang(request: Request) -> str:
    return request.cookies.get("lang", "cs")

def _t(key: str, lang: str) -> str:
    return TRANSLATIONS.get(lang, {}).get(key, key)


@asynccontextmanager
async def lifespan(app):
    await db.init_db()
    yield

app = FastAPI(title="LEI Lookup Tool", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _render(request: Request, name: str, context: dict, status_code: int = 200):
    """Render a Jinja2 template, compatible with Starlette 1.0+."""
    lang = _get_lang(request)
    ctx = {
        **context,
        "request": request,
        "lang": lang,
        "t": lambda key: _t(key, lang),
        "translations_json": json.dumps(TRANSLATIONS.get(lang, {}), ensure_ascii=False),
    }
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

@app.get("/set-language")
async def set_language(request: Request, lang: str = "cs"):
    lang = lang if lang in TRANSLATIONS else "cs"
    referer = request.headers.get("referer", "/")
    response = RedirectResponse(url=referer, status_code=303)
    response.set_cookie("lang", lang, max_age=365 * 24 * 3600, samesite="lax")
    return response


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
        lang = _get_lang(request)
        return _render(request, "index.html", {
            "error": _t("err_name_required", lang),
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
        lang = _get_lang(request)
        return _render(request, "index.html", {
            "error": _t("err_gleif_unavailable", lang),
            "form": {"name": name, "country": country, "isin": isin, "street": street, "town": town, "zip_code": zip_code},
        })
    except Exception:
        logger.exception("Lookup failed for %s", name)
        lang = _get_lang(request)
        return _render(request, "index.html", {
            "error": _t("err_unexpected", lang),
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

    # Duplicate detection: cache results by (name, isin) to avoid redundant API calls
    seen: dict[tuple, tuple[int, LookupResult]] = {}

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

            dedup_key = (entity.name.lower().strip(), entity.isin or "")
            if dedup_key in seen:
                orig_idx, orig_result = seen[dedup_key]
                result = orig_result.model_copy(update={
                    "notes": f"Duplicitní záznam — výsledek převzat z řádku {orig_idx + 1}.",
                })
            else:
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
                seen[dedup_key] = (i, result)

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


@app.post("/paste")
async def paste(request: Request, paste_text: str = Form(...)):
    """Create a batch job from pasted text (one entity per line, tab-separated columns)."""
    lines = [l.strip() for l in paste_text.strip().splitlines() if l.strip()]
    if not lines:
        lang = _get_lang(request)
        return _render(request, "index.html", {"upload_error": _t("err_no_entities_pasted", lang)}, status_code=400)

    entities: list[InputEntity] = []
    for line in lines:
        parts = line.split("\t")
        name = parts[0].strip()
        if not name:
            continue
        entities.append(InputEntity(
            name=name,
            isin=parts[1].strip() or None if len(parts) > 1 else None,
            street=parts[2].strip() or None if len(parts) > 2 else None,
            town=parts[3].strip() or None if len(parts) > 3 else None,
            country=parts[4].strip() or None if len(parts) > 4 else None,
            zip_code=parts[5].strip() or None if len(parts) > 5 else None,
        ))

    if not entities:
        lang = _get_lang(request)
        return _render(request, "index.html", {"upload_error": _t("err_no_valid_entities", lang)}, status_code=400)

    job_id = str(uuid.uuid4())
    job = Job(id=job_id, filename=f"paste_{len(entities)}_entities", entities=entities)
    _active_jobs[job_id] = job
    await _save_job_to_db(job)
    asyncio.create_task(_process_job(job))

    return RedirectResponse(url=f"/results/{job_id}", status_code=303)


@app.get("/download/{job_id}")
async def download(job_id: str, format: str = "xlsx", filter: str = ""):
    job = await _get_job(job_id)

    if not job.results:
        raise HTTPException(status_code=400, detail="No results available yet.")

    rows = []
    for entity, result in zip(job.entities, job.results):
        if filter == "unmatched" and result.match_type != MatchType.NO_MATCH:
            continue
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


# ---------------------------------------------------------------------------
# JSON API endpoints
# ---------------------------------------------------------------------------

@app.post("/api/lookup")
async def api_lookup(request: Request):
    """Single entity lookup returning JSON."""
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)

    entity = InputEntity(
        name=name,
        isin=(body.get("isin") or "").strip() or None,
        street=(body.get("street") or "").strip() or None,
        town=(body.get("town") or "").strip() or None,
        country=(body.get("country") or "").strip() or None,
        zip_code=(body.get("zip_code") or "").strip() or None,
    )

    client = GleifClient()
    try:
        result = await lookup_entity(entity, client)
    except GleifApiError:
        return JSONResponse({"error": "GLEIF API unavailable"}, status_code=503)
    except Exception:
        logger.exception("API lookup failed for %s", name)
        return JSONResponse({"error": "Internal error"}, status_code=500)
    finally:
        await client.close()

    return JSONResponse({
        "entity": entity.model_dump(),
        "result": result.model_dump(),
    })


@app.post("/api/batch")
async def api_batch(request: Request):
    """Batch lookup from JSON array, returns job ID for polling."""
    body = await request.json()
    items = body if isinstance(body, list) else body.get("entities", [])
    if not items:
        return JSONResponse({"error": "Provide a list of entities"}, status_code=400)

    entities: list[InputEntity] = []
    for item in items:
        name = (item.get("name") or "").strip()
        if not name:
            continue
        entities.append(InputEntity(
            name=name,
            isin=(item.get("isin") or "").strip() or None,
            street=(item.get("street") or "").strip() or None,
            town=(item.get("town") or "").strip() or None,
            country=(item.get("country") or "").strip() or None,
            zip_code=(item.get("zip_code") or "").strip() or None,
        ))

    if not entities:
        return JSONResponse({"error": "No valid entities found"}, status_code=400)

    job_id = str(uuid.uuid4())
    job = Job(id=job_id, filename=f"api_batch_{len(entities)}", entities=entities)
    _active_jobs[job_id] = job
    await _save_job_to_db(job)
    asyncio.create_task(_process_job(job))

    return JSONResponse({
        "job_id": job_id,
        "total": len(entities),
        "status_url": f"/api/job/{job_id}",
        "progress_url": f"/progress/{job_id}",
    })


@app.get("/api/job/{job_id}")
async def api_job_status(job_id: str):
    """Get job status and results as JSON."""
    try:
        job = await _get_job(job_id)
    except HTTPException:
        return JSONResponse({"error": "Job not found"}, status_code=404)

    results_out = []
    for i, result in enumerate(job.results):
        entity = job.entities[i] if i < len(job.entities) else None
        results_out.append({
            "entity": entity.model_dump() if entity else None,
            "result": result.model_dump(),
        })

    return JSONResponse({
        "job_id": job.id,
        "status": job.status,
        "progress": job.progress,
        "total": len(job.entities),
        "results": results_out,
    })
