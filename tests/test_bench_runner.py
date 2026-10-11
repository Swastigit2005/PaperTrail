"""Benchmark runner helpers: the rolling 24h token ledger that protects the demo's quota."""

import json
import time

from bench import run_benchmark as rb


def test_ledger_rolling_window(tmp_path, monkeypatch):
    monkeypatch.setattr(rb, "LEDGER", tmp_path / "ledger.jsonl")
    rb.ledger_add("vlm", 5000)
    rb.ledger_add("vlm", 0)  # cache hits are free and not recorded
    rb.ledger_add("ocr+llm", 700)
    old = {"t": time.time() - 90000, "model": "vlm", "tokens": 99999}  # older than 24h
    with rb.LEDGER.open("a") as fh:
        fh.write(json.dumps(old) + "\n")
    assert rb.ledger_used("vlm") == 5000 and rb.ledger_used("ocr+llm") == 700
    assert len(rb.LEDGER.read_text().splitlines()) == 3


def test_truth_tree_parses_into_schema():
    from papertrail.core.pack import get_pack

    doc = rb.truth_doc(get_pack("expenses"), "receipt", {"merchant": "Cafe", "total": 10.0, "items": []})
    assert doc.merchant.value == "Cafe" and doc.merchant.confidence == 1.0 and doc.tax.value is None
