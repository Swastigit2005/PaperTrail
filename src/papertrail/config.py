"""Runtime settings, read from environment variables (and `.env` when present)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Raised when a required setting is missing."""


def _opt(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


@dataclass(frozen=True)
class Settings:
    """All tunables. Model names are only ever read from env vars."""

    groq_api_key: str | None
    vision_model: str | None
    vision_model_b: str | None
    text_model: str | None
    match_model: str | None
    reasoning_effort: str | None
    matching: str
    cache_dir: Path
    max_wait_s: float
    max_image_side: int
    rpm: int
    tpm: int

    def require(self, attr: str) -> str:
        """Return a string setting or raise a ConfigError naming the env var to set."""
        value = getattr(self, attr)
        if not value:
            raise ConfigError(f"Setting '{attr}' is empty; set {_ENV_NAMES[attr]} in .env")
        return value


_ENV_NAMES = {
    "groq_api_key": "GROQ_API_KEY",
    "vision_model": "GROQ_VISION_MODEL",
    "vision_model_b": "GROQ_VISION_MODEL_B",
    "text_model": "GROQ_TEXT_MODEL",
    "match_model": "GROQ_MATCH_MODEL",
}


def get_settings() -> Settings:
    """Load settings from the environment, reading `.env` from the working directory if present."""
    load_dotenv(Path.cwd() / ".env")
    matching = os.getenv("MATCHING", "rules+llm").strip()
    if matching not in ("rules", "rules+llm"):
        raise ConfigError(f"MATCHING must be 'rules' or 'rules+llm', got {matching!r}")
    return Settings(
        groq_api_key=_opt("GROQ_API_KEY"),
        vision_model=_opt("GROQ_VISION_MODEL"),
        vision_model_b=_opt("GROQ_VISION_MODEL_B"),
        text_model=_opt("GROQ_TEXT_MODEL"),
        match_model=_opt("GROQ_MATCH_MODEL"),
        reasoning_effort=_opt("GROQ_TEXT_REASONING_EFFORT"),
        matching=matching,
        cache_dir=Path(os.getenv("PAPERTRAIL_CACHE_DIR", ".cache")),
        max_wait_s=float(os.getenv("PAPERTRAIL_MAX_WAIT", "120")),
        max_image_side=int(os.getenv("PAPERTRAIL_MAX_IMAGE_SIDE", "1024")),
        rpm=int(os.getenv("GROQ_RPM", "30")),
        tpm=int(os.getenv("GROQ_TPM", "8000")),
    )
