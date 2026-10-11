"""Web API with a fake LLM: limits, extraction, evaluation, busy handling. No network."""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app as entry
from papertrail.core.llm import RateLimited
from papertrail.web import api
from tests.conftest import fake_llm, make_settings
from tests.test_payables_pack import inv_line, invoice, po
from tests.test_pipelines import RECEIPT_JSON


@pytest.fixture
def client(tmp_path):
    api._state.clear()
    api._state["settings"] = make_settings(tmp_path)
    api._state["tesseract"] = False
    api._ip_hits.clear()
    yield TestClient(entry.app)
    api._state.clear()


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (200, 120), "white").save(buf, "PNG")
    return buf.getvalue()


def test_packs_and_health(client):
    names = {p["name"] for p in client.get("/api/packs").json()}
    assert names >= {"claims", "payables", "expenses"}
    health = client.get("/api/health").json()
    assert health["ok"] and "groq_api_key" not in health and "test-key" not in str(health)


def test_extract_with_override(client, tmp_path):
    api._state["llm"] = fake_llm(RECEIPT_JSON, tmp_path)
    r = client.post("/api/extract", data={"pack": "expenses", "doc_type": "receipt"},
                    files={"file": ("r.png", png_bytes(), "image/png")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["doc_type"] == "receipt" and body["data"]["total"]["value"] == 180.0
    assert body["detection"]["method"] == "override" and len(body["image"]["dhash"]) == 16


def test_upload_limits(client):
    r = client.post("/api/extract", data={"pack": "expenses"}, files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 415
    big = b"0" * (5 * 1024 * 1024 + 10)
    r = client.post("/api/extract", data={"pack": "expenses"}, files={"file": ("a.png", big, "image/png")})
    assert r.status_code == 413
    r = client.post("/api/extract", data={"pack": "expenses", "pipeline": "ocr+llm"},
                    files={"file": ("a.png", png_bytes(), "image/png")})
    assert r.status_code == 400 and "OCR" in r.json()["detail"]


def test_rate_limit_is_friendly_busy_message(client, tmp_path):
    def busy(_params):
        raise RateLimited("rate limited", retry_after=42)

    api._state["llm"] = fake_llm(busy, tmp_path)
    r = client.post("/api/extract", data={"pack": "expenses", "doc_type": "receipt"},
                    files={"file": ("r.png", png_bytes(), "image/png")})
    assert r.status_code == 429
    assert r.json()["error"] == "demo_busy" and r.json()["retry_after"] == 42


def test_evaluate_rules(client):
    docs = {"invoice": invoice().model_dump(mode="json"), "purchase_order": po().model_dump(mode="json")}
    payload = {"pack": "payables", "matching": "rules", "documents": docs}
    r = client.post("/api/evaluate", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["decision"]["outcome"] == "approve"
    assert {c["code"] for c in r.json()["crosschecks"]} >= {"gstin_mismatch", "line_not_on_po"}


def test_evaluate_falls_back_to_rules_when_llm_busy(client, tmp_path):
    def busy(_params):
        raise RateLimited("rate limited", retry_after=30)

    api._state["llm"] = fake_llm(busy, tmp_path)
    order = po()
    inv = invoice(lines=[inv_line("Something unusual", "9999", 1, 10.0)])
    payload = {"pack": "payables", "matching": "rules+llm",
               "documents": {"invoice": inv.model_dump(mode="json"), "purchase_order": order.model_dump(mode="json")}}
    r = client.post("/api/evaluate", json=payload)
    assert r.status_code == 200 and r.json()["matching"] == "rules" and r.json()["notes"]


def test_evaluate_missing_document(client):
    docs = {"invoice": invoice().model_dump(mode="json")}
    r = client.post("/api/evaluate", json={"pack": "payables", "documents": docs})
    assert r.status_code == 400 and "purchase_order" in r.json()["detail"]


def test_ip_cap(client, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "IP_CAP_PER_HOUR", 1)
    api._state["llm"] = fake_llm(RECEIPT_JSON, tmp_path)
    files = {"file": ("r.png", png_bytes(), "image/png")}
    assert client.post("/api/extract", data={"pack": "expenses", "doc_type": "receipt"}, files=files).status_code == 200
    files = {"file": ("r.png", png_bytes(), "image/png")}
    assert client.post("/api/extract", data={"pack": "expenses", "doc_type": "receipt"}, files=files).status_code == 429


def _model_router(busy_models, reply):
    """Fake API: models in ``busy_models`` answer 429, others reply with ``reply``."""
    import json as _json

    def respond(params):
        if params["model"] in busy_models:
            raise RateLimited("quota", retry_after=60)
        return _json.dumps(reply)

    return respond


def test_vision_busy_falls_back_to_text_model_with_ocr_text(client, tmp_path):
    api._state["llm"] = fake_llm(_model_router({"vision-a"}, RECEIPT_JSON), tmp_path)
    form = {"pack": "expenses", "doc_type": "receipt", "ocr_text": "CAFE AROMA total 180"}
    r = client.post("/api/extract", data=form, files={"file": ("r.png", png_bytes(), "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "text-model" and "Fallback used" in r.json()["notes"][0]


def test_text_model_busy_falls_back_to_match_model(client, tmp_path):
    api._state["llm"] = fake_llm(_model_router({"vision-a", "text-model"}, RECEIPT_JSON), tmp_path)
    r = client.post("/api/extract", data={"pack": "expenses", "doc_type": "receipt", "pipeline": "ocr+llm",
                                          "ocr_text": "CAFE AROMA total 180"},
                    files={"file": ("r.png", png_bytes(), "image/png")})
    assert r.status_code == 200 and r.json()["model"] == "match-model"


def test_vision_busy_without_ocr_text_asks_browser_for_ocr(client, tmp_path):
    api._state["llm"] = fake_llm(_model_router({"vision-a"}, RECEIPT_JSON), tmp_path)
    r = client.post("/api/extract", data={"pack": "expenses", "doc_type": "receipt"},
                    files={"file": ("r.png", png_bytes(), "image/png")})
    assert r.status_code == 429 and r.json()["fallback"] == "ocr"


def test_every_model_busy_is_friendly(client, tmp_path):
    api._state["llm"] = fake_llm(_model_router({"vision-a", "text-model", "match-model"}, RECEIPT_JSON), tmp_path)
    r = client.post("/api/extract", data={"pack": "expenses", "doc_type": "receipt", "ocr_text": "x"},
                    files={"file": ("r.png", png_bytes(), "image/png")})
    assert r.status_code == 429 and r.json()["fallback"] is None and "example documents" in r.json()["message"]
