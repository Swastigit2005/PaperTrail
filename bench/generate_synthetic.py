"""Generate synthetic document pairs with exact ground truth and seeded discrepancies.

    python -m bench.generate_synthetic --seed 7 --claims 60 --payables 60

Writes ``bench/data/synthetic/<pack>/manifest.json`` plus one folder of images per pair.
Reproducible: the same seed produces the same documents, truth and images.
All names, companies, GSTINs and numbers are fake.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

from faker import Faker

from bench.synth import claims, payables
from bench.synth.degrade import TIERS, degrade, occlude

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "synthetic"
REFERENCE_DATE = "2026-10-06"  # "today" for date-sanity checks; fixed so results are reproducible
TIER_SHARE = {"clean": 0.40, "scanned": 0.35, "hard": 0.25}
HARD_OCCLUSION_RATE = 0.6

PACKS: dict[str, Any] = {
    "claims": {
        "module": claims,
        "docs": (("prescription", claims.render_prescription, "rx"), ("bill", claims.render_bill, "bill")),
    },
    "payables": {
        "module": payables,
        "docs": (("invoice", payables.render_invoice, "invoice"), ("purchase_order", payables.render_po, "po")),
    },
}


def _strip_private(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_private(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [_strip_private(v) for v in value]
    return value


def plan(n: int, types: tuple[str, ...], rng: random.Random) -> list[tuple[str, list[str]]]:
    """Stratified tiers and a balanced discrepancy schedule: ~50% clean, the rest 1-2 types."""
    tiers = [t for t in TIERS for _ in range(round(n * TIER_SHARE[t]))]
    tiers = (tiers + ["scanned"] * n)[:n]
    rng.shuffle(tiers)
    n_bad = n // 2
    # deal types from consecutive shuffled permutations so every type gets an (almost) equal share
    cycle: list[str] = []
    while len(cycle) < 2 * n:
        perm = list(types)
        rng.shuffle(perm)
        cycle.extend(perm)
    disc: list[list[str]] = [[] for _ in range(n - n_bad)]
    for _ in range(n_bad):
        first = cycle.pop(0)
        chosen = [first]
        if rng.random() < 0.3 and cycle[0] != first:
            chosen.append(cycle.pop(0))
        disc.append(chosen)
    rng.shuffle(disc)
    return list(zip(tiers, disc, strict=True))


def generate_pack(pack: str, n: int, seed: int, out: Path) -> dict[str, Any]:
    """Generate ``n`` pairs for one pack and write images + manifest."""
    spec = PACKS[pack]
    rng = random.Random(f"{seed}-{pack}")
    fake = Faker("en_IN")
    fake.seed_instance(seed)
    pack_dir = out / pack
    pack_dir.mkdir(parents=True, exist_ok=True)
    pairs = []
    for idx, (tier, kinds) in enumerate(plan(n, spec["module"].DISCREPANCIES, rng)):
        pair_id = f"{pack[:3]}-{idx:03d}"
        case = spec["module"].build_case(rng, fake, kinds)
        truths = {"prescription": case.rx, "bill": case.bill} if pack == "claims" else {
            "invoice": case.invoice, "purchase_order": case.po}
        seeded_fields = {f for d in case.discrepancies for f in d["fields"]}
        documents = {}
        (pack_dir / pair_id).mkdir(exist_ok=True)
        for doc_type, render, stem in spec["docs"]:
            canvas, template = render(truths[doc_type], rng)
            occluded: list[str] = []
            if tier == "hard" and rng.random() < HARD_OCCLUSION_RATE:
                candidates = [p for p in canvas.boxes if f"{doc_type}.{p}" not in seeded_fields]
                if candidates:
                    path = rng.choice(sorted(candidates))
                    occlude(canvas.img, canvas.boxes[path], rng)
                    occluded.append(path)
            img, params = degrade(canvas.img, tier, rng)
            ext = "png" if tier == "clean" else "jpg"
            rel = f"{pair_id}/{stem}.{ext}"
            if ext == "png":
                img.save(pack_dir / rel, optimize=True)
            else:
                img.save(pack_dir / rel, quality=92)
            documents[doc_type] = {"image": rel, "template": template, "degradation": params, "occluded": occluded,
                                   "truth": _strip_private(truths[doc_type])}
        pairs.append({
            "pair_id": pair_id, "pack": pack, "tier": tier, "discrepancies": case.discrepancies,
            "expected_outcome": "flag" if case.discrepancies else "approve", "documents": documents,
        })
    manifest = {"pack": pack, "seed": seed, "reference_date": REFERENCE_DATE, "count": len(pairs), "pairs": pairs}
    (pack_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def main() -> None:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--claims", type=int, default=60)
    ap.add_argument("--payables", type=int, default=60)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    for pack, n in (("claims", args.claims), ("payables", args.payables)):
        if n:
            m = generate_pack(pack, n, args.seed, args.out)
            tiers = {t: sum(p["tier"] == t for p in m["pairs"]) for t in TIERS}
            kinds: dict[str, int] = {}
            for p in m["pairs"]:
                for d in p["discrepancies"]:
                    kinds[d["type"]] = kinds.get(d["type"], 0) + 1
            clean = sum(not p["discrepancies"] for p in m["pairs"])
            print(f"{pack}: {m['count']} pairs, {clean} without discrepancies, tiers {tiers}, seeded {kinds}")


if __name__ == "__main__":
    main()
