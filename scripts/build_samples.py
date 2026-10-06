"""Precompute the demo's sample pairs: render documents, run the real engine once, save everything.

    python scripts/build_samples.py            # uses Groq (vision model) once per sample document
    python scripts/build_samples.py --render-only

Writes src/papertrail/packs/<pack>/samples/<id>/{<doc>.jpg, result.json} and samples/manifest.json.
Samples come from a dedicated seed (not the benchmark test set). All data is synthetic.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

from faker import Faker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from bench.synth import claims, expenses, payables  # noqa: E402
from bench.synth.degrade import degrade  # noqa: E402
from papertrail.config import get_settings  # noqa: E402
from papertrail.core.crosscheck import CheckContext  # noqa: E402
from papertrail.core.engine import evaluate  # noqa: E402
from papertrail.core.extraction import VLMDirectExtractor  # noqa: E402
from papertrail.core.images import load_document  # noqa: E402
from papertrail.core.llm import LLMClient  # noqa: E402
from papertrail.core.matching import Matcher  # noqa: E402
from papertrail.core.pack import get_pack  # noqa: E402
from papertrail.core.validators.duplicates import DuplicateIndex  # noqa: E402
from papertrail.core.validators.rxnorm import RxNormClient  # noqa: E402

SEED = 2026
SAMPLES: dict[str, list[dict[str, Any]]] = {
    "claims": [
        {"id": "clean", "title": "Clean claim (brand vs generic names)", "kinds": [], "tier": "clean"},
        {"id": "unprescribed", "title": "Bill includes a medicine that was not prescribed",
         "kinds": ["unprescribed_medicine"], "tier": "scanned"},
        {"id": "quantity", "title": "Billed quantity far exceeds the prescribed course",
         "kinds": ["implausible_quantity", "bill_before_prescription"], "tier": "clean"},
    ],
    "payables": [
        {"id": "clean", "title": "Clean invoice (paraphrased lines)", "kinds": [], "tier": "clean"},
        {"id": "overbilled", "title": "Rate above PO and quantity over PO", "kinds": ["rate_above_po", "qty_over_po"],
         "tier": "scanned"},
        {"id": "tax", "title": "Wrong tax split and invalid GSTIN", "kinds": ["wrong_tax_split", "invalid_gstin"],
         "tier": "clean"},
    ],
    "expenses": [
        {"id": "clean", "title": "Clean meal claim", "extra": 0.0, "tier": "clean"},
        {"id": "inflated", "title": "Claimed amount exceeds the receipt", "extra": 350.0, "tier": "scanned"},
        {"id": "duplicate", "title": "Same receipt claimed again (duplicate)", "extra": 0.0, "tier": "scanned",
         "duplicate_of": "clean"},
    ],
}
RENDER = {"claims": (("prescription", claims.render_prescription), ("bill", claims.render_bill)),
          "payables": (("invoice", payables.render_invoice), ("purchase_order", payables.render_po))}


def render_pack(pack: str, out: Path, rng: random.Random, fake: Faker) -> list[dict[str, Any]]:
    """Render every sample of a pack; returns manifest entries with truth."""
    entries = []
    receipts: dict[str, Any] = {}
    for spec in SAMPLES[pack]:
        folder = out / spec["id"]
        folder.mkdir(parents=True, exist_ok=True)
        if pack == "expenses":
            if spec.get("duplicate_of"):
                truth, canvases = receipts[spec["duplicate_of"]]
                truth = {"receipt": truth["receipt"], "expense_claim": dict(truth["expense_claim"],
                         claim_date=truth["expense_claim"]["claim_date"])}
                canvases = {"receipt": canvases["receipt"], "expense_claim": expenses.render_claim(
                    truth["expense_claim"], rng)}
            else:
                truth = expenses.build_case(rng, fake, spec["extra"])
                canvases = {"receipt": expenses.render_receipt(truth["receipt"], rng),
                            "expense_claim": expenses.render_claim(truth["expense_claim"], rng)}
                receipts[spec["id"]] = (truth, canvases)
            discrepancies = (["claim_amount_mismatch"] if spec["extra"] else []) + \
                (["duplicate_receipt"] if spec.get("duplicate_of") else [])
        else:
            module = claims if pack == "claims" else payables
            case = module.build_case(rng, fake, list(spec["kinds"]))
            truth = {"prescription": case.rx, "bill": case.bill} if pack == "claims" else {
                "invoice": case.invoice, "purchase_order": case.po}
            canvases = {dt: fn(truth[dt], rng)[0] for dt, fn in RENDER[pack]}
            discrepancies = [d["type"] for d in case.discrepancies]
        docs = {}
        for dt, canvas in canvases.items():
            img, _ = degrade(canvas.img, spec["tier"], rng)
            img.thumbnail((1400, 1400))
            img.save(folder / f"{dt}.jpg", quality=88)
            docs[dt] = f"{dt}.jpg"
        entries.append({"id": spec["id"], "title": spec["title"], "tier": spec["tier"], "documents": docs,
                        "seeded": discrepancies, "duplicate_of": spec.get("duplicate_of"),
                        "expected": "flag" if discrepancies else "approve"})
    return entries


def run_engine(pack_name: str, out: Path, entries: list[dict[str, Any]]) -> None:
    """Extract (VLM) + evaluate (rules+LLM) every sample once and store the result JSON."""
    settings = get_settings()
    llm = LLMClient(settings)
    extractor = VLMDirectExtractor(llm, settings.require("vision_model"))
    pack = get_pack(pack_name)
    rxnorm = RxNormClient(cache_dir=settings.cache_dir) if pack_name == "claims" else None
    history = DuplicateIndex()
    for e in entries:
        folder = out / e["id"]
        docs, extracted, images = {}, {}, {}
        for dt, fname in e["documents"].items():
            image = load_document((folder / fname).read_bytes(), name=fname, max_side=settings.max_image_side)
            res = extractor.extract(image, pack.doc_type(dt))
            docs[dt] = res.data
            images[dt] = image
            extracted[dt] = {"doc_type": dt, "title": pack.doc_type(dt).title,
                             "detection": {"method": "override", "confidence": 1.0},
                             "data": res.data.model_dump(mode="json"), "pipeline": res.pipeline, "model": res.model,
                             "latency_s": round(res.latency_s, 2), "usage": res.usage, "cached": False,
                             "image": {"sha256": image.sha256, "dhash": f"{image.dhash:016x}"}}
        before = len(history._entries)
        ctx = CheckContext(matcher=Matcher("rules+llm", llm, settings.match_model), case_id=f"sample-{e['id']}",
                           images=images, history=history, services={"rxnorm": rxnorm} if rxnorm else {})
        validations, outcomes, decision = evaluate(pack, docs, ctx)
        result = {"pack": pack_name, "sample": e["id"], "pipeline": "vlm", "matching": "rules+llm",
                  "documents": extracted,
                  "evaluation": {"validations": [f.model_dump(mode="json") for f in validations],
                                 "crosschecks": [o.model_dump(mode="json") for o in outcomes],
                                 "decision": decision.model_dump(mode="json"), "matching": "rules+llm", "notes": []},
                  "history_before": [{"case_id": c, "dhash": f"{h:016x}" if h is not None else None, "fingerprint": f}
                                     for c, h, f in history._entries[:before]]}
        (folder / "result.json").write_text(json.dumps(result, indent=1))
        print(f"{pack_name}/{e['id']}: {decision.outcome.value} ({len(decision.reasons)} reasons)")


def main() -> None:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--pack", choices=sorted(SAMPLES), action="append")
    args = ap.parse_args()
    for pack in args.pack or sorted(SAMPLES):
        rng = random.Random(f"{SEED}-{pack}")
        fake = Faker("en_IN")
        fake.seed_instance(SEED)
        out = ROOT / "src" / "papertrail" / "packs" / pack / "samples"
        entries = render_pack(pack, out, rng, fake)
        (out / "manifest.json").write_text(json.dumps({"pack": pack, "samples": entries}, indent=1))
        if not args.render_only:
            run_engine(pack, out, entries)


if __name__ == "__main__":
    main()
