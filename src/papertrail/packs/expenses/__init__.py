"""Expenses pack: employee reimbursement (receipt + expense claim). Demo only, not benchmarked.

Built by following docs/ADDING_A_PACK.md without touching papertrail/core.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from papertrail.core.crosscheck import CheckContext, CrossCheck
from papertrail.core.decision import DecisionPolicy
from papertrail.core.findings import Finding
from papertrail.core.pack import Pack, register
from papertrail.packs.expenses.crosschecks import CHECKS
from papertrail.packs.expenses.schemas import EXPENSE_CLAIM, RECEIPT, ExpenseClaim, Receipt
from papertrail.packs.expenses.validators import validate_claim, validate_receipt


@register
class ExpensesPack(Pack):
    """Receipt + expense claim: amount, dates, policy window, merchant, duplicates."""

    name = "expenses"
    title = "Expense reimbursement"
    description = "Employee expense claims checked against the receipt: amount, policy window and duplicates."
    doc_types = (RECEIPT, EXPENSE_CLAIM)
    policy = DecisionPolicy(
        min_confidence=0.75,
        critical_fields={
            "receipt": ("total", "date", "merchant"),
            "expense_claim": ("amount_claimed", "expense_date", "claim_date"),
        },
    )
    benchmarked = False

    def validate(self, doc_type: str, doc: BaseModel, ctx: CheckContext) -> list[Finding]:
        """Dispatch to the receipt or claim validator."""
        if isinstance(doc, Receipt):
            return validate_receipt(doc, ctx)
        if isinstance(doc, ExpenseClaim):
            return validate_claim(doc, ctx)
        return []

    def crosschecks(self) -> Sequence[CrossCheck]:
        """Receipt-vs-claim checks."""
        return CHECKS
