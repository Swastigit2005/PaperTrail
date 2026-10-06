"""Document-type auto-detection: free keyword scoring on OCR text, vision-LLM fallback.

Callers can always bypass detection with a manual override.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from papertrail.core.images import PreparedImage
from papertrail.core.llm import LLMClient
from papertrail.core.pack import DocType


@dataclass(frozen=True)
class Detection:
    """Detected type, how sure, and which method decided."""

    doc_type: str
    confidence: float
    method: str  # "override" | "keywords" | "llm"


class _Verdict(BaseModel):
    doc_type: str
    confidence: float = 0.5


def keyword_scores(text: str, candidates: Sequence[DocType]) -> dict[str, int]:
    """Count each candidate's keywords present in the text."""
    low = text.lower()
    return {dt.name: sum(1 for kw in dt.keywords if kw.lower() in low) for dt in candidates}


def detect_doc_type(
    image: PreparedImage,
    candidates: Sequence[DocType],
    *,
    ocr_text: str | None = None,
    llm: LLMClient | None = None,
    model: str | None = None,
) -> Detection:
    """Pick which of ``candidates`` the image is.

    Keywords decide when one type leads by at least two hits; otherwise the vision model is asked.
    Raises ValueError when neither method is available or the model answers outside the candidates.
    """
    if ocr_text:
        scores = keyword_scores(ocr_text, candidates)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        best, runner_up = ranked[0], ranked[1] if len(ranked) > 1 else ("", 0)
        if best[1] >= 2 and best[1] - runner_up[1] >= 2:
            return Detection(best[0], min(0.95, 0.6 + 0.1 * (best[1] - runner_up[1])), "keywords")
    if llm is None or model is None:
        raise ValueError("cannot auto-detect document type: no OCR keywords and no vision model")
    options = "\n".join(f"- {dt.name}: {dt.title} ({dt.description})" for dt in candidates)
    verdict, _ = llm.parse(
        _Verdict,
        model=model,
        system=f"Classify the document image as exactly one of:\n{options}\n"
        'Reply as JSON {"doc_type": "<name>", "confidence": <0..1>}.',
        user="Which document type is this?",
        images=[image],
        max_tokens=60,
    )
    names = {dt.name for dt in candidates}
    if verdict.doc_type not in names:
        raise ValueError(f"model classified the document as unknown type {verdict.doc_type!r}")
    return Detection(verdict.doc_type, verdict.confidence, "llm")
