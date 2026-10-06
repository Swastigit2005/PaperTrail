"""Assemble the static site's data files into public/ (run at deploy time and before local serving).

Copies, never invents: sample results from src/papertrail/packs/*/samples, benchmark report and
charts from bench/results, the pack guide from docs/, and the pack catalogue from the registry.

    python scripts/build_web.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
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
        index[p.name] = entries
        _copy_tree(p.samples_dir, PUBLIC / "samples" / p.name, ("*/*.jpg", "*/result.json"))
    (PUBLIC / "samples" / "index.json").write_text(json.dumps(index, indent=1))

    results = ROOT / "bench" / "results"
    n = _copy_tree(results, PUBLIC / "benchmark", ("BENCHMARK.md", "*.png", "failures/*.jpg"))
    shutil.copy2(ROOT / "docs" / "ADDING_A_PACK.md", PUBLIC / "docs" / "ADDING_A_PACK.md")
    print(f"public/: {len(packs)} packs, {sum(len(v) for v in index.values())} samples, {n} benchmark files")


if __name__ == "__main__":
    main()
