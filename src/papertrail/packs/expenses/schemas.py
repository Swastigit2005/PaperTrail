"""Expenses pack schemas: a merchant receipt and an employee expense-claim form."""

from __future__ import annotations

from pydantic import BaseModel

from papertrail.core.fields import Field, LenientDate, Number, doc_field, doc_list
from papertrail.core.pack import DocType


class ReceiptLine(BaseModel):
    """One purchased item on a receipt."""

    description: Field[str] = doc_field("item name as printed")
    amount: Field[Number] = doc_field("line amount")


class Receipt(BaseModel):
    """A shop / restaurant / travel receipt."""

    merchant: Field[str] = doc_field("business name printed at the top")
    receipt_number: Field[str] = doc_field("bill / receipt / invoice number")
    date: Field[LenientDate] = doc_field("transaction date")
    items: list[ReceiptLine] = doc_list("purchased items, in order")
    tax: Field[Number] = doc_field("total tax (GST) shown separately, null if not shown")
    total: Field[Number] = doc_field("final amount paid, including taxes")
    payment_mode: Field[str] = doc_field("cash, card, UPI, ... if printed")


class ExpenseClaim(BaseModel):
    """An employee's reimbursement claim form for one expense."""

    employee_name: Field[str] = doc_field("claimant's full name")
    employee_id: Field[str] = doc_field("employee ID / code")
    claim_date: Field[LenientDate] = doc_field("date the claim form was filled/submitted")
    expense_date: Field[LenientDate] = doc_field("date the expense was incurred")
    merchant: Field[str] = doc_field("merchant / vendor named on the claim")
    category: Field[str] = doc_field("expense category, e.g. meals, travel, supplies")
    amount_claimed: Field[Number] = doc_field("amount the employee asks to be reimbursed")
    purpose: Field[str] = doc_field("business purpose as written")


RECEIPT = DocType(
    name="receipt",
    title="merchant receipt",
    schema=Receipt,
    description="a till, restaurant, fuel or travel receipt issued by a merchant",
    keywords=("receipt", "cash memo", "thank you", "visit again", "bill no", "payment", "change"),
    hints="The total may be labelled 'Net Amount', 'Grand Total' or 'Amount Paid'. "
    "Ignore loyalty points and rounding lines when listing items.",
)

EXPENSE_CLAIM = DocType(
    name="expense_claim",
    title="expense claim form",
    schema=ExpenseClaim,
    description="an employee reimbursement form naming the claimant, expense and amount claimed",
    keywords=("expense claim", "reimbursement", "employee", "claimant", "approved by", "purpose"),
)
