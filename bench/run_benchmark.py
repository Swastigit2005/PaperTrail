"""Run a benchmark configuration over the synthetic pairs and score it.

    python -m bench.run_benchmark --pack claims --config vlm_llm --limit 20
    python -m bench.run_benchmark --pack payables --config oracle_rules
    python -m bench.run_benchmark --pack claims --config vlm_llm --score-only

Configs (pipeline x matching): vlm_rules, vlm_llm, vlmb_llm, ocr_llm, oracle_rules, oracle_llm.
Oracle configs skip extraction and feed ground truth into validation + cross-checks.

Runs are resumable: each finished pair is appended to bench/results/runs/<pack>__<config>.jsonl and
skipped next time. A daily-quota 429 stops the run cleanly (exit code 3) so it can resume tomorrow.
Scores go to bench/results/<pack>__<config>.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from bench import metrics
from papertrail.config import get_settings
from papertrail.core.crosscheck import CheckContext
from papertrail.core.engine import DocumentInput, evaluate, process_case
from papertrail.core.extraction import build_extractor
from papertrail.core.fields import wildcard
from papertrail.core.images import load_document
from papertrail.core.llm import LLMClient, LLMError, QuotaExhausted, RateLimited
from papertrail.core.matching import Matcher
from papertrail.core.pack import Pack, get_pack
from papertrail.core.validators.rxnorm import RxNormClient

ROOT = Path(__file__).resolve().parent
DATASETS = {
    "test": (ROOT / "data" / "synthetic", ROOT / "results"),  # frozen seed 7: the reported numbers
    "dev": (ROOT / "data" / "dev", ROOT / "dev_results"),  # seed 1: used for tuning
    "real": (ROOT / "data" / "real_fake", ROOT / "results" / "real_fake"),  # phone photos, reported separately
}
DATA, RESULTS = DATASETS["test"]
RUNS = RESULTS / "runs"


def use_dataset(name: str) -> None:
    """Switch between the frozen test set (reported) and the dev set (used for tuning)."""
    global DATA, RESULTS, RUNS
    DATA, RESULTS = DATASETS[name]
    RUNS = RESULTS / "runs"

CONFIGS: dict[str, dict[str, str]] = {
    "vlm_rules": {"pipeline": "vlm", "matching": "rules"},
    "vlm_llm": {"pipeline": "vlm", "matching": "rules+llm"},
    "vlmb_llm": {"pipeline": "vlm_b", "matching": "rules+llm"},
    "ocr_llm": {"pipeline": "ocr+llm", "matching": "rules+llm"},
    "oracle_rules": {"pipeline": "oracle", "matching": "rules"},
    "oracle_llm": {"pipeline": "oracle", "matching": "rules+llm"},
}
TAXONOMY = {
    "claims": ("unprescribed_medicine", "patient_name_mismatch", "bill_before_prescription",
               "implausible_quantity", "arithmetic_error"),
    "payables": ("gstin_mismatch", "invalid_gstin", "rate_above_po", "qty_over_po", "line_not_on_po",
                 "wrong_tax_split", "total_mismatch"),
}  # fmt: skip
VALIDATION_TYPES = {"claims": ("arithmetic_error",), "payables": ("invalid_gstin", "total_mismatch")}


def truth_tree(value: Any) -> Any:
    """Plain truth JSON -> Field-shaped JSON (confidence 1.0) so it parses into the pack schema."""
    if isinstance(value, dict):
        return {k: truth_tree(v) for k, v in value.items()}
    if isinstance(value, list):
        return [truth_tree(v) for v in value]
    if value is None:
        return {"value": None, "confidence": 1.0, "null_reason": "not present"}
    return {"value": value, "confidence": 1.0, "evidence": str(value)}


def truth_doc(pack: Pack, doc_type: str, truth: dict[str, Any]) -> BaseModel:
    """Ground truth as a schema instance (oracle runs)."""
    return pack.doc_type(doc_type).schema.model_validate(truth_tree(truth))


def load_manifest(pack: str) -> dict[str, Any]:
    """The generated dataset for one pack."""
    path = DATA / pack / "manifest.json"
    if not path.exists():
        sys.exit(f"{path} missing: run `python -m bench.generate_synthetic` first")
    return json.loads(path.read_text())


def _findings_json(validations: list, outcomes: list) -> list[dict[str, Any]]:
    out = [f.model_dump(mode="json") for f in validations]
    for o in outcomes:
        out += [f.model_dump(mode="json") for f in o.findings]
    return out


def run_pair(pair: dict[str, Any], pack: Pack, config: str, ctx: CheckContext, extractor: Any) -> dict[str, Any]:
    """Process one pair; returns the per-pair record (never raises except QuotaExhausted)."""
    record: dict[str, Any] = {"pair_id": pair["pair_id"], "tier": pair["tier"], "config": config,
                              "expected": sorted({d["type"] for d in pair["discrepancies"]}),
                              "expected_outcome": pair["expected_outcome"]}
    start = time.perf_counter()
    try:
        if extractor is None:  # oracle
            docs = {dt: truth_doc(pack, dt, d["truth"]) for dt, d in pair["documents"].items()}
            validations, outcomes, decision = evaluate(pack, docs, ctx)
            record.update(documents={dt: doc.model_dump(mode="json") for dt, doc in docs.items()}, usage={},
                          model_latency_s=0.0)
        else:
            inputs = [
                DocumentInput(load_document((DATA / pack.name / d["image"]).read_bytes(), name=d["image"]), doc_type=dt)
                for dt, d in pair["documents"].items()
            ]
            result = process_case(pack, inputs, extractor, ctx)
            validations, outcomes, decision = result.validations, result.crosschecks, result.decision
            record.update(documents={dt: r.data for dt, r in result.documents.items()}, usage=result.usage,
                          model_latency_s=round(sum(r.latency_s for r in result.documents.values()), 3))
        record.update(findings=_findings_json(validations, outcomes), decision=decision.outcome.value,
                      reasons=[r.message for r in decision.reasons], status="ok")
    except (QuotaExhausted, RateLimited):
        raise
    except (LLMError, ValueError) as exc:
        record.update(status="error", error=str(exc)[:500], documents={}, findings=[], decision="needs_review",
                      reasons=["extraction failed: routed to a human"], usage={}, model_latency_s=0.0)
    record["wall_s"] = round(time.perf_counter() - start, 3)
    return record


LEDGER = ROOT / "logs" / "token_ledger.jsonl"


def ledger_used(model: str, window_s: float = 86400) -> int:
    """Tokens this benchmark billed for ``model`` in the last 24h (Groq's daily limit is a rolling window)."""
    if not LEDGER.exists():
        return 0
    now = time.time()
    total = 0
    for line in LEDGER.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("model") == model and now - e.get("t", 0) < window_s:
            total += int(e.get("tokens", 0))
    return total


def ledger_add(model: str, tokens: int) -> None:
    """Record billed tokens for the rolling budget."""
    if tokens:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a") as fh:
            fh.write(json.dumps({"t": time.time(), "model": model, "tokens": tokens}) + "\n")


def run(pack_name: str, config: str, limit: int | None, tiers: set[str] | None) -> Path:
    """Run (or resume) one config; returns the JSONL path."""
    spec = CONFIGS[config]
    settings = get_settings()
    pack = get_pack(pack_name)
    manifest = load_manifest(pack_name)
    pairs = [p for p in manifest["pairs"] if not tiers or p["tier"] in tiers][:limit]
    RUNS.mkdir(parents=True, exist_ok=True)
    out = RUNS / f"{pack_name}__{config}.jsonl"
    done = {json.loads(line)["pair_id"] for line in out.read_text().splitlines()} if out.exists() else set()
    todo = [p for p in pairs if p["pair_id"] not in done]
    print(f"[{pack_name}/{config}] {len(pairs)} pairs selected, {len(done & {p['pair_id'] for p in pairs})} done, "
          f"{len(todo)} to run")
    if not todo:
        return out

    needs_llm = spec["pipeline"] != "oracle" or spec["matching"] == "rules+llm"
    llm = LLMClient(settings) if needs_llm else None
    extractor = None if spec["pipeline"] == "oracle" else build_extractor(spec["pipeline"], llm, settings)
    matcher = Matcher(mode=spec["matching"], llm=llm, model=settings.match_model)
    rxnorm = RxNormClient(cache_dir=settings.cache_dir) if pack_name == "claims" else None
    today = date.fromisoformat(manifest["reference_date"]) if manifest.get("reference_date") else date.today()
    budget = int(os.getenv("BENCH_DAILY_TOKEN_BUDGET", "110000"))
    ledger_model = spec["pipeline"] if spec["pipeline"] != "oracle" else "match"
    with out.open("a") as fh:
        for i, pair in enumerate(todo, 1):
            used = ledger_used(ledger_model)
            if budget and used >= budget:
                print(f"\nDaily benchmark budget reached ({used:,} of {budget:,} tokens in the last 24h for "
                      f"'{ledger_model}'); the rest of the quota is left for the live demo. Resume later.")
                sys.exit(3)
            ctx = CheckContext(matcher=matcher, case_id=pair["pair_id"], today=today,
                               services={"rxnorm": rxnorm} if rxnorm else {})
            record = None
            for attempt in range(8):
                try:
                    record = run_pair(pair, pack, config, ctx, extractor)
                    break
                except QuotaExhausted as exc:
                    print(f"\nDaily Groq quota exhausted ({exc}). {i - 1} pairs finished this session; "
                          f"re-run the same command after the quota resets to resume.")
                    sys.exit(3)
                except RateLimited as exc:  # per-minute limit: wait it out, never score it as an error
                    wait = min(600.0, exc.retry_after + 5)
                    print(f"  rate limited on {pair['pair_id']} (attempt {attempt + 1}); waiting {wait:.0f}s")
                    time.sleep(wait)
            if record is None:
                print(f"  giving up on {pair['pair_id']} for now; it will be retried on the next run")
                continue
            fh.write(json.dumps(record) + "\n")
            fh.flush()
            ledger_add(ledger_model, record["usage"].get("billed_tokens", 0))
            print(f"  {i}/{len(todo)} {pair['pair_id']} {pair['tier']:<7} expected={record['expected_outcome']:<7} "
                  f"got={record['decision']:<12} {record['status']} tokens={record['usage'].get('total_tokens', 0)}")
    return out


def score(pack_name: str, config: str) -> dict[str, Any]:
    """Compute all metrics for one config from its JSONL and write the JSON report."""
    manifest = load_manifest(pack_name)
    by_id = {p["pair_id"]: p for p in manifest["pairs"]}
    path = RUNS / f"{pack_name}__{config}.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    taxonomy = TAXONOMY[pack_name]
    pack = get_pack(pack_name)

    detection = metrics.DetectionCounts()
    per_pair: dict[str, tuple[set[str], set[str]]] = {}
    slices: dict[str, metrics.FieldCounts] = {}
    scored: list[metrics.ScoredField] = []
    critical_scored: list[metrics.ScoredField] = []
    decisions = []
    for rec in records:
        pair = by_id[rec["pair_id"]]
        expected = set(rec["expected"])
        predicted = metrics.detected_types(rec["findings"], taxonomy)
        detection.update(expected, predicted, taxonomy)
        per_pair[rec["pair_id"]] = (expected, predicted)
        decisions.append((rec["expected_outcome"], rec["decision"]))
        if CONFIGS[config]["pipeline"] == "oracle":
            continue
        for dt, d in pair["documents"].items():
            counts, sf = metrics.score_document(rec["documents"].get(dt), d["truth"], d["occluded"])
            for key in ("all", f"{dt}", f"{dt}|{pair['tier']}", f"tier|{pair['tier']}"):
                slices.setdefault(key, metrics.FieldCounts()).add(counts)
            scored += sf
            crit = set(pack.policy.critical_fields.get(dt, ()))
            critical_scored += [s for s in sf if wildcard(s.path) in crit or s.path in crit]

    validation = {
        t: (detection.tp.get(t, 0) / (detection.tp.get(t, 0) + detection.fn.get(t, 0))
            if detection.tp.get(t, 0) + detection.fn.get(t, 0) else None)
        for t in VALIDATION_TYPES[pack_name]
    }
    lat = [r["model_latency_s"] for r in records if r["status"] == "ok" and r["model_latency_s"]]
    tok = [r["usage"].get("total_tokens", 0) for r in records if r["status"] == "ok" and r["usage"]]
    report: dict[str, Any] = {
        "pack": pack_name, "config": config, **CONFIGS[config],
        "n_pairs": len(records), "n_errors": sum(r["status"] != "ok" for r in records),
        "dataset": {"seed": manifest["seed"], "total_pairs": manifest["count"]},
        "detection": {"micro": detection.micro(), "per_type": detection.per_type(taxonomy)},
        "validation_catch_rate": validation,
        "decision": metrics.decision_summary(decisions),
        "ops": {"model_latency_p50_s": metrics.percentile(lat, 50), "model_latency_p95_s": metrics.percentile(lat, 95),
                "tokens_per_pair_mean": sum(tok) / len(tok) if tok else None,
                "tokens_per_pair_p95": metrics.percentile([float(t) for t in tok], 95)},
        "per_pair": {k: {"expected": sorted(e), "predicted": sorted(p)} for k, (e, p) in per_pair.items()},
    }
    if slices:
        report["extraction"] = {k: {"exact": c.prf(False), "normalized": c.prf(True), "counts": c.__dict__}
                                for k, c in sorted(slices.items())}
        report["calibration"] = {"all_fields": metrics.calibration(scored),
                                 "critical_fields": metrics.calibration(critical_scored)}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{pack_name}__{config}.json").write_text(json.dumps(report, indent=1, default=str))
    return report


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.WARNING, format="  %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pack", required=True, choices=sorted(TAXONOMY))
    ap.add_argument("--config", required=True, choices=sorted(CONFIGS))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--tier", action="append", choices=["clean", "scanned", "hard"])
    ap.add_argument("--score-only", action="store_true")
    ap.add_argument("--results-dir", type=Path, default=None, help="write runs/scores here (e.g. the CI gate)")
    ap.add_argument("--dataset", choices=sorted(DATASETS), default="test",
                    help="test = frozen seed-7 set (README); dev = seed-1 tuning set; real = labelled phone photos")
    args = ap.parse_args()
    use_dataset(args.dataset)
    if args.results_dir:
        global RESULTS, RUNS
        RESULTS = args.results_dir
        RUNS = RESULTS / "runs"
    if not args.score_only:
        run(args.pack, args.config, args.limit, set(args.tier) if args.tier else None)
    rep = score(args.pack, args.config)
    micro = rep["detection"]["micro"]
    f1 = rep.get("extraction", {}).get("all", {}).get("normalized", {}).get("f1")
    print(f"[{args.pack}/{args.config}] pairs={rep['n_pairs']} discrepancy P={_fmt(micro['precision'])} "
          f"R={_fmt(micro['recall'])} false-alarm={_fmt(micro['false_alarm_rate'])} "
          f"decision acc={_fmt(rep['decision']['accuracy'])} extraction F1(norm)={_fmt(f1)}")


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.3f}"


if __name__ == "__main__":
    main()
