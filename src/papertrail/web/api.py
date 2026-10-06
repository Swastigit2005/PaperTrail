"""HTTP API for the live demo (FastAPI). Deployed as one Vercel Function; also runs under uvicorn.

Endpoints
- ``GET  /api/health``    configuration status (never exposes secrets)
- ``GET  /api/packs``     packs, document types and checks (for the UI and "How it works")
- ``POST /api/extract``   one document image -> detected type + field-level extraction
- ``POST /api/evaluate``  extracted documents -> validations, cross-checks, decision

Free-tier guards: 5 MB uploads, at most two extractions at a time per instance, a per-IP hourly cap,
the response cache keyed on image bytes, and Groq 429s turned into a friendly "demo busy" reply.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from pydantic import Field as PField

from papertrail.config import ConfigError, Settings, get_settings
from papertrail.core.crosscheck import CheckContext
from papertrail.core.engine import evaluate
from papertrail.core.extraction import OcrThenLLMExtractor, VLMDirectExtractor
from papertrail.core.extraction.detect import detect_doc_type
from papertrail.core.images import load_document
from papertrail.core.llm import LLMClient, LLMError, RateLimited
from papertrail.core.matching import Matcher
from papertrail.core.pack import get_pack, list_packs
from papertrail.core.validators.duplicates import DuplicateIndex
from papertrail.core.validators.rxnorm import RxNormClient

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_TYPES = ("image/jpeg", "image/png", "image/webp", "application/pdf")
CONCURRENCY = 2
IP_CAP_PER_HOUR = int(os.getenv("PAPERTRAIL_IP_CAP", "40"))
BUSY_MESSAGE = "The demo is busy (free-tier rate limit). Please try again in a minute."

app = FastAPI(title="PaperTrail", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")

_slots = threading.BoundedSemaphore(CONCURRENCY)
_ip_hits: dict[str, deque[float]] = defaultdict(deque)
_ip_lock = threading.Lock()
_state: dict[str, Any] = {}


def _settings() -> Settings:
    if "settings" not in _state:
        if os.getenv("VERCEL") and not os.getenv("PAPERTRAIL_CACHE_DIR"):
            os.environ["PAPERTRAIL_CACHE_DIR"] = "/tmp/papertrail-cache"  # the only writable path on Vercel
        os.environ.setdefault("PAPERTRAIL_MAX_WAIT", "20")  # never hold a web request for minutes
        _state["settings"] = get_settings()
    return _state["settings"]


def _llm() -> LLMClient:
    if "llm" not in _state:
        try:
            _state["llm"] = LLMClient(_settings(), max_retries=2)
        except ConfigError as exc:
            raise HTTPException(503, f"server not configured: {exc}") from exc
    return _state["llm"]


def _rxnorm() -> RxNormClient:
    if "rxnorm" not in _state:
        _state["rxnorm"] = RxNormClient(cache_dir=_settings().cache_dir, timeout=6.0, retries=1)
    return _state["rxnorm"]


def _check_ip(request: Request) -> None:
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0]
    now = time.time()
    with _ip_lock:
        hits = _ip_hits[ip]
        while hits and now - hits[0] > 3600:
            hits.popleft()
        if len(hits) >= IP_CAP_PER_HOUR:
            raise HTTPException(429, "Hourly demo limit reached for your network. The sample pairs still work.")
        hits.append(now)


@app.exception_handler(RateLimited)
async def _busy(_: Request, exc: RateLimited) -> JSONResponse:
    return JSONResponse(status_code=429, content={"error": "demo_busy", "message": BUSY_MESSAGE,
                                                  "retry_after": round(min(exc.retry_after, 120))})


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Configuration status; model names are public config, keys are never returned."""
    s = _settings()
    return {"ok": True, "groq_configured": bool(s.groq_api_key), "vision_model": s.vision_model,
            "text_model": s.text_model, "match_model": s.match_model, "server_ocr": _has_tesseract()}


@app.get("/api/packs")
def packs() -> list[dict[str, Any]]:
    """Every registered pack with its document types and checks."""
    return [
        {
            "name": p.name, "title": p.title, "description": p.description, "benchmarked": p.benchmarked,
            "doc_types": [{"name": d.name, "title": d.title, "description": d.description} for d in p.doc_types],
            "checks": [{"code": c.code, "description": c.description} for c in p.crosschecks()],
        }
        for p in list_packs()
    ]


@app.get("/api/benchmark", response_class=PlainTextResponse)
def benchmark_markdown() -> str:
    """BENCHMARK.md straight from bench/results (fallback when the static copy is missing)."""
    path = Path(__file__).resolve().parents[3] / "bench" / "results" / "BENCHMARK.md"
    if not path.exists():
        raise HTTPException(404, "benchmark report not built yet")
    return path.read_text()


def _has_tesseract() -> bool:
    if "tesseract" not in _state:
        try:
            import shutil

            import pytesseract  # noqa: F401

            _state["tesseract"] = shutil.which("tesseract") is not None
        except ImportError:
            _state["tesseract"] = False
    return _state["tesseract"]


@app.post("/api/extract")
def extract(
    request: Request,
    file: Annotated[UploadFile, File()],
    pack: Annotated[str, Form()],
    doc_type: Annotated[str, Form()] = "auto",
    pipeline: Annotated[Literal["vlm", "ocr+llm"], Form()] = "vlm",
    ocr_text: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """Extract one document. ``ocr_text`` (from in-browser OCR) is required for ocr+llm without server OCR."""
    try:
        the_pack = get_pack(pack)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(415, "Upload a JPG, PNG, WEBP image or a PDF (first page is used).")
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large: the limit is 5 MB.")
    if pipeline == "ocr+llm" and not ocr_text and not _has_tesseract():
        raise HTTPException(400, "OCR text missing: the OCR pipeline runs Tesseract in your browser first.")
    _check_ip(request)
    s, llm = _settings(), _llm()
    try:
        image = load_document(data, name=file.filename or "upload", max_side=s.max_image_side)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if not _slots.acquire(timeout=25):
        raise RateLimited("all demo slots busy", retry_after=30)
    try:
        if pipeline == "vlm":
            extractor: Any = VLMDirectExtractor(llm, s.require("vision_model"))
        else:
            extractor = OcrThenLLMExtractor(llm, s.require("text_model"))
        if doc_type == "auto":
            text = ocr_text or (extractor.detection_text(image) if pipeline == "ocr+llm" else None)
            det = detect_doc_type(image, the_pack.doc_types, ocr_text=text, llm=llm, model=s.vision_model)
            detection = {"method": det.method, "confidence": det.confidence}
            dt = the_pack.doc_type(det.doc_type)
        else:
            dt = the_pack.doc_type(doc_type)
            detection = {"method": "override", "confidence": 1.0}
        result = extractor.extract(image, dt, ocr_text=ocr_text)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except LLMError as exc:
        if isinstance(exc, RateLimited):
            raise
        raise HTTPException(502, f"model call failed: {exc}") from exc
    finally:
        _slots.release()
    return {
        "doc_type": dt.name, "title": dt.title, "detection": detection,
        "data": result.data.model_dump(mode="json"), "pipeline": result.pipeline, "model": result.model,
        "latency_s": round(result.latency_s, 2), "usage": result.usage, "cached": result.cached,
        "image": {"sha256": image.sha256, "dhash": f"{image.dhash:016x}"}, "ocr_text": result.ocr_text,
    }


class HistoryEntry(BaseModel):
    """A document seen earlier in the browser session (for duplicate detection)."""

    case_id: str
    dhash: str | None = None
    fingerprint: str | None = None


class EvaluateRequest(BaseModel):
    """Extracted documents to validate, cross-check and decide."""

    pack: str
    case_id: str = "case"
    matching: Literal["rules", "rules+llm"] = "rules+llm"
    documents: dict[str, dict[str, Any]]
    images: dict[str, dict[str, str]] = PField(default_factory=dict)
    history: list[HistoryEntry] = PField(default_factory=list)


@app.post("/api/evaluate")
def evaluate_case(req: EvaluateRequest) -> dict[str, Any]:
    """Run per-document validators, cross-checks and the decision on extracted documents."""
    try:
        pack = get_pack(req.pack)
        docs = {dt: pack.doc_type(dt).schema.model_validate(data) for dt, data in req.documents.items()}
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    missing = sorted({d.name for d in pack.doc_types} - set(docs))
    if missing:
        raise HTTPException(400, f"pack '{pack.name}' also needs: {', '.join(missing)}")
    history = DuplicateIndex()
    for h in req.history:
        history.add(h.case_id, int(h.dhash, 16) if h.dhash else None, h.fingerprint)
    images = {dt: SimpleNamespace(dhash=int(v["dhash"], 16)) for dt, v in req.images.items() if v.get("dhash")}
    notes: list[str] = []

    def run(mode: str) -> tuple:
        llm = _llm() if mode == "rules+llm" else None
        matcher = Matcher(mode=mode, llm=llm, model=_settings().match_model)
        services = {"rxnorm": _rxnorm()} if pack.name == "claims" else {}
        ctx = CheckContext(matcher=matcher, case_id=req.case_id, today=date.today(), images=images,  # type: ignore[arg-type]
                           history=history, services=services)
        return evaluate(pack, docs, ctx)

    try:
        validations, outcomes, decision = run(req.matching)
    except RateLimited:
        notes.append("LLM matching is busy right now; this result used deterministic rules only.")
        validations, outcomes, decision = run("rules")
    new_entries = [{"case_id": c, "dhash": f"{h:016x}" if h is not None else None, "fingerprint": f}
                   for c, h, f in history._entries[len(req.history):]]
    return {
        "validations": [f.model_dump(mode="json") for f in validations],
        "crosschecks": [o.model_dump(mode="json") for o in outcomes],
        "decision": decision.model_dump(mode="json"),
        "matching": req.matching if not notes else "rules", "notes": notes, "history_add": new_entries,
    }


def mount_static(root: Path) -> None:
    """Serve the frontend when not on Vercel (Vercel serves public/ from its CDN)."""
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=root, html=True), name="public")
