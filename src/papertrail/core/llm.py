"""Groq client wrapper built for the free tier.

- client-side sliding-window limiter on requests and tokens per minute, per model;
- exponential backoff on HTTP 429 that honours ``retry-after``; daily-quota 429s raise
  ``QuotaExhausted`` immediately so benchmark runs can stop and resume the next day;
- on-disk JSON response cache keyed on the full request (images included via their bytes);
- token accounting per call;
- JSON responses validated against a Pydantic schema with one repair round-trip.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import groq
from pydantic import BaseModel, ValidationError

from papertrail.config import Settings
from papertrail.core.images import PreparedImage

M = TypeVar("M", bound=BaseModel)
log = logging.getLogger(__name__)

IMAGE_TOKEN_ESTIMATE = 1400  # measured ~1,309 prompt tokens for a <=1024px image on qwen3.8-27b
CHARS_PER_TOKEN = 3.5


class LLMError(RuntimeError):
    """The model call failed or returned unusable output."""


class RateLimited(LLMError):
    """Capacity is not available within the allowed wait. Shown as 'demo busy' in the UI."""

    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class QuotaExhausted(RateLimited):
    """A per-day limit was hit; retrying today is pointless."""


@dataclass
class Usage:
    """Token and request accounting."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    requests: int = 0
    cached_requests: int = 0

    @property
    def total_tokens(self) -> int:
        """Prompt plus completion tokens."""
        return self.prompt_tokens + self.completion_tokens

    def add(self, other: Usage) -> Usage:
        """Accumulate another Usage into this one and return self."""
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.requests += other.requests
        self.cached_requests += other.cached_requests
        return self

    def as_dict(self) -> dict[str, int]:
        """Plain-dict form for JSON output."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "requests": self.requests,
            "cached_requests": self.cached_requests,
        }


@dataclass
class LLMResponse:
    """Raw text response plus accounting."""

    content: str
    model: str
    usage: Usage
    latency_s: float
    cached: bool


# --- rate limiting ------------------------------------------------------------------------


_DURATION = re.compile(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+)ms)?$")


def parse_duration(text: str | None) -> float | None:
    """Parse Groq durations like '59.29s', '4m19.2s', '1h2m', '250ms' or plain seconds."""
    if not text:
        return None
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        pass
    m = _DURATION.match(text)
    if not m or not any(m.groups()):
        return None
    h, mins, s, ms = (float(g) if g else 0.0 for g in m.groups())
    return h * 3600 + mins * 60 + s + ms / 1000


class RateLimiter:
    """Sliding 60-second window over requests and tokens for one model."""

    def __init__(
        self,
        rpm: int,
        tpm: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rpm, self.tpm = rpm, tpm
        self._clock, self._sleep = clock, sleep
        self._events: deque[tuple[float, int]] = deque()
        self._blocked_until = 0.0
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0][0] >= 60.0:
            self._events.popleft()

    def wait_time(self, tokens: int) -> float:
        """Seconds until a request of ``tokens`` fits in the window."""
        now = self._clock()
        self._prune(now)
        wait = max(0.0, self._blocked_until - now)
        if len(self._events) >= self.rpm:
            wait = max(wait, 60.0 - (now - self._events[0][0]))
        used = sum(t for _, t in self._events)
        if used + tokens > self.tpm:
            # wait until enough old events expire to make room
            excess = used + tokens - self.tpm
            for ts, t in self._events:
                excess -= t
                if excess <= 0:
                    wait = max(wait, 60.0 - (now - ts))
                    break
        return wait

    def acquire(self, tokens: int, max_wait: float) -> None:
        """Block until capacity exists, or raise RateLimited if that takes longer than max_wait."""
        tokens = min(tokens, self.tpm)
        with self._lock:
            wait = self.wait_time(tokens)
            if wait > max_wait:
                raise RateLimited(f"rate limit: next slot in {wait:.0f}s", retry_after=wait)
            if wait > 0:
                self._sleep(wait)
            self._events.append((self._clock(), tokens))

    def record_actual(self, tokens: int) -> None:
        """Replace the estimate of the most recent request with the real token count."""
        with self._lock:
            if self._events:
                ts, _ = self._events.pop()
                self._events.append((ts, tokens))

    def block_for(self, seconds: float) -> None:
        """Hold all requests for ``seconds`` (after a server-side 429)."""
        with self._lock:
            self._blocked_until = max(self._blocked_until, self._clock() + seconds)


# --- disk cache -----------------------------------------------------------------------------


class ResponseCache:
    """One JSON file per request hash. Best-effort: silently skips on read-only filesystems."""

    def __init__(self, root: Path | None) -> None:
        self.root = root / "llm" if root else None

    def _path(self, key: str) -> Path | None:
        return self.root / key[:2] / f"{key}.json" if self.root else None

    def get(self, key: str) -> dict[str, Any] | None:
        """Return a cached response dict or None."""
        path = self._path(key)
        if path is None or not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

    def put(self, key: str, value: dict[str, Any]) -> None:
        """Store a response dict (atomic rename)."""
        path = self._path(key)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(value))
            tmp.replace(path)
        except OSError:
            pass


# --- client ---------------------------------------------------------------------------------


def extract_json(text: str) -> Any:
    """Parse a JSON object from model output, tolerating code fences and surrounding prose."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


def _is_daily_limit(exc: groq.RateLimitError) -> bool:
    text = str(exc).lower()
    return "per day" in text or "(tpd)" in text or "(rpd)" in text


@dataclass
class LLMClient:
    """Groq chat client with limiter, retries, cache and accounting. ``api`` is injectable for tests."""

    settings: Settings
    api: Any = None
    sleep: Callable[[float], None] = time.sleep
    max_retries: int = 4
    use_cache: bool = True
    usage: Usage = field(default_factory=Usage)
    _limiters: dict[str, RateLimiter] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.api is None:
            self.api = groq.Groq(api_key=self.settings.require("groq_api_key"), max_retries=0, timeout=90)
        self.cache = ResponseCache(self.settings.cache_dir if self.use_cache else None)

    def limiter(self, model: str) -> RateLimiter:
        """The limiter for one model (each Groq model has its own quota)."""
        if model not in self._limiters:
            self._limiters[model] = RateLimiter(self.settings.rpm, self.settings.tpm, sleep=self.sleep)
        return self._limiters[model]

    def _params(self, model: str, messages: list[dict[str, Any]], max_tokens: int) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_completion_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        if self.settings.reasoning_effort and model != self.settings.vision_model:
            params["reasoning_effort"] = self.settings.reasoning_effort
        return params

    def chat(
        self,
        *,
        model: str,
        system: str,
        user: str,
        images: Sequence[PreparedImage] = (),
        max_tokens: int = 2048,
        history: Sequence[dict[str, Any]] = (),
        max_wait: float | None = None,
    ) -> LLMResponse:
        """One JSON-mode chat completion, served from cache when possible."""
        content: Any = user
        if images:
            content = [{"type": "text", "text": user}] + [
                {"type": "image_url", "image_url": {"url": img.data_url()}} for img in images
            ]
        messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": content}]
        params = self._params(model, messages, max_tokens)
        key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()

        cached = self.cache.get(key)
        if cached is not None:
            # report the original call's cost and latency (benchmarks measure the pipeline, not the cache)
            orig = cached.get("usage", {})
            usage = Usage(
                prompt_tokens=orig.get("prompt_tokens", 0),
                completion_tokens=orig.get("completion_tokens", 0),
                cached_requests=1,
            )
            self.usage.add(usage)
            return LLMResponse(cached["content"], model, usage, float(cached.get("latency_s", 0.0)), cached=True)

        estimate = int((len(system) + len(user)) / CHARS_PER_TOKEN) + IMAGE_TOKEN_ESTIMATE * len(images)
        estimate += min(max_tokens, 1500)
        wait_budget = self.settings.max_wait_s if max_wait is None else max_wait
        limiter = self.limiter(model)

        for attempt in range(self.max_retries + 1):
            limiter.acquire(estimate, wait_budget)
            start = time.perf_counter()
            try:
                resp = self.api.chat.completions.create(**params)
            except groq.RateLimitError as exc:
                retry_after = parse_duration(exc.response.headers.get("retry-after")) or 2.0**attempt
                log.warning("429 from %s (attempt %d, retry-after %.1fs): %s", model, attempt, retry_after,
                            str(exc)[:300])
                if _is_daily_limit(exc):
                    raise QuotaExhausted(f"daily quota exhausted for {model}", retry_after) from exc
                limiter.block_for(retry_after)
                if retry_after > wait_budget or attempt == self.max_retries:
                    raise RateLimited(f"rate limited on {model}", retry_after) from exc
                self.sleep(retry_after)
                continue
            except (groq.APIConnectionError, groq.APITimeoutError, groq.InternalServerError) as exc:
                if attempt == self.max_retries:
                    raise LLMError(f"Groq unavailable: {exc}") from exc
                self.sleep(min(30.0, 2.0**attempt))
                continue
            latency = time.perf_counter() - start
            text = resp.choices[0].message.content or ""
            usage = Usage(
                prompt_tokens=resp.usage.prompt_tokens,
                completion_tokens=resp.usage.completion_tokens,
                requests=1,
            )
            limiter.record_actual(usage.total_tokens)
            self.usage.add(usage)
            self.cache.put(key, {"content": text, "model": model, "usage": usage.as_dict(), "latency_s": latency})
            return LLMResponse(text, model, usage, latency, cached=False)
        raise LLMError("unreachable")  # pragma: no cover

    def parse(
        self,
        schema: type[M],
        *,
        model: str,
        system: str,
        user: str,
        images: Sequence[PreparedImage] = (),
        max_tokens: int = 2048,
        max_wait: float | None = None,
    ) -> tuple[M, LLMResponse]:
        """Chat and validate the JSON reply against ``schema``; on failure, ask once for a fix."""
        resp = self.chat(model=model, system=system, user=user, images=images,
                         max_tokens=max_tokens, max_wait=max_wait)
        try:
            return schema.model_validate(extract_json(resp.content)), resp
        except (ValueError, ValidationError) as err:
            error = str(err)[:800]
        history = [
            {"role": "user", "content": user},
            {"role": "assistant", "content": resp.content[:6000]},
        ]
        repair = self.chat(
            model=model, system=system, images=(), max_tokens=max_tokens, history=history,
            user=f"Your reply was not valid for the required JSON shape: {error}\nReturn only the corrected JSON.",
            max_wait=max_wait,
        )
        try:
            parsed = schema.model_validate(extract_json(repair.content))
        except (ValueError, ValidationError) as err:
            raise LLMError(f"model returned invalid JSON twice: {err}") from err
        total = Usage().add(resp.usage).add(repair.usage)
        return parsed, LLMResponse(repair.content, model, total, resp.latency_s + repair.latency_s, False)
