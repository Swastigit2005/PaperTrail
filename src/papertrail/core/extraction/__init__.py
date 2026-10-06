"""Extraction pipelines behind one interface."""

from __future__ import annotations

from papertrail.config import Settings
from papertrail.core.extraction.base import ExtractionResult, Extractor
from papertrail.core.extraction.ocr_then_llm import OcrThenLLMExtractor
from papertrail.core.extraction.vlm_direct import VLMDirectExtractor
from papertrail.core.llm import LLMClient

__all__ = ["ExtractionResult", "Extractor", "OcrThenLLMExtractor", "VLMDirectExtractor", "build_extractor"]


def build_extractor(pipeline: str, llm: LLMClient, settings: Settings) -> Extractor:
    """Factory: ``vlm`` (vision model A), ``vlm_b`` (vision model B) or ``ocr+llm``."""
    if pipeline == "vlm":
        return VLMDirectExtractor(llm, settings.require("vision_model"))
    if pipeline == "vlm_b":
        return VLMDirectExtractor(llm, settings.require("vision_model_b"), name="vlm_b")
    if pipeline == "ocr+llm":
        return OcrThenLLMExtractor(llm, settings.require("text_model"))
    raise ValueError(f"unknown pipeline {pipeline!r}; use vlm, vlm_b or ocr+llm")
