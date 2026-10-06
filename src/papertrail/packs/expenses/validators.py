"""Per-document validators for the expenses pack."""

from __future__ import annotations

from papertrail.core.crosscheck import CheckContext
from papertrail.core.fields import Field
from papertrail.core.findings import Finding, hard, soft
from papertrail.core.validators.arithmetic import close
from papertrail.core.validators.dates import sanity_problem
from papertrail.packs.expenses.schemas import ExpenseClaim, Receipt


def _date_sanity(ref: str, fld: Field, ctx: CheckContext) -> list[Finding]:
    if fld.value is None:
        return []
    problem = sanity_problem(fld.value, today=ctx.today)
    return [soft("implausible_date", f"{ref}: {problem}", ref)] if problem else []


def validate_receipt(doc: Receipt, ctx: CheckContext) -> list[Finding]:
    """Items (with or without separately shown tax) must add up to the total; date must be sane."""
    findings = _date_sanity("receipt.date", doc.date, ctx)
    amounts = [item.amount.value for item in doc.items]
    if doc.total.value is not None and amounts and all(a is not None for a in amounts):
        items_sum = sum(a for a in amounts if a is not None)
        with_tax = items_sum + (doc.tax.value or 0.0)
        if not (close(doc.total.value, items_sum) or close(doc.total.value, with_tax)):
            refs = ["receipt.total"] + [f"receipt.items[{i}].amount" for i in range(len(amounts))]
            findings.append(
                hard(
                    "receipt_total_mismatch",
                    f"receipt.total {doc.total.value:,.2f} does not match items {items_sum:,.2f}"
                    + (f" (+ tax = {with_tax:,.2f})" if doc.tax.value else ""),
                    *refs,
                )
            )
    return findings


def validate_claim(doc: ExpenseClaim, ctx: CheckContext) -> list[Finding]:
    """Positive amount, sane dates, and the expense happening before the claim."""
    findings = _date_sanity("expense_claim.claim_date", doc.claim_date, ctx)
    findings += _date_sanity("expense_claim.expense_date", doc.expense_date, ctx)
    if doc.amount_claimed.value is not None and doc.amount_claimed.value <= 0:
        findings.append(
            hard("non_positive_claim", "expense_claim.amount_claimed must be positive", "expense_claim.amount_claimed")
        )
    if doc.claim_date.value and doc.expense_date.value and doc.expense_date.value > doc.claim_date.value:
        findings.append(
            hard(
                "expense_after_claim",
                f"expense_claim.expense_date {doc.expense_date.value} is after claim_date {doc.claim_date.value}",
                "expense_claim.expense_date",
                "expense_claim.claim_date",
            )
        )
    return findings
