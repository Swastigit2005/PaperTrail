"""Case orchestration: detect -> extract -> validate -> cross-check -> decide.

``evaluate`` runs everything after extraction and is also used by the benchmark's oracle runs,
which feed ground-truth documents straight in.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from pydantic import Field as PField

from papertrail.core.crosscheck import CheckContext, CheckOutcome, run_crosschecks
from papertrail.core.decision import Decision
from papertrail.core.extraction.base import Extractor
from papertrail.core.extraction.detect import Detection, detect_doc_type
from papertrail.core.findings import Finding
from papertrail.core.images import PreparedImage
from papertrail.core.llm import LLMClient
from papertrail.core.pack import Pack


@dataclass
class DocumentInput:
    """One uploaded document. ``doc_type`` is a manual override; ``ocr_text`` is optional."""

    image: PreparedImage
    doc_type: str | None = None
    ocr_text: str | None = None


class DocumentResult(BaseModel):
    """Extraction output for one document."""

    doc_type: str
    title: str
    data: dict[str, Any]
    detection: dict[str, Any]
    pipeline: str
    model: str
    latency_s: float
    usage: dict[str, int]
    cached: bool
    image_sha256: str


class CaseResult(BaseModel):
    """Everything the UI, the JSON export and the benchmark need about one case."""

    case_id: str
    pack: str
    pipeline: str
    matching: str
    documents: dict[str, DocumentResult]
    validations: list[Finding]
    crosschecks: list[CheckOutcome]
    decision: Decision
    latency_s: float
    usage: dict[str, int] = PField(default_factory=dict)
    created_at: str = PField(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))


def evaluate(
    pack: Pack, docs: Mapping[str, BaseModel], ctx: CheckContext
) -> tuple[list[Finding], list[CheckOutcome], Decision]:
    """Validate each document, run cross-checks and decide."""
    validations: list[Finding] = []
    for doc_type, doc in docs.items():
        validations.extend(pack.validate(doc_type, doc, ctx))
    outcomes = run_crosschecks(list(pack.crosschecks()), docs, ctx)
    findings = validations + [f for o in outcomes for f in o.findings]
    return validations, outcomes, pack.decide(findings, docs)


def assign_doc_types(
    pack: Pack,
    inputs: Sequence[DocumentInput],
    *,
    llm: LLMClient | None = None,
    vision_model: str | None = None,
) -> list[Detection]:
    """Resolve each input's document type: manual override first, then auto-detection.

    If two inputs are detected as the same type, the less confident one is given the type that
    is still unassigned (method ``elimination``).
    """
    detections: list[Detection | None] = []
    for doc in inputs:
        if doc.doc_type:
            pack.doc_type(doc.doc_type)  # validates the override
            detections.append(Detection(doc.doc_type, 1.0, "override"))
        else:
            detections.append(None)
    for i, doc in enumerate(inputs):
        if detections[i] is None:
            detections[i] = detect_doc_type(
                doc.image, pack.doc_types, ocr_text=doc.ocr_text, llm=llm, model=vision_model
            )
    resolved: list[Detection] = [d for d in detections if d is not None]
    names = [d.doc_type for d in resolved]
    if len(set(names)) < len(names) and len(inputs) == len(pack.doc_types):
        free = [dt.name for dt in pack.doc_types if dt.name not in names]
        seen: set[str] = set()
        # most confident (and overrides, confidence 1.0) keep their type
        for k in sorted(range(len(resolved)), key=lambda k: -resolved[k].confidence):
            if resolved[k].doc_type in seen and free:
                resolved[k] = Detection(free.pop(0), resolved[k].confidence, "elimination")
            seen.add(resolved[k].doc_type)
    return resolved


def _clients(*candidates: object) -> list[LLMClient]:
    """Distinct LLM clients among the candidates (identity, not equality)."""
    out: list[LLMClient] = []
    for c in candidates:
        if isinstance(c, LLMClient) and all(c is not o for o in out):
            out.append(c)
    return out


def process_case(
    pack: Pack,
    inputs: Sequence[DocumentInput],
    extractor: Extractor,
    ctx: CheckContext,
    *,
    llm: LLMClient | None = None,
    vision_model: str | None = None,
) -> CaseResult:
    """Run one case end to end. Requires exactly one document per pack document type.

    ``usage`` on the result covers every LLM call made for the case: detection, extraction and
    (in rules+llm mode) matching.
    """
    start = time.perf_counter()
    clients = _clients(llm, getattr(extractor, "llm", None), ctx.matcher.llm)
    before = [c.usage.as_dict() for c in clients]
    # OCR pipelines hand their (memoised) OCR text to detection so keywords can decide for free;
    # extraction still receives the original input and reuses the OCR with its word confidences.
    detect_inputs = [
        doc if doc.doc_type or doc.ocr_text else replace(doc, ocr_text=extractor.detection_text(doc.image))
        for doc in inputs
    ]
    detections = assign_doc_types(pack, detect_inputs, llm=llm, vision_model=vision_model)
    found = sorted(d.doc_type for d in detections)
    expected = sorted(dt.name for dt in pack.doc_types)
    if found != expected:
        raise ValueError(f"pack '{pack.name}' needs documents {expected}, got {found}")

    docs: dict[str, BaseModel] = {}
    results: dict[str, DocumentResult] = {}
    images: dict[str, PreparedImage] = {}
    extract_usage: dict[str, int] = {}
    for doc, det in zip(inputs, detections, strict=True):
        dt = pack.doc_type(det.doc_type)
        res = extractor.extract(doc.image, dt, ocr_text=doc.ocr_text)
        docs[dt.name] = res.data
        images[dt.name] = doc.image
        for k, v in res.usage.items():
            extract_usage[k] = extract_usage.get(k, 0) + v
        results[dt.name] = DocumentResult(
            doc_type=dt.name, title=dt.title, data=res.data.model_dump(mode="json"),
            detection={"method": det.method, "confidence": det.confidence},
            pipeline=res.pipeline, model=res.model, latency_s=round(res.latency_s, 3),
            usage=res.usage, cached=res.cached, image_sha256=doc.image.sha256,
        )

    ctx.images = images
    validations, outcomes, decision = evaluate(pack, docs, ctx)
    usage: dict[str, int] = {} if clients else extract_usage
    for client, snap in zip(clients, before, strict=True):
        for k, v in client.usage.as_dict().items():
            usage[k] = usage.get(k, 0) + v - snap[k]
    return CaseResult(
        case_id=ctx.case_id or uuid.uuid4().hex[:8],
        pack=pack.name,
        pipeline=extractor.name,
        matching=ctx.matcher.mode,
        documents=results,
        validations=validations,
        crosschecks=outcomes,
        decision=decision,
        latency_s=round(time.perf_counter() - start, 3),
        usage=usage,
    )
