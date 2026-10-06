"""Receipt-vs-claim checks for the expenses pack."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel

from papertrail.core.crosscheck import CheckContext, CrossCheck
from papertrail.core.findings import Finding, hard, info, soft, unverifiable
from papertrail.core.validators.arithmetic import close
from papertrail.core.validators.dates import days_between
from papertrail.core.validators.duplicates import fingerprint
from papertrail.core.validators.fuzzy import text_similarity
from papertrail.packs.expenses.schemas import ExpenseClaim, Receipt

POLICY_WINDOW_DAYS = 30
MERCHANT_MIN_SIMILARITY = 80.0


def _docs(docs: Mapping[str, BaseModel]) -> tuple[Receipt, ExpenseClaim]:
    receipt, claim = docs["receipt"], docs["expense_claim"]
    assert isinstance(receipt, Receipt) and isinstance(claim, ExpenseClaim)
    return receipt, claim


def amount_matches(docs: Mapping[str, BaseModel], ctx: CheckContext) -> list[Finding]:
    """The claimed amount must not exceed the receipt total."""
    receipt, claim = _docs(docs)
    refs = ("expense_claim.amount_claimed", "receipt.total")
    claimed, total = claim.amount_claimed.value, receipt.total.value
    if claimed is None or total is None:
        return [unverifiable("claim_amount_mismatch", "cannot compare claimed amount with receipt total", *refs)]
    if close(claimed, total):
        return []
    if claimed > total:
        return [hard("claim_amount_mismatch", f"claimed {claimed:,.2f} exceeds receipt total {total:,.2f}", *refs)]
    return [info("claim_below_receipt", f"claimed {claimed:,.2f} is less than receipt total {total:,.2f}", *refs)]


def expense_date_matches(docs: Mapping[str, BaseModel], ctx: CheckContext) -> list[Finding]:
    """The expense date on the claim should be the receipt date."""
    receipt, claim = _docs(docs)
    refs = ("expense_claim.expense_date", "receipt.date")
    if claim.expense_date.value is None or receipt.date.value is None:
        return [unverifiable("expense_date_mismatch", "cannot compare expense date with receipt date", *refs)]
    if claim.expense_date.value != receipt.date.value:
        return [
            soft(
                "expense_date_mismatch",
                f"claim says {claim.expense_date.value}, receipt is dated {receipt.date.value}",
                *refs,
            )
        ]
    return []


def within_policy_window(docs: Mapping[str, BaseModel], ctx: CheckContext) -> list[Finding]:
    """The receipt must be dated on or before the claim and at most POLICY_WINDOW_DAYS earlier."""
    receipt, claim = _docs(docs)
    refs = ("receipt.date", "expense_claim.claim_date")
    if receipt.date.value is None or claim.claim_date.value is None:
        return [unverifiable("outside_policy_window", "cannot check the policy window", *refs)]
    age = days_between(receipt.date.value, claim.claim_date.value)
    if age < 0:
        return [hard("outside_policy_window", f"receipt dated {-age} day(s) after the claim was filed", *refs)]
    if age > POLICY_WINDOW_DAYS:
        return [
            hard("outside_policy_window", f"receipt is {age} days old; policy allows {POLICY_WINDOW_DAYS}", *refs)
        ]
    return []


def merchant_matches(docs: Mapping[str, BaseModel], ctx: CheckContext) -> list[Finding]:
    """The merchant named on the claim should be the one on the receipt."""
    receipt, claim = _docs(docs)
    refs = ("expense_claim.merchant", "receipt.merchant")
    if not claim.merchant.value or not receipt.merchant.value:
        return [unverifiable("merchant_mismatch", "cannot compare merchant names", *refs)]
    score = text_similarity(claim.merchant.value, receipt.merchant.value)
    if score < MERCHANT_MIN_SIMILARITY:
        return [
            soft(
                "merchant_mismatch",
                f"claim merchant '{claim.merchant.value}' vs receipt '{receipt.merchant.value}' "
                f"(similarity {score:.0f})",
                *refs,
            )
        ]
    return []


def not_duplicate(docs: Mapping[str, BaseModel], ctx: CheckContext) -> list[Finding]:
    """Same receipt image (perceptual hash) or same merchant+date+total seen in an earlier case."""
    receipt, _ = _docs(docs)
    if ctx.history is None:
        return []
    image = ctx.images.get("receipt")
    image_hash = image.dhash if image else None
    fp = None
    if receipt.merchant.value and receipt.date.value and receipt.total.value is not None:
        fp = fingerprint(receipt.merchant.value, receipt.date.value.isoformat(), float(receipt.total.value))
    hits = ctx.history.check(ctx.case_id, image_hash, fp)
    ctx.history.add(ctx.case_id, image_hash, fp)
    return [
        hard(
            "duplicate_receipt",
            f"receipt looks like a duplicate of case {hit.case_id} "
            + ("(near-identical image)" if hit.kind == "image" else "(same merchant, date and total)"),
            "receipt.merchant",
            "receipt.date",
            "receipt.total",
            other_case=hit.case_id,
            kind=hit.kind,
        )
        for hit in hits
    ]


CHECKS = (
    CrossCheck("claim_amount_mismatch", "Claimed amount does not exceed the receipt total", amount_matches),
    CrossCheck("expense_date_mismatch", "Claim expense date equals the receipt date", expense_date_matches),
    CrossCheck(
        "outside_policy_window", f"Receipt is within {POLICY_WINDOW_DAYS} days of the claim", within_policy_window
    ),
    CrossCheck("merchant_mismatch", "Claim merchant matches the receipt merchant", merchant_matches),
    CrossCheck("duplicate_receipt", "Receipt was not already claimed", not_duplicate),
)
