"""The generator is reproducible and its ground truth is internally consistent."""

import random

from faker import Faker

from bench.generate_synthetic import plan
from bench.synth import claims, payables
from papertrail.core.validators.gstin import validate_gstin


def _case(module, seed, kinds):
    fake = Faker("en_IN")
    fake.seed_instance(seed)
    return module.build_case(random.Random(seed), fake, kinds)


def test_same_seed_same_truth():
    a, b = _case(claims, 3, ["implausible_quantity"]), _case(claims, 3, ["implausible_quantity"])
    assert a.rx == b.rx and a.bill == b.bill and a.discrepancies == b.discrepancies


def test_clean_claims_truth_adds_up():
    for seed in range(20):
        case = _case(claims, seed, [])
        lines = case.bill["line_items"]
        assert all(abs(x["qty"] * x["rate"] - x["amount"]) < 0.011 for x in lines)
        assert abs(sum(x["amount"] for x in lines) - (case.bill["discount"] or 0) - case.bill["total"]) < 0.011
        assert case.bill["date"] >= case.rx["date"]


def test_clean_payables_truth_is_valid():
    for seed in range(20):
        case = _case(payables, seed, [])
        inv = case.invoice
        assert validate_gstin(inv["seller_gstin"]).valid and validate_gstin(inv["buyer_gstin"]).valid
        tax = (inv["cgst"] or 0) + (inv["sgst"] or 0) + (inv["igst"] or 0)
        assert abs(inv["taxable_value"] + tax - inv["grand_total"]) < 0.011
        assert (inv["igst"] is None) == (inv["seller_gstin"][:2] == inv["buyer_gstin"][:2])


def test_seeded_refs_point_at_final_lines():
    for seed in range(30):
        case = _case(payables, seed, ["line_not_on_po", "rate_above_po"])
        for d in case.discrepancies:
            if d["type"] == "line_not_on_po":
                idx = int(d["fields"][0].split("[")[1].split("]")[0])
                po_descs = {x["description"] for x in case.po["line_items"]}
                assert case.invoice["line_items"][idx]["description"] not in po_descs


def test_plan_balances_types_and_half_clean():
    p = plan(60, payables.DISCREPANCIES, random.Random(0))
    assert sum(not kinds for _, kinds in p) == 30
    counts = {t: sum(t in kinds for _, kinds in p) for t in payables.DISCREPANCIES}
    assert max(counts.values()) - min(counts.values()) <= 2
