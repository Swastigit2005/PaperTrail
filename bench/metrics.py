"""Benchmark metrics. Pure functions over plain dicts so they can be unit-tested by hand.

Conventions
- A *truth* document is plain JSON (values only); a *pred* document is the engine's JSON output
  where every leaf is ``{"value", "confidence", "evidence", "null_reason"}``.
- List fields (line items, medicines) are compared as sets: items are aligned greedily by how
  many of their fields agree, then compared field by field.
- Occluded fields (covered on the image): a null prediction is neither rewarded nor penalised,
  a correct value is a TP, a wrong value is an FP (a guess).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from papertrail.core.validators.fuzzy import normalize_person_name

_STOP = {"tab", "tabs", "tablet", "tablets", "cap", "caps", "capsule", "capsules", "mr", "mrs", "ms", "dr", "rs",
         "no", "the"}  # fmt: skip
_NAME_FIELDS = ("patient_name", "doctor_name")


# --- value comparison ---------------------------------------------------------------------------


def _norm_str(text: str) -> str:
    tokens = [t for t in re.sub(r"[^a-z0-9]+", " ", text.lower()).split() if t not in _STOP]
    return "".join(tokens)


def values_match(path: str, pred: Any, truth: Any, normalized: bool) -> bool:
    """Compare one predicted value with the truth. Numbers within 0.01; dates by ISO string."""
    if pred is None or truth is None:
        return pred is None and truth is None
    if isinstance(truth, int | float) and not isinstance(truth, bool):
        try:
            return abs(float(pred) - float(truth)) <= 0.01
        except (TypeError, ValueError):
            return False
    if isinstance(truth, date):
        truth = truth.isoformat()
    p, t = str(pred).strip(), str(truth).strip()
    if not normalized:
        return p == t
    if path.rsplit(".", 1)[-1] in _NAME_FIELDS:
        return normalize_person_name(p).replace(" ", "") == normalize_person_name(t).replace(" ", "")
    return _norm_str(p) == _norm_str(t)


# --- document scoring ---------------------------------------------------------------------------


@dataclass
class FieldCounts:
    """TP/FP/FN for one slice (exact and normalised), plus guesses on occluded fields."""

    tp: int = 0
    fp: int = 0
    fn: int = 0
    tp_norm: int = 0
    fp_norm: int = 0
    fn_norm: int = 0
    occluded_guesses: int = 0
    occluded_total: int = 0

    def add(self, other: FieldCounts) -> FieldCounts:
        """Accumulate in place."""
        for k in self.__dataclass_fields__:
            setattr(self, k, getattr(self, k) + getattr(other, k))
        return self

    def prf(self, normalized: bool = False) -> dict[str, float | None]:
        """Precision, recall, F1."""
        tp, fp, fn = (self.tp_norm, self.fp_norm, self.fn_norm) if normalized else (self.tp, self.fp, self.fn)
        p = tp / (tp + fp) if tp + fp else None
        r = tp / (tp + fn) if tp + fn else None
        f1 = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
        return {"precision": p, "recall": r, "f1": f1}


@dataclass
class ScoredField:
    """One predicted non-null field with its confidence and correctness (for calibration)."""

    path: str
    confidence: float
    correct: bool
    correct_norm: bool


def _leaf(pred: Any) -> tuple[Any, float]:
    if isinstance(pred, Mapping) and "value" in pred:
        return pred.get("value"), float(pred.get("confidence") or 0.0)
    return pred, 0.0


def _agreement(pred_item: Mapping[str, Any], truth_item: Mapping[str, Any]) -> int:
    return sum(
        1 for k, tv in truth_item.items()
        if tv is not None and values_match(k, _leaf(pred_item.get(k))[0], tv, normalized=True)
    )


def align_items(
    pred_items: list[Mapping[str, Any]], truth_items: list[Mapping[str, Any]]
) -> list[tuple[int | None, int | None]]:
    """Greedy one-to-one alignment by field agreement. Returns (pred_idx, truth_idx) pairs incl. unmatched."""
    scores = sorted(
        ((_agreement(p, t), i, j) for i, p in enumerate(pred_items) for j, t in enumerate(truth_items)),
        reverse=True,
    )
    used_p, used_t, pairs = set(), set(), []
    for score, i, j in scores:
        if score == 0 or i in used_p or j in used_t:
            continue
        used_p.add(i)
        used_t.add(j)
        pairs.append((i, j))
    pairs += [(i, None) for i in range(len(pred_items)) if i not in used_p]
    pairs += [(None, j) for j in range(len(truth_items)) if j not in used_t]
    return pairs


def _score_leaf(
    path: str, pred: Any, truth: Any, occluded: bool, counts: FieldCounts, scored: list[ScoredField]
) -> None:
    value, conf = _leaf(pred)
    for normalized in (False, True):
        ok = values_match(path, value, truth, normalized) if value is not None and truth is not None else False
        tp, fp, fn = ("tp_norm", "fp_norm", "fn_norm") if normalized else ("tp", "fp", "fn")
        if value is not None and ok:
            setattr(counts, tp, getattr(counts, tp) + 1)
        elif value is not None:
            setattr(counts, fp, getattr(counts, fp) + 1)
            if truth is not None and not occluded:
                setattr(counts, fn, getattr(counts, fn) + 1)
        elif truth is not None and not occluded:
            setattr(counts, fn, getattr(counts, fn) + 1)
    if occluded:
        counts.occluded_total += 1
        if value is not None and not values_match(path, value, truth, True):
            counts.occluded_guesses += 1
    if value is not None:
        scored.append(ScoredField(path, conf, values_match(path, value, truth, False) if truth is not None else False,
                                  values_match(path, value, truth, True) if truth is not None else False))


def score_document(
    pred: Mapping[str, Any] | None, truth: Mapping[str, Any], occluded: Iterable[str] = ()
) -> tuple[FieldCounts, list[ScoredField]]:
    """Field-level counts for one document. ``pred`` None means extraction failed (all FN)."""
    pred = pred or {}
    occ = set(occluded)
    counts, scored = FieldCounts(), []
    for key, tv in truth.items():
        if isinstance(tv, list):
            p_items = [x for x in (pred.get(key) or []) if isinstance(x, Mapping)]
            for i, j in align_items(p_items, tv):
                t_item = tv[j] if j is not None else {}
                p_item = p_items[i] if i is not None else {}
                keys = set(t_item) | {k for k in p_item}
                for k in sorted(keys):
                    path = f"{key}[{j if j is not None else i}].{k}"
                    _score_leaf(path, p_item.get(k), t_item.get(k), j is not None and f"{key}[{j}].{k}" in occ,
                                counts, scored)
        else:
            _score_leaf(key, pred.get(key), tv, key in occ, counts, scored)
    return counts, scored


# --- discrepancy detection ----------------------------------------------------------------------


def detected_types(findings: Iterable[Mapping[str, Any]], taxonomy: Iterable[str]) -> set[str]:
    """Taxonomy codes the engine raised (hard or soft, excluding 'could not verify' findings)."""
    tax = set(taxonomy)
    return {
        f["code"] for f in findings
        if f["code"] in tax and f.get("severity") in ("hard", "soft") and not f.get("unverifiable")
    }


@dataclass
class DetectionCounts:
    """Per-type TP/FP/FN over pairs."""

    tp: dict[str, int] = field(default_factory=dict)
    fp: dict[str, int] = field(default_factory=dict)
    fn: dict[str, int] = field(default_factory=dict)
    clean_pairs: int = 0
    clean_with_alarm: int = 0

    def update(self, expected: set[str], predicted: set[str], taxonomy: Iterable[str]) -> None:
        """Add one pair."""
        for t in taxonomy:
            if t in expected and t in predicted:
                self.tp[t] = self.tp.get(t, 0) + 1
            elif t in predicted:
                self.fp[t] = self.fp.get(t, 0) + 1
            elif t in expected:
                self.fn[t] = self.fn.get(t, 0) + 1
        if not expected:
            self.clean_pairs += 1
            self.clean_with_alarm += bool(predicted)

    def per_type(self, taxonomy: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Precision / recall / support per type."""
        out = {}
        for t in taxonomy:
            tp, fp, fn = self.tp.get(t, 0), self.fp.get(t, 0), self.fn.get(t, 0)
            out[t] = {"tp": tp, "fp": fp, "fn": fn, "support": tp + fn,
                      "precision": tp / (tp + fp) if tp + fp else None,
                      "recall": tp / (tp + fn) if tp + fn else None}
        return out

    def micro(self) -> dict[str, float | None]:
        """Micro-averaged precision / recall across types, and the clean-pair false-alarm rate."""
        tp, fp, fn = sum(self.tp.values()), sum(self.fp.values()), sum(self.fn.values())
        return {"precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
                "false_alarm_rate": self.clean_with_alarm / self.clean_pairs if self.clean_pairs else None}


# --- decisions, attribution, calibration --------------------------------------------------------

OUTCOMES = ("approve", "needs_review", "flag")


def confusion(rows: Iterable[tuple[str, str]]) -> dict[str, dict[str, int]]:
    """Confusion matrix expected -> predicted over the three outcomes."""
    m = {e: dict.fromkeys(OUTCOMES, 0) for e in OUTCOMES}
    for expected, predicted in rows:
        m[expected][predicted] += 1
    return m


def decision_summary(rows: list[tuple[str, str]]) -> dict[str, Any]:
    """Exact accuracy plus 'safe routing' (discrepant pairs never auto-approved, clean pairs never flagged)."""
    n = len(rows)
    exact = sum(e == p for e, p in rows)
    bad = [(e, p) for e, p in rows if e == "flag"]
    good = [(e, p) for e, p in rows if e == "approve"]
    return {
        "n": n,
        "accuracy": exact / n if n else None,
        "discrepant_not_auto_approved": sum(p != "approve" for _, p in bad) / len(bad) if bad else None,
        "clean_auto_approved": sum(p == "approve" for _, p in good) / len(good) if good else None,
        "clean_flagged": sum(p == "flag" for _, p in good) / len(good) if good else None,
        "confusion": confusion(rows),
    }


def attribute_errors(
    real: Mapping[str, tuple[set[str], set[str]]], oracle: Mapping[str, tuple[set[str], set[str]]]
) -> dict[str, int]:
    """Split real-run detection errors into extraction vs matching/logic errors.

    ``real``/``oracle`` map pair_id -> (expected types, predicted types). An error (missed or false
    type) that the oracle run also makes is a matching/logic error; one the oracle gets right is
    an extraction error.
    """
    out = {"missed_extraction": 0, "missed_logic": 0, "false_extraction": 0, "false_logic": 0}
    for pair_id, (expected, predicted) in real.items():
        o_pred = oracle.get(pair_id, (expected, set()))[1]
        for t in expected - predicted:
            out["missed_logic" if t not in o_pred else "missed_extraction"] += 1
        for t in predicted - expected:
            out["false_logic" if t in o_pred else "false_extraction"] += 1
    return out


CONF_BINS = ((0.0, 0.5), (0.5, 0.75), (0.75, 0.9), (0.9, 1.01))


def calibration(scored: Iterable[ScoredField], threshold: float = 0.75) -> dict[str, Any]:
    """Accuracy by confidence bin, and how many wrong fields fall under the review threshold."""
    items = list(scored)
    bins = []
    for lo, hi in CONF_BINS:
        sel = [s for s in items if lo <= s.confidence < hi]
        bins.append({"range": [lo, min(hi, 1.0)], "n": len(sel),
                     "accuracy": sum(s.correct_norm for s in sel) / len(sel) if sel else None})
    wrong = [s for s in items if not s.correct_norm]
    high = [s for s in items if s.confidence >= threshold]
    low = [s for s in items if s.confidence < threshold]
    return {
        "bins": bins,
        "threshold": threshold,
        "high_conf_accuracy": sum(s.correct_norm for s in high) / len(high) if high else None,
        "low_conf_accuracy": sum(s.correct_norm for s in low) / len(low) if low else None,
        "errors_flagged_low_conf": sum(s.confidence < threshold for s in wrong) / len(wrong) if wrong else None,
        "n_fields": len(items),
        "n_errors": len(wrong),
    }


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0-100)."""
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, math.ceil(q / 100 * len(ordered)) - 1)
    return ordered[k]
