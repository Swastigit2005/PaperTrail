"""The extractor interface shared by both pipelines."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PField

from papertrail.core.images import PreparedImage
from papertrail.core.pack import DocType


class ExtractionResult(BaseModel):
    """A parsed document plus how it was obtained."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    doc_type: str
    data: BaseModel
    pipeline: str
    model: str
    latency_s: float
    usage: dict[str, int] = PField(default_factory=dict)
    cached: bool = False
    ocr_text: str | None = None


class Extractor(Protocol):
    """image -> schema instance. Implementations: VLMDirectExtractor, OcrThenLLMExtractor."""

    name: str

    def extract(
        self, image: PreparedImage, doc_type: DocType, *, ocr_text: str | None = None
    ) -> ExtractionResult:
        """Extract ``doc_type``'s schema from the image. ``ocr_text`` may be supplied by the
        caller (e.g. browser-side OCR); pipelines that do not need it ignore it."""
        ...
