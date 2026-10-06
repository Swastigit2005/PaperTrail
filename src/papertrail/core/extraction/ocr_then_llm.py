"""Pipeline 2: image -> Tesseract OCR -> text LLM -> schema.

Field confidence is capped by the OCR engine's own word confidence for the evidence text, so a
value read from a smudged word cannot come out more confident than the OCR that produced it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel

from papertrail.core.extraction.base import ExtractionResult
from papertrail.core.extraction.prompts import system_prompt
from papertrail.core.fields import iter_fields
from papertrail.core.images import PreparedImage
from papertrail.core.llm import LLMClient
from papertrail.core.pack import DocType
from papertrail.core.validators.fuzzy import normalize_text


@dataclass
class OcrResult:
    """Plain text (line-ordered) plus per-word confidence (0-100)."""

    text: str
    word_conf: dict[str, float] = field(default_factory=dict)


def tesseract_ocr(image: PreparedImage) -> OcrResult:
    """Run Tesseract on the high-resolution copy of the image."""
    import pytesseract

    data = pytesseract.image_to_data(
        image.ocr_image(), output_type=pytesseract.Output.DICT, config="--psm 3"
    )
    lines: dict[tuple[int, int, int], list[str]] = {}
    word_conf: dict[str, float] = {}
    for i, word in enumerate(data["text"]):
        word = word.strip()
        if not word:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(word)
        conf = float(data["conf"][i])
        norm = normalize_text(word)
        if norm and conf >= 0:
            word_conf[norm] = min(conf, word_conf.get(norm, 100.0))
    text = "\n".join(" ".join(words) for _, words in sorted(lines.items()))
    return OcrResult(text=text, word_conf=word_conf)


def cap_confidence(doc: BaseModel, word_conf: dict[str, float]) -> None:
    """Lower each field's confidence to the weakest OCR word in its evidence (in place)."""
    if not word_conf:
        return
    for _, fld in iter_fields(doc):
        if fld.value is None or not fld.evidence:
            continue
        confs = [word_conf[w] for w in normalize_text(fld.evidence).split() if w in word_conf]
        if confs:
            fld.confidence = min(fld.confidence, min(confs) / 100.0)


@dataclass
class OcrThenLLMExtractor:
    """OCR the image locally, then have a text model structure the OCR text."""

    llm: LLMClient
    model: str
    ocr: Callable[[PreparedImage], OcrResult] = tesseract_ocr
    max_tokens: int = 3000
    name: str = "ocr+llm"

    def extract(
        self, image: PreparedImage, doc_type: DocType, *, ocr_text: str | None = None
    ) -> ExtractionResult:
        """Extract one document; uses ``ocr_text`` if given (e.g. from browser OCR) else Tesseract."""
        ocr = OcrResult(text=ocr_text) if ocr_text is not None else self.ocr(image)
        user = (
            f"OCR text of a {doc_type.title} (reading order, may contain OCR errors; fix obvious "
            "character confusions only when certain, otherwise lower confidence):\n"
            f"<<<\n{ocr.text}\n>>>\nExtract every field into the template."
        )
        data, resp = self.llm.parse(
            doc_type.schema,
            model=self.model,
            system=system_prompt(doc_type.title, doc_type.description, doc_type.schema, doc_type.hints),
            user=user,
            max_tokens=self.max_tokens,
        )
        cap_confidence(data, ocr.word_conf)
        return ExtractionResult(
            doc_type=doc_type.name, data=data, pipeline=self.name, model=self.model,
            latency_s=resp.latency_s, usage=resp.usage.as_dict(), cached=resp.cached, ocr_text=ocr.text,
        )
