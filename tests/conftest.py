"""Shared test fixtures: offline settings, a fake Groq API and tiny images."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image, ImageDraw

from papertrail.config import Settings
from papertrail.core.images import PreparedImage, load_document
from papertrail.core.llm import LLMClient


def make_settings(tmp_path: Path | None = None, **overrides: Any) -> Settings:
    """Settings that never touch the network or the real .env."""
    base: dict[str, Any] = dict(
        groq_api_key="test-key",
        vision_model="vision-a",
        vision_model_b=None,
        text_model="text-model",
        match_model="match-model",
        reasoning_effort=None,
        matching="rules",
        cache_dir=tmp_path or Path("/nonexistent-cache"),
        max_wait_s=5.0,
        max_image_side=512,
        rpm=30,
        tpm=8000,
    )
    base.update(overrides)
    return Settings(**base)


class FakeCompletions:
    """Stands in for ``groq.Groq().chat.completions``. ``responder`` returns content or raises."""

    def __init__(self, responder: Callable[[dict[str, Any]], str]) -> None:
        self.responder = responder
        self.calls: list[dict[str, Any]] = []

    def create(self, **params: Any) -> Any:
        self.calls.append(params)
        content = self.responder(params)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        )


def fake_api(responder: Callable[[dict[str, Any]], str] | list[str] | dict[str, Any]) -> Any:
    """Build a fake Groq client from a responder function, a list of replies, or one JSON object."""
    if isinstance(responder, dict):
        payload = json.dumps(responder)
        fn: Callable[[dict[str, Any]], str] = lambda _p: payload  # noqa: E731
    elif isinstance(responder, list):
        replies = list(responder)
        fn = lambda _p: replies.pop(0)  # noqa: E731
    else:
        fn = responder
    completions = FakeCompletions(fn)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def fake_llm(responder: Any, tmp_path: Path | None = None, **settings: Any) -> LLMClient:
    """LLMClient over a fake API, with caching only if ``tmp_path`` is given."""
    return LLMClient(
        make_settings(tmp_path, **settings), api=fake_api(responder), sleep=lambda _s: None,
        use_cache=tmp_path is not None,
    )


def make_image(text: str = "RECEIPT", size: tuple[int, int] = (300, 200), seed: int = 0) -> PreparedImage:
    """A small synthetic document image."""
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 10), text, fill="black")
    for i in range(seed % 7):
        draw.rectangle((20 + 30 * i, 100, 40 + 30 * i, 180), fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return load_document(buf.getvalue(), name=f"{text}.png", max_side=512)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Offline settings with a temp cache dir."""
    return make_settings(tmp_path)
