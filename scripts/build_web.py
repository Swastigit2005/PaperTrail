"""Assemble the static site's data files into public/ (run at deploy time and before local serving).

Copies, never invents: sample results from src/papertrail/packs/*/samples, benchmark report and
charts from bench/results, the pack guide from docs/, and the pack catalogue from the registry.

    python scripts/build_web.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from papertrail.core.pack import list_packs  # noqa: E402

PUBLIC = ROOT / "public"


def _copy_tree(src: Path, dst: Path, patterns: tuple[str, ...]) -> int:
    n = 0
    for pattern in patterns:
        for f in src.glob(pattern):
            if f.is_file():
                target = dst / f.relative_to(src)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)
                n += 1
    return n


EXAMPLES_README = """# Example documents (synthetic)

Every file here is synthetic: fake people, companies, GSTINs and amounts. Drag a pair into the PaperTrail demo's
upload slots (or use "Use pair" in the demo). Because these exact files are bundled with the demo, their
extraction is precomputed with the live engine: they work instantly and never use model quota.

"""


def write_examples(index: dict[str, list]) -> None:
    """Friendly-named copies in examples/ (repo root) and public/examples/<pack>.zip for download."""
    root = ROOT / "examples"
    shutil.rmtree(root, ignore_errors=True)
    (PUBLIC / "examples").mkdir(parents=True, exist_ok=True)
    lines = [EXAMPLES_README]
    for pack, entries in sorted(index.items()):
        pack_dir = root / pack
        pack_dir.mkdir(parents=True)
        pack_lines = [EXAMPLES_README, f"## {pack}\n"]
        lines.append(f"## {pack}\n")
        zpath = PUBLIC / "examples" / f"{pack}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as zf:
            for n, e in enumerate(entries, 1):
                expected = ", ".join(x.replace("_", " ") for x in e["seeded"]) or "nothing (should approve)"
                lines.append(f"{n}. **{e['title']}**: seeded issue: {expected}.")
                pack_lines.append(lines[-1])
                for dt, f in e["documents"].items():
                    name = f"{pack}-{n:02d}-{e['id']}-{f}"
                    src = PUBLIC / "samples" / pack / e["id"] / f
                    shutil.copy2(src, pack_dir / name)
                    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                    zf.writestr(info, src.read_bytes())
                    lines.append(f"   - `{pack}/{name}` ({dt.replace('_', ' ')})")
                    pack_lines.append(f"   - `{name}` ({dt.replace('_', ' ')})")
            zf.writestr(zipfile.ZipInfo("README.md", date_time=(2026, 1, 1, 0, 0, 0)), "\n".join(pack_lines))
        lines.append("")
    (root / "README.md").write_text("\n".join(lines))


def main() -> None:
    """Write public/data, public/samples, public/benchmark and public/docs."""
    for sub in ("data", "samples", "benchmark", "docs"):
        shutil.rmtree(PUBLIC / sub, ignore_errors=True)
        (PUBLIC / sub).mkdir(parents=True)

    packs = [
        {"name": p.name, "title": p.title, "description": p.description, "benchmarked": p.benchmarked,
         "doc_types": [{"name": d.name, "title": d.title, "description": d.description} for d in p.doc_types],
         "checks": [{"code": c.code, "description": c.description} for c in p.crosschecks()]}
        for p in sorted(list_packs(), key=lambda p: (not p.benchmarked, p.name))
    ]
    (PUBLIC / "data" / "packs.json").write_text(json.dumps(packs, indent=1))
    repo = os.getenv("PAPERTRAIL_REPO_URL", "")  # set once the GitHub repo exists
    (PUBLIC / "data" / "site.json").write_text(json.dumps({"repo_url": repo}))

    index: dict[str, list] = {}
    for p in list_packs():
        manifest = p.samples_dir / "manifest.json"
        if not manifest.exists():
            continue
        entries = [e for e in json.loads(manifest.read_text())["samples"]
                   if (p.samples_dir / e["id"] / "result.json").exists()]
        for e in entries:  # sha256 of the exact file bytes: lets the demo recognise a dropped example
            e["hashes"] = {dt: hashlib.sha256((p.samples_dir / e["id"] / f).read_bytes()).hexdigest()
                           for dt, f in e["documents"].items()}
        index[p.name] = entries
        _copy_tree(p.samples_dir, PUBLIC / "samples" / p.name, ("*/*.jpg", "*/result.json"))
    (PUBLIC / "samples" / "index.json").write_text(json.dumps(index, indent=1))
    write_examples(index)

    results = ROOT / "bench" / "results"
    n = _copy_tree(results, PUBLIC / "benchmark", ("BENCHMARK.md", "*.png", "failures/*.jpg"))
    # slim copies of the scored results for the plain-language "How accurate is it?" page
    summary = {}
    for f in sorted(results.glob("*__*.json")):
        r = json.loads(f.read_text())
        summary[f.stem] = {k: r.get(k) for k in ("pack", "config", "pipeline", "matching", "n_pairs", "dataset",
                                                 "detection", "decision", "ops", "validation_catch_rate")}
        summary[f.stem]["detection"] = {"micro": r["detection"]["micro"]}
        summary[f.stem]["extraction_f1"] = r.get("extraction", {}).get("all", {}).get("normalized", {}).get("f1")
    (PUBLIC / "benchmark" / "summary.json").write_text(json.dumps(summary, indent=1))
    shutil.copy2(ROOT / "docs" / "ADDING_A_PACK.md", PUBLIC / "docs" / "ADDING_A_PACK.md")
    print(f"public/: {len(packs)} packs, {sum(len(v) for v in index.values())} samples, {n} benchmark files")


if __name__ == "__main__":
    main()
