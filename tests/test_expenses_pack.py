"""Expenses pack: one approve case plus a failing case for every check."""

from datetime import date

from papertrail.core.crosscheck import CheckContext
from papertrail.core.decision import Outcome
from papertrail.core.engine import evaluate
from papertrail.core.fields import known, missing
from papertrail.core.matching import Matcher
from papertrail.core.pack import get_pack
from papertrail.core.validators.duplicates import DuplicateIndex
from papertrail.packs.expenses.schemas import ExpenseClaim, Receipt, ReceiptLine
from tests.conftest import make_image

TODAY = date(2026, 10, 6)


def receipt_doc(total=450.0, day=date(2026, 9, 20), merchant="Cafe Aroma", total_conf=0.95, items=(200.0, 250.0)):
    return Receipt(
        merchant=known(merchant, 0.95),
        receipt_number=known("R-101", 0.9),
        date=known(day, 0.95),
        items=[ReceiptLine(description=known(f"item {i}"), amount=known(a)) for i, a in enumerate(items)],
        tax=missing(),
        total=known(total, total_conf),
        payment_mode=known("UPI"),
    )


def claim_doc(amount=450.0, expense_day=date(2026, 9, 20), claim_day=date(2026, 10, 1), merchant="Cafe Aroma"):
    return ExpenseClaim(
        employee_name=known("Asha Verma"),
        employee_id=known("E-1042"),
        claim_date=known(claim_day),
        expense_date=known(expense_day),
        merchant=known(merchant),
        category=known("meals"),
        amount_claimed=known(amount),
        purpose=known("client lunch"),
    )


def run(receipt, claim, history=None, case_id="c1", image=None):
    ctx = CheckContext(matcher=Matcher(), case_id=case_id, today=TODAY, history=history)
    if image is not None:
        ctx.images = {"receipt": image}
    validations, outcomes, decision = evaluate(get_pack("expenses"), {"receipt": receipt, "expense_claim": claim}, ctx)
    codes = {f.code for f in validations} | {f.code for o in outcomes for f in o.findings}
    return decision, codes


def test_clean_case_approves():
    decision, codes = run(receipt_doc(), claim_doc())
    assert decision.outcome == Outcome.APPROVE and codes == set()


def test_claim_exceeds_receipt_flags():
    decision, codes = run(receipt_doc(), claim_doc(amount=900.0))
    assert decision.outcome == Outcome.FLAG and "claim_amount_mismatch" in codes
    assert "expense_claim.amount_claimed" in decision.reasons[0].fields


def test_claim_below_receipt_is_info_only():
    decision, codes = run(receipt_doc(), claim_doc(amount=400.0))
    assert decision.outcome == Outcome.APPROVE and "claim_below_receipt" in codes


def test_receipt_items_do_not_add_up():
    decision, codes = run(receipt_doc(total=500.0), claim_doc(amount=500.0))
    assert decision.outcome == Outcome.FLAG and "receipt_total_mismatch" in codes


def test_old_receipt_outside_policy_window():
    old = date(2026, 7, 1)
    decision, codes = run(receipt_doc(day=old), claim_doc(expense_day=old))
    assert decision.outcome == Outcome.FLAG and "outside_policy_window" in codes


def test_receipt_after_claim_flags():
    late = date(2026, 10, 3)
    _, codes = run(receipt_doc(day=late), claim_doc(expense_day=late))
    assert "outside_policy_window" in codes and "expense_after_claim" in codes


def test_expense_date_mismatch_needs_review():
    decision, codes = run(receipt_doc(), claim_doc(expense_day=date(2026, 9, 21)))
    assert decision.outcome == Outcome.NEEDS_REVIEW and codes == {"expense_date_mismatch"}


def test_merchant_mismatch_needs_review():
    decision, codes = run(receipt_doc(), claim_doc(merchant="Hotel Sunrise"))
    assert decision.outcome == Outcome.NEEDS_REVIEW and codes == {"merchant_mismatch"}


def test_null_amount_is_unverifiable_review():
    receipt = receipt_doc()
    receipt.total = missing("illegible")
    decision, codes = run(receipt, claim_doc())
    assert decision.outcome == Outcome.NEEDS_REVIEW and "claim_amount_mismatch" in codes


def test_duplicate_by_image_hash():
    history = DuplicateIndex()
    img = make_image("SAME RECEIPT", seed=4)
    run(receipt_doc(), claim_doc(), history, "first", img)
    decision, codes = run(receipt_doc(merchant="Other Cafe"), claim_doc(merchant="Other Cafe"), history, "second", img)
    assert decision.outcome == Outcome.FLAG and "duplicate_receipt" in codes


def test_duplicate_by_field_fingerprint():
    history = DuplicateIndex(max_distance=0)
    run(receipt_doc(), claim_doc(), history, "first", make_image("A", seed=1))
    _, codes = run(receipt_doc(merchant="CAFE AROMA."), claim_doc(), history, "second", make_image("B", size=(600, 90)))
    assert "duplicate_receipt" in codes


def test_different_receipt_not_duplicate():
    history = DuplicateIndex(max_distance=0)
    run(receipt_doc(), claim_doc(), history, "first", make_image("A", seed=1))
    _, codes = run(receipt_doc(total=300.0, items=(300.0,)), claim_doc(amount=300.0), history, "second",
                   make_image("B", size=(600, 90)))
    assert "duplicate_receipt" not in codes
