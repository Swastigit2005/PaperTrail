"""Benchmark metric functions on small hand-computed cases."""

import pytest

from bench import metrics


def F(v, c=0.9):  # noqa: N802 - engine-shaped field
    return {"value": v, "confidence": c}


TRUTH = {"name": "Rahul Sharma", "total": 450.0, "date": "2026-09-01",
         "items": [{"desc": "Dolo 650", "qty": 10.0}, {"desc": "Pan 40", "qty": 15.0}]}


def test_values_match_exact_vs_normalized():
    assert metrics.values_match("total", "450", 450.0, False)
    assert not metrics.values_match("name", "Mr. Rahul Sharma", "Rahul Sharma", False)
    assert metrics.values_match("patient_name", "Mr. Rahul Sharma", "Rahul Sharma", True)
    assert metrics.values_match("medicine", "DOLO 650 TAB", "Dolo 650", True)
    assert not metrics.values_match("medicine", "Dolo 500", "Dolo 650", True)
    assert metrics.values_match("x", None, None, True) and not metrics.values_match("x", "a", None, True)


def test_score_document_hand_computed():
    pred = {"name": F("Rahul Sharma"), "total": F(540.0), "date": F(None),
            "items": [{"desc": F("Pan 40"), "qty": F(15.0)}, {"desc": F("Dolo 650"), "qty": F(10.0)},
                      {"desc": F("Crocin"), "qty": F(5.0)}]}
    counts, scored = metrics.score_document(pred, TRUTH)
    # truth leaves: name, total, date, 2x(desc, qty) = 7
    # correct: name, both aligned items (4 fields) = 5 TP
    # total wrong -> FP+FN; date null -> FN; extra item (2 fields) -> 2 FP
    assert (counts.tp, counts.fp, counts.fn) == (5, 3, 2)
    prf = counts.prf()
    assert prf["precision"] == pytest.approx(5 / 8) and prf["recall"] == pytest.approx(5 / 7)
    assert prf["f1"] == pytest.approx(2 * (5 / 8) * (5 / 7) / (5 / 8 + 5 / 7))
    assert len(scored) == 8 and sum(s.correct for s in scored) == 5


def test_occluded_field_null_is_free_guess_is_fp():
    pred_null = {"name": F(None), "total": F(450.0), "date": F("2026-09-01"), "items": []}
    c1, _ = metrics.score_document(pred_null, {"name": "Rahul", "total": 450.0, "date": "2026-09-01", "items": []},
                                   occluded=["name"])
    assert (c1.tp, c1.fp, c1.fn, c1.occluded_guesses) == (2, 0, 0, 0)
    pred_guess = dict(pred_null, name=F("Rohit"))
    c2, _ = metrics.score_document(pred_guess, {"name": "Rahul", "total": 450.0, "date": "2026-09-01", "items": []},
                                   occluded=["name"])
    assert (c2.tp, c2.fp, c2.fn, c2.occluded_guesses) == (2, 1, 0, 1)


def test_failed_extraction_is_all_fn():
    counts, _ = metrics.score_document(None, TRUTH)
    assert (counts.tp, counts.fp, counts.fn) == (0, 0, 7)


def test_align_items_greedy():
    pairs = metrics.align_items([{"a": F(1)}, {"a": F(2)}], [{"a": 2}, {"a": 3}])
    assert (1, 0) in pairs and (0, None) in pairs and (None, 1) in pairs


def test_detection_counts_and_false_alarms():
    tax = ("a", "b")
    d = metrics.DetectionCounts()
    d.update({"a"}, {"a"}, tax)  # TP a
    d.update({"a"}, set(), tax)  # FN a
    d.update(set(), {"b"}, tax)  # FP b on a clean pair
    d.update(set(), set(), tax)  # clean, quiet
    per = d.per_type(tax)
    assert per["a"]["recall"] == 0.5 and per["a"]["precision"] == 1.0
    assert per["b"]["precision"] == 0.0 and per["b"]["recall"] is None
    micro = d.micro()
    assert micro["precision"] == 0.5 and micro["recall"] == 0.5 and micro["false_alarm_rate"] == 0.5


def test_detected_types_ignores_info_and_unverifiable():
    findings = [{"code": "a", "severity": "hard"}, {"code": "b", "severity": "soft", "unverifiable": True},
                {"code": "a2", "severity": "info"}, {"code": "c", "severity": "hard"}]
    assert metrics.detected_types(findings, ("a", "b", "a2")) == {"a"}


def test_decision_summary():
    rows = [("approve", "approve"), ("approve", "needs_review"), ("flag", "flag"), ("flag", "approve")]
    s = metrics.decision_summary(rows)
    assert s["accuracy"] == 0.5 and s["discrepant_not_auto_approved"] == 0.5
    assert s["clean_auto_approved"] == 0.5 and s["clean_flagged"] == 0.0
    assert s["confusion"]["flag"]["approve"] == 1


def test_attribution():
    real = {"p1": ({"a"}, set()), "p2": ({"b"}, set()), "p3": (set(), {"c"}), "p4": (set(), {"d"})}
    oracle = {"p1": ({"a"}, {"a"}), "p2": ({"b"}, set()), "p3": (set(), set()), "p4": (set(), {"d"})}
    assert metrics.attribute_errors(real, oracle) == {"missed_extraction": 1, "missed_logic": 1,
                                                     "false_extraction": 1, "false_logic": 1}


def test_calibration():
    s = [metrics.ScoredField("x", 0.95, True, True), metrics.ScoredField("y", 0.9, False, False),
         metrics.ScoredField("z", 0.4, False, False), metrics.ScoredField("w", 0.6, True, True)]
    c = metrics.calibration(s, threshold=0.75)
    assert c["high_conf_accuracy"] == 0.5 and c["low_conf_accuracy"] == 0.5
    assert c["errors_flagged_low_conf"] == 0.5 and c["n_errors"] == 2


def test_percentile_nearest_rank():
    assert metrics.percentile([1, 2, 3, 4], 50) == 2 and metrics.percentile([1, 2, 3, 4], 95) == 4
    assert metrics.percentile([], 50) is None
