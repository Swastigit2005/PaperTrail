"""Pipeline 1: image -> vision LLM -> schema."""

from __future__ import annotations

from dataclasses import dataclass

from papertrail.core.extraction.base import ExtractionResult
from papertrail.core.extraction.prompts import system_prompt
from papertrail.core.images import PreparedImage
from papertrail.core.llm import LLMClient
from papertrail.core.pack import DocType


@dataclass
class VLMDirectExtractor:
    """Send the (downscaled) image straight to a vision model."""

    llm: LLMClient
    model: str
    max_tokens: int = 3000
    name: str = "vlm"

    def extract(
        self, image: PreparedImage, doc_type: DocType, *, ocr_text: str | None = None
    ) -> ExtractionResult:
        """Extract one document from its image; ``ocr_text`` is ignored."""
        data, resp = self.llm.parse(
            doc_type.schema,
            model=self.model,
            system=system_prompt(doc_type.title, doc_type.description, doc_type.schema, doc_type.hints),
            user=f"Extract every field of this {doc_type.title} into the template.",
            images=[image],
            max_tokens=self.max_tokens,
        )
        return ExtractionResult(
            doc_type=doc_type.name, data=data, pipeline=self.name, model=self.model,
            latency_s=resp.latency_s, usage=resp.usage.as_dict(), cached=resp.cached,
        )
