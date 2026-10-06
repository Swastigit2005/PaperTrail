import groq
import httpx
import pytest
from pydantic import BaseModel

from papertrail.core.llm import (
    LLMClient,
    LLMError,
    QuotaExhausted,
    RateLimited,
    RateLimiter,
    extract_json,
    parse_duration,
)
from tests.conftest import fake_api, fake_llm, make_image, make_settings


class Answer(BaseModel):
    x: int


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def rate_limit_error(retry_after: str, message: str = "Rate limit reached for model on tokens per minute (TPM)"):
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp = httpx.Response(429, headers={"retry-after": retry_after}, request=req)
    return groq.RateLimitError(message, response=resp, body=None)


def test_parse_duration():
    assert parse_duration("59.288s") == pytest.approx(59.288)
    assert parse_duration("4m19.2s") == pytest.approx(259.2)
    assert parse_duration("1h2m") == pytest.approx(3720)
    assert parse_duration("250ms") == pytest.approx(0.25)
    assert parse_duration("7") == 7.0
    assert parse_duration("soon") is None


def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('```json\n{"x": 1}\n```') == {"x": 1}
    assert extract_json('Sure! {"x": 2} hope that helps') == {"x": 2}


def test_limiter_waits_for_token_window():
    clock = Clock()
    lim = RateLimiter(rpm=30, tpm=1000, clock=clock, sleep=clock.sleep)
    lim.acquire(600, max_wait=100)
    lim.acquire(600, max_wait=100)  # must wait for the first to leave the 60 s window
    assert clock.t == pytest.approx(60.0)


def test_limiter_request_cap_and_max_wait():
    clock = Clock()
    lim = RateLimiter(rpm=2, tpm=10_000, clock=clock, sleep=clock.sleep)
    lim.acquire(1, 0)
    lim.acquire(1, 0)
    with pytest.raises(RateLimited) as exc:
        lim.acquire(1, max_wait=5)
    assert exc.value.retry_after == pytest.approx(60.0)


def test_retry_after_429_then_success():
    sleeps: list[float] = []
    calls = {"n": 0}

    def responder(_params):
        calls["n"] += 1
        if calls["n"] == 1:
            raise rate_limit_error("2")
        return '{"x": 7}'

    llm = LLMClient(make_settings(), api=fake_api(responder), sleep=sleeps.append, use_cache=False)
    parsed, resp = llm.parse(Answer, model="m", system="s", user="u")
    assert parsed.x == 7 and calls["n"] == 2
    assert 2.0 in sleeps  # honoured retry-after


def test_429_longer_than_budget_raises_rate_limited():
    llm = LLMClient(make_settings(max_wait_s=5), api=fake_api(lambda _p: (_ for _ in ()).throw(rate_limit_error("90"))),
                    sleep=lambda _s: None, use_cache=False)
    with pytest.raises(RateLimited):
        llm.chat(model="m", system="s", user="u")


def test_daily_quota_raises_immediately():
    err = rate_limit_error("3600", "Rate limit reached on tokens per day (TPD): Limit 200000")
    llm = LLMClient(make_settings(), api=fake_api(lambda _p: (_ for _ in ()).throw(err)),
                    sleep=lambda _s: None, use_cache=False)
    with pytest.raises(QuotaExhausted):
        llm.chat(model="m", system="s", user="u")


def test_cache_hit_skips_api(tmp_path):
    llm = fake_llm({"x": 1}, tmp_path)
    img = make_image()
    first = llm.chat(model="m", system="s", user="u", images=[img])
    second = llm.chat(model="m", system="s", user="u", images=[img])
    assert not first.cached and second.cached
    assert len(llm.api.chat.completions.calls) == 1
    assert llm.usage.requests == 1 and llm.usage.cached_requests == 1
    assert second.usage.total_tokens == first.usage.total_tokens  # original cost reported on a hit
    other = llm.chat(model="m", system="s", user="u", images=[make_image("OTHER")])
    assert not other.cached  # different image bytes -> different key


def test_repair_round_trip_and_failure():
    llm = fake_llm(['{"x": "not a number"}', '{"x": 3}'])
    parsed, resp = llm.parse(Answer, model="m", system="s", user="u")
    assert parsed.x == 3 and resp.usage.requests == 2
    bad = fake_llm(["nope", "still nope"])
    with pytest.raises(LLMError):
        bad.parse(Answer, model="m", system="s", user="u")


def test_reasoning_effort_only_for_text_models():
    llm = fake_llm({"x": 1}, reasoning_effort="low")
    llm.chat(model="vision-a", system="s", user="u")
    llm.chat(model="text-model", system="s", user="u")
    calls = llm.api.chat.completions.calls
    assert "reasoning_effort" not in calls[0] and calls[1]["reasoning_effort"] == "low"
    assert calls[0]["response_format"] == {"type": "json_object"}
