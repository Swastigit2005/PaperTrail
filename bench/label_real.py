"""Label phone photos of printed documents (with fake data) for the real-photo benchmark.

Drop photos into ``bench/data/real_fake/<pack>/<pair_id>/`` named after the document type, e.g.

    bench/data/real_fake/claims/rp-001/prescription.jpg
    bench/data/real_fake/claims/rp-001/bill.jpg

then run

    python -m bench.label_real --pack claims            # label every unlabelled pair
    python -m bench.label_real --pack claims --prefill  # start from the model's extraction (you correct it)

For every field you type the value as printed (Enter keeps the shown value, '-' clears it).
Labels go to ``bench/data/real_fake/<pack>/manifest.json`` in the same format as the synthetic set,
so ``run_benchmark --dataset real`` can score them. Use fake details only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from papertrail.core.fields import Field
from papertrail.core.pack import get_pack

ROOT = Path(__file__).resolve().parent / "data" / "real_fake"
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".pdf")


def _ask(prompt: str, default: Any) -> Any:
    shown = "" if default is None else f" [{default}]"
    raw = input(f"  {prompt}{shown}: ").strip()
    if raw == "":
        return default
    if raw == "-":
        return None
    try:
        return float(raw) if raw.replace(".", "", 1).replace("-", "", 1).isdigit() else raw
    except ValueError:
        return raw


def label_model(schema: type[BaseModel], prefill: dict[str, Any], indent: str = "") -> dict[str, Any]:
    """Interactively collect plain values for every field of ``schema``."""
    out: dict[str, Any] = {}
    for name, info in schema.model_fields.items():
        tp = info.annotation
        if isinstance(tp, type) and issubclass(tp, Field):
            out[name] = _ask(f"{indent}{name} ({info.description})", prefill.get(name))
        else:  # list of line models
            item_schema = tp.__args__[0]
            items = prefill.get(name) or []
            n = int(_ask(f"{indent}number of {name}", len(items)) or 0)
            out[name] = []
            for i in range(n):
                print(f"{indent}  {name}[{i}]")
                out[name].append(label_model(item_schema, items[i] if i < len(items) else {}, indent + "    "))
    return out


def _plain(data: Any) -> Any:
    """Engine JSON (Field dicts) -> plain values, for prefilling."""
    if isinstance(data, dict) and "value" in data and "confidence" in data:
        return data["value"]
    if isinstance(data, dict):
        return {k: _plain(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_plain(v) for v in data]
    return data


def prefill_from_model(pack_name: str, doc_type: str, image: Path) -> dict[str, Any]:
    """Run the VLM extractor once to give the labeller a starting point."""
    from papertrail.config import get_settings
    from papertrail.core.extraction import build_extractor
    from papertrail.core.images import load_document
    from papertrail.core.llm import LLMClient

    settings = get_settings()
    extractor = build_extractor("vlm", LLMClient(settings), settings)
    res = extractor.extract(load_document(image.read_bytes(), name=image.name), get_pack(pack_name).doc_type(doc_type))
    return _plain(res.data.model_dump(mode="json"))


def main() -> None:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pack", required=True, choices=["claims", "payables", "expenses"])
    ap.add_argument("--prefill", action="store_true", help="start from a model extraction (uses Groq quota)")
    args = ap.parse_args()
    pack = get_pack(args.pack)
    pack_dir = ROOT / args.pack
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
        "pack": args.pack, "seed": None, "reference_date": None, "pairs": []}
    labelled = {p["pair_id"] for p in manifest["pairs"]}
    folders = sorted(d for d in pack_dir.iterdir() if d.is_dir()) if pack_dir.exists() else []
    if not folders:
        print(f"No pair folders in {pack_dir}. Create {pack_dir}/<pair_id>/<doc_type>.jpg first.")
        return
    for folder in folders:
        if folder.name in labelled:
            continue
        print(f"\n=== {folder.name}")
        documents = {}
        for dt in pack.doc_types:
            image = next((f for f in folder.iterdir() if f.stem == dt.name and f.suffix.lower() in IMAGE_EXT), None)
            if image is None:
                print(f"  missing {dt.name}.* in {folder}; skipping pair")
                break
            print(f"-- {dt.title}: {image.name}")
            prefill = prefill_from_model(args.pack, dt.name, image) if args.prefill else {}
            documents[dt.name] = {"image": f"{folder.name}/{image.name}", "template": "real_photo",
                                  "degradation": {"tier": "real"}, "occluded": [],
                                  "truth": label_model(dt.schema, prefill)}
        else:
            kinds = _ask("seeded discrepancy types, comma-separated (empty if none)", "") or ""
            discrepancies = [{"type": k.strip(), "fields": [], "detail": "labelled by hand"}
                             for k in str(kinds).split(",") if k.strip()]
            manifest["pairs"].append({"pair_id": folder.name, "pack": args.pack, "tier": "real",
                                      "discrepancies": discrepancies,
                                      "expected_outcome": "flag" if discrepancies else "approve",
                                      "documents": documents})
            manifest["count"] = len(manifest["pairs"])
            manifest_path.write_text(json.dumps(manifest, indent=1))
            print(f"saved {folder.name}")


if __name__ == "__main__":
    main()
