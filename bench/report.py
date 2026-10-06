"""Build bench/results/BENCHMARK.md and charts from the scored runs. Never hard-codes a number.

    python -m bench.report

Anything not yet run shows as TBD. Failure-pattern diagnoses are written by a human after looking
at the examples, in bench/results/failure_notes.json (pattern key -> one line); patterns without a
note show an automatic description.
"""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from PIL import Image  # noqa: E402

from bench import metrics  # noqa: E402
from bench.run_benchmark import DATA, RESULTS, RUNS, TAXONOMY  # noqa: E402

PACKS = ("claims", "payables")
REAL = ("vlm_rules", "vlm_llm", "vlmb_llm", "ocr_llm")
ORACLE_FOR = {"vlm_rules": "oracle_rules", "vlm_llm": "oracle_llm", "vlmb_llm": "oracle_llm", "ocr_llm": "oracle_llm"}
LABEL = {"vlm_rules": "VLM direct + rules", "vlm_llm": "VLM direct + rules+LLM", "vlmb_llm": "VLM B + rules+LLM",
         "ocr_llm": "OCR+LLM + rules+LLM", "oracle_rules": "Oracle (ground truth) + rules",
         "oracle_llm": "Oracle (ground truth) + rules+LLM"}
# reference palette slots 1-4 (validated: adjacent CVD dE >= 9.1, normal-vision >= 22.9 on #fcfcfb)
SERIES = {"vlm_rules": "#2a78d6", "vlm_llm": "#eb6834", "ocr_llm": "#1baf7a", "oracle_llm": "#eda100"}
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
FAIL_DIR = RESULTS / "failures"


def load(pack: str, config: str) -> dict[str, Any] | None:
    """A scored result, or None if that run does not exist yet."""
    path = RESULTS / f"{pack}__{config}.json"
    return json.loads(path.read_text()) if path.exists() else None


def pct(v: float | None) -> str:
    """0.873 -> '87.3%'; None -> 'TBD'."""
    return "TBD" if v is None else f"{v * 100:.1f}%"


def num(v: float | None, fmt: str = "{:.2f}") -> str:
    """Format a number or TBD."""
    return "TBD" if v is None else fmt.format(v)


def _f1(r: dict[str, Any] | None, key: str = "all", mode: str = "normalized") -> float | None:
    return (r or {}).get("extraction", {}).get(key, {}).get(mode, {}).get("f1")


def headline(pack: str) -> list[str]:
    """One headline table per pack."""
    rows = ["| Configuration | Pairs | Extraction F1 (exact / norm.) | Discrepancy precision | Discrepancy recall | "
            "False alarms on clean pairs | Decision accuracy | Tokens / pair | Model latency p50 / p95 |",
            "|---|---|---|---|---|---|---|---|---|"]
    for config in (*REAL, "oracle_rules", "oracle_llm"):
        r = load(pack, config)
        if r is None:
            note = " (no second vision model available on Groq)" if config == "vlmb_llm" else ""
            rows.append(f"| {LABEL[config]}{note} | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |")
            continue
        micro, ops = r["detection"]["micro"], r["ops"]
        oracle = r["pipeline"] == "oracle"
        f1 = "n/a (ground truth)" if oracle else f"{pct(_f1(r, mode='exact'))} / {pct(_f1(r))}"
        tokens = "n/a" if oracle and r["matching"] == "rules" else num(ops["tokens_per_pair_mean"], "{:,.0f}")
        latency = "n/a" if oracle else f"{num(ops['model_latency_p50_s'], '{:.1f}s')} / " \
                                        f"{num(ops['model_latency_p95_s'], '{:.1f}s')}"
        partial = f" of {r['dataset']['total_pairs']}" if r["n_pairs"] < r["dataset"]["total_pairs"] else ""
        rows.append(f"| {LABEL[config]} | {r['n_pairs']}{partial} | {f1} | {pct(micro['precision'])} | "
                    f"{pct(micro['recall'])} | {pct(micro['false_alarm_rate'])} | {pct(r['decision']['accuracy'])} | "
                    f"{tokens} | {latency} |")
    return rows


def per_type_table(pack: str) -> list[str]:
    """Recall (and precision) per discrepancy type for every config that has run."""
    configs = [c for c in (*REAL, "oracle_rules", "oracle_llm") if load(pack, c)]
    if not configs:
        return ["TBD"]
    rows = ["| Discrepancy type | Seeded | " + " | ".join(f"{LABEL[c]} R / P" for c in configs) + " |",
            "|---|---|" + "---|" * len(configs)]
    for t in TAXONOMY[pack]:
        cells = []
        support = None
        for c in configs:
            pt = load(pack, c)["detection"]["per_type"][t]  # type: ignore[index]
            support = pt["support"] if c.startswith("oracle") else support
            cells.append(f"{pct(pt['recall'])} / {pct(pt['precision'])}")
        rows.append(f"| `{t}` | {support if support is not None else 'TBD'} | " + " | ".join(cells) + " |")
    return rows


def recall_chart(pack: str) -> str | None:
    """Grouped horizontal bars: recall per discrepancy type, one series per config (max 4)."""
    configs = [c for c in SERIES if load(pack, c)]
    if not configs:
        return None
    types = list(TAXONOMY[pack])
    fig_h = 0.62 * len(types) * max(2, len(configs)) / 2 + 1.4
    fig, ax = plt.subplots(figsize=(8.5, fig_h), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    bar_h = 0.8 / len(configs)
    for k, c in enumerate(configs):
        r = load(pack, c)
        vals = [(r["detection"]["per_type"][t]["recall"] or 0.0) for t in types]  # type: ignore[index]
        ys = [i + (k - (len(configs) - 1) / 2) * bar_h for i in range(len(types))]
        ax.barh(ys, vals, height=bar_h * 0.86, color=SERIES[c], label=LABEL[c], edgecolor=SURFACE, linewidth=1.5)
        for y, v in zip(ys, vals, strict=True):
            ax.text(v + 0.01, y, f"{v * 100:.0f}%", va="center", ha="left", fontsize=7, color=INK2)
    ax.set_yticks(range(len(types)))
    ax.set_yticklabels([t.replace("_", " ") for t in types], fontsize=9, color=INK)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.12)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_xticklabels(["0%", "25%", "50%", "75%", "100%"], fontsize=8, color=INK2)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(length=0)
    ax.set_title(f"{pack.capitalize()}: recall per discrepancy type", loc="left", fontsize=11, color=INK, pad=12)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0 + 0.35 / fig_h), ncol=2, frameon=False, fontsize=8,
              labelcolor=INK2)
    fig.tight_layout()
    out = RESULTS / f"recall_{pack}.png"
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)
    return out.name


def attribution(pack: str) -> list[str]:
    """Real vs oracle (same matching mode): how many detection errors come from extraction vs logic."""
    rows = ["| Configuration | Missed: extraction | Missed: matching/logic | False alarm: extraction | "
            "False alarm: matching/logic |", "|---|---|---|---|---|"]
    any_row = False
    for c in REAL:
        r, o = load(pack, c), load(pack, ORACLE_FOR[c])
        if not r or not o:
            continue
        real = {k: (set(v["expected"]), set(v["predicted"])) for k, v in r["per_pair"].items()}
        orc = {k: (set(v["expected"]), set(v["predicted"])) for k, v in o["per_pair"].items()}
        a = metrics.attribute_errors(real, orc)
        rows.append(f"| {LABEL[c]} (vs {LABEL[ORACLE_FOR[c]]}) | {a['missed_extraction']} | {a['missed_logic']} | "
                    f"{a['false_extraction']} | {a['false_logic']} |")
        any_row = True
    return rows if any_row else ["TBD"]


def extraction_table(pack: str) -> list[str]:
    """Normalised F1 by document type x tier for the real configs."""
    configs = [c for c in REAL if load(pack, c)]
    if not configs:
        return ["TBD"]
    doc_types = sorted({k.split("|")[0] for k in load(pack, configs[0])["extraction"] if "|" in k and  # type: ignore
                        not k.startswith("tier")})
    rows = ["| Document | Tier | " + " | ".join(LABEL[c] for c in configs) + " |", "|---|---|" + "---|" * len(configs)]
    for dt in doc_types:
        for tier in ("clean", "scanned", "hard"):
            cells = [pct(_f1(load(pack, c), f"{dt}|{tier}")) for c in configs]
            rows.append(f"| {dt} | {tier} | " + " | ".join(cells) + " |")
    return rows


def calibration_table(pack: str) -> list[str]:
    """Does confidence separate right from wrong fields (critical fields)?"""
    configs = [c for c in REAL if load(pack, c)]
    if not configs:
        return ["TBD"]
    rows = ["| Configuration | Critical fields scored | Accuracy when conf >= 0.75 | Accuracy when conf < 0.75 | "
            "Share of wrong fields caught by low confidence |", "|---|---|---|---|---|"]
    for c in configs:
        cal = load(pack, c)["calibration"]["critical_fields"]  # type: ignore[index]
        rows.append(f"| {LABEL[c]} | {cal['n_fields']} | {pct(cal['high_conf_accuracy'])} | "
                    f"{pct(cal['low_conf_accuracy'])} | {pct(cal['errors_flagged_low_conf'])} |")
    return rows


def decision_tables(pack: str) -> list[str]:
    """Confusion matrix (expected -> predicted) per real config."""
    out: list[str] = []
    for c in (*REAL, "oracle_llm"):
        r = load(pack, c)
        if not r:
            continue
        d = r["decision"]
        out += [f"**{LABEL[c]}**: accuracy {pct(d['accuracy'])}; discrepant pairs not auto-approved "
                f"{pct(d['discrepant_not_auto_approved'])}; clean pairs auto-approved {pct(d['clean_auto_approved'])}; "
                f"clean pairs flagged {pct(d['clean_flagged'])}", "",
                "| expected \\ predicted | approve | needs_review | flag |", "|---|---|---|---|"]
        for e in ("approve", "flag"):
            m = d["confusion"][e]
            out.append(f"| {e} | {m['approve']} | {m['needs_review']} | {m['flag']} |")
        out.append("")
    return out or ["TBD"]


def validation_line(pack: str) -> str:
    """Validator catch rates from the best available real config."""
    for c in ("vlm_llm", "ocr_llm", "vlm_rules"):
        r = load(pack, c)
        if r:
            parts = [f"`{k}` {pct(v)}" for k, v in r["validation_catch_rate"].items()]
            return f"Field-validation catch rate ({LABEL[c]}): " + ", ".join(parts)
    return "Field-validation catch rate: TBD"


# --- failure patterns ---------------------------------------------------------------------------


def _pred_value(doc: dict[str, Any] | None, path: str) -> Any:
    cur: Any = doc or {}
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if part.startswith("["):
            idx = int(part[1:-1])
            cur = cur[idx] if isinstance(cur, list) and idx < len(cur) else None
        else:
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None:
            return None
    return cur.get("value") if isinstance(cur, dict) and "value" in cur else cur


def failure_patterns(limit: int = 10) -> list[dict[str, Any]]:
    """Group errors from the real runs into patterns, ranked by frequency."""
    patterns: dict[str, dict[str, Any]] = {}

    def add(key: str, desc: str, example: dict[str, Any]) -> None:
        p = patterns.setdefault(key, {"key": key, "description": desc, "count": 0, "examples": []})
        p["count"] += 1
        if len(p["examples"]) < 3:
            p["examples"].append(example)

    for pack in PACKS:
        manifest = json.loads((DATA / pack / "manifest.json").read_text())
        by_id = {p["pair_id"]: p for p in manifest["pairs"]}
        oracle = load(pack, "oracle_llm")
        for config in ("vlm_llm", "ocr_llm"):
            path = RUNS / f"{pack}__{config}.jsonl"
            if not path.exists():
                continue
            for line in path.read_text().splitlines():
                rec = json.loads(line)
                pair = by_id[rec["pair_id"]]
                expected = set(rec["expected"])
                predicted = metrics.detected_types(rec["findings"], TAXONOMY[pack])
                o_pred = set((oracle or {}).get("per_pair", {}).get(rec["pair_id"], {}).get("predicted", []))
                for t in expected - predicted:
                    src = "logic" if t not in o_pred else "extraction"
                    add(f"{pack}|{config}|missed|{t}|{src}", f"{LABEL[config]} missed `{t}` ({src} error)",
                        {"pair": rec["pair_id"], "tier": rec["tier"], "doc": None})
                for t in predicted - expected:
                    src = "logic" if t in o_pred else "extraction"
                    add(f"{pack}|{config}|false|{t}|{src}", f"{LABEL[config]} false alarm `{t}` ({src} error)",
                        {"pair": rec["pair_id"], "tier": rec["tier"], "doc": None})
                for dt, d in pair["documents"].items():
                    _, scored = metrics.score_document(rec["documents"].get(dt), d["truth"], d["occluded"])
                    wrong = {s.path for s in scored if not s.correct_norm}
                    flat = _flatten(d["truth"])
                    missed = {p for p, v in flat.items() if v is not None and _pred_value(rec["documents"].get(dt), p)
                              is None and p not in d["occluded"]}
                    for p in sorted(wrong | missed):
                        w = re.sub(r"\[\d+\]", "[*]", p)
                        kind = "misread" if p in wrong else "dropped"
                        what = "wrong value" if kind == "misread" else "left null"
                        add(f"{pack}|{config}|field|{dt}.{w}|{kind}", f"{LABEL[config]}: `{dt}.{w}` {kind} ({what})",
                            {"pair": rec["pair_id"], "tier": rec["tier"], "doc": dt, "path": p,
                             "truth": _truth_value(d["truth"], p), "pred": _pred_value(rec["documents"].get(dt), p)})
    ranked = sorted(patterns.values(), key=lambda p: -p["count"])[:limit]
    for p in ranked:
        tiers = Counter(e["tier"] for e in p["examples"])
        p["tiers_in_examples"] = dict(tiers)
    return ranked


def _flatten(truth: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in truth.items():
        if isinstance(v, list):
            for i, item in enumerate(v):
                out.update(_flatten(item, f"{prefix}{k}[{i}]."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def _truth_value(truth: dict[str, Any], path: str) -> Any:
    return _flatten(truth).get(path)


def _example_image(pack: str, pair_id: str, doc: str | None, n: int) -> str | None:
    manifest = json.loads((DATA / pack / "manifest.json").read_text())
    pair = next(p for p in manifest["pairs"] if p["pair_id"] == pair_id)
    doc = doc or next(iter(pair["documents"]))
    src = DATA / pack / pair["documents"][doc]["image"]
    if not src.exists():
        return None
    FAIL_DIR.mkdir(parents=True, exist_ok=True)
    out = FAIL_DIR / f"{n:02d}_{pair_id}_{doc}.jpg"
    img = Image.open(src).convert("RGB")
    img.thumbnail((900, 900))
    img.save(out, quality=82)
    return f"failures/{out.name}"


def failure_section() -> list[str]:
    """Top patterns with one example image and a one-line diagnosis each."""
    notes_path = RESULTS / "failure_notes.json"
    notes = json.loads(notes_path.read_text()) if notes_path.exists() else {}
    pats = failure_patterns()
    if not pats:
        return ["TBD (needs real extraction runs)"]
    if FAIL_DIR.exists():
        shutil.rmtree(FAIL_DIR)
    out = []
    for n, p in enumerate(pats, 1):
        pack = p["key"].split("|")[0]
        ex = p["examples"][0]
        img = _example_image(pack, ex["pair"], ex.get("doc"), n)
        diag = notes.get(p["key"], f"_auto:_ {p['description']}")
        detail = ""
        if "truth" in ex:
            detail = (f" Example `{ex['pair']}` ({ex['tier']}): `{ex['path']}` truth `{ex['truth']}`, "
                      f"got `{ex['pred']}`.")
        else:
            detail = f" Example `{ex['pair']}` ({ex['tier']})."
        out += [f"**{n}. {p['description']}**: {p['count']} occurrences.{detail}", "", f"Diagnosis: {diag}", ""]
        if img:
            out += [f'<img src="{img}" width="360" alt="example for pattern {n}">', ""]
    (RESULTS / "failure_patterns.json").write_text(json.dumps(pats, indent=1, default=str))
    return out


def build() -> Path:
    """Write BENCHMARK.md and charts."""
    RESULTS.mkdir(parents=True, exist_ok=True)
    lines = ["# PaperTrail benchmark", "",
             "Generated by `python -m bench.report` from `bench/results/*.json`. Every number below comes from a run "
             "saved in this folder; anything not yet run reads TBD.", ""]
    for pack in PACKS:
        manifest = json.loads((DATA / pack / "manifest.json").read_text())
        tiers = Counter(p["tier"] for p in manifest["pairs"])
        clean = sum(not p["discrepancies"] for p in manifest["pairs"])
        lines += [f"## {pack.capitalize()}", "",
                  f"Test set: {manifest['count']} synthetic pairs (seed {manifest['seed']}): {clean} without "
                  f"discrepancies; tiers {dict(tiers)}.", "", "### Headline", "", *headline(pack), "",
                  validation_line(pack), "", "### Recall per discrepancy type", ""]
        chart = recall_chart(pack)
        if chart:
            lines += [f"![{pack} recall per discrepancy type]({chart})", ""]
        lines += [*per_type_table(pack), "", "### Error attribution (oracle vs real)", "",
                  "Each missed or false discrepancy in a real run is attributed to **extraction** if the oracle run "
                  "(ground-truth documents, same matching mode) got that pair right, else to **matching/logic**.", "",
                  *attribution(pack), "", "### Extraction F1 (normalised) by document and tier", "",
                  *extraction_table(pack), "", "### Confidence calibration (critical fields)", "",
                  *calibration_table(pack), "", "### Decisions", "", *decision_tables(pack), ""]
    lines += ["## Top failure patterns", "", *failure_section()]
    dev = RESULTS / "dev_notes"
    if dev.exists():
        lines += ["## Dev-set notes (not the reported test set)", ""]
        for f in sorted(dev.glob("*.json")):
            r = json.loads(f.read_text())
            m = r["detection"]["micro"]
            lines.append(f"- `{f.stem}`: precision {pct(m['precision'])}, recall {pct(m['recall'])}, clean-pair false "
                         f"alarms {pct(m['false_alarm_rate'])}")
        lines.append("")
    out = RESULTS / "BENCHMARK.md"
    out.write_text("\n".join(lines))
    return out


def main() -> None:
    """CLI entry point."""
    print(f"wrote {build()}")


if __name__ == "__main__":
    main()
