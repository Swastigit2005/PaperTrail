"""Fail (exit 1) if a gate run falls below bench/thresholds.json.

    python -m bench.check_thresholds --results-dir bench/gate_results --config vlm_llm
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

THRESHOLDS = Path(__file__).resolve().parent / "thresholds.json"


def main() -> None:
    """Compare discrepancy recall and extraction F1 per pack against the stored floors."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results-dir", type=Path, required=True)
    ap.add_argument("--config", default="vlm_llm")
    args = ap.parse_args()
    floors = json.loads(THRESHOLDS.read_text())
    failed = False
    for pack, floor in floors["packs"].items():
        report = json.loads((args.results_dir / f"{pack}__{args.config}.json").read_text())
        recall = report["detection"]["micro"]["recall"] or 0.0
        f1 = report["extraction"]["all"]["normalized"]["f1"] or 0.0
        for name, value, minimum in (("discrepancy recall", recall, floor["discrepancy_recall"]),
                                     ("extraction F1", f1, floor["extraction_f1"])):
            ok = value >= minimum
            failed |= not ok
            print(f"{'PASS' if ok else 'FAIL'} {pack} {name}: {value:.3f} (floor {minimum:.3f})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
