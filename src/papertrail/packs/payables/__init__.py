"""Payables pack: accounts payable invoice-to-PO matching for Indian GST invoices."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from papertrail.core.crosscheck import CheckContext, CrossCheck
from papertrail.core.decision import DecisionPolicy
from papertrail.core.findings import Finding
from papertrail.core.pack import Pack, register
from papertrail.packs.payables.crosschecks import CHECKS
from papertrail.packs.payables.schemas import INVOICE, PURCHASE_ORDER, GSTInvoice, PurchaseOrder
from papertrail.packs.payables.validators import validate_invoice, validate_po


@register
class PayablesPack(Pack):
    """GST invoice + PO: GSTIN checksum, arithmetic, tax split, and line-level PO matching."""

    name = "payables"
    title = "Accounts payable (invoice to PO)"
    description = "GST invoices checked for validity and matched line by line against the purchase order."
    doc_types = (INVOICE, PURCHASE_ORDER)
    policy = DecisionPolicy(
        min_confidence=0.75,
        critical_fields={
            "invoice": ("seller_gstin", "buyer_gstin", "po_reference", "taxable_value", "grand_total",
                        "line_items[*].description", "line_items[*].qty", "line_items[*].rate"),
            "purchase_order": ("po_number", "vendor_gstin", "line_items[*].qty", "line_items[*].rate"),
        },
    )

    def validate(self, doc_type: str, doc: BaseModel, ctx: CheckContext) -> list[Finding]:
        """Dispatch to the invoice or PO validator."""
        if isinstance(doc, GSTInvoice):
            return validate_invoice(doc, ctx)
        if isinstance(doc, PurchaseOrder):
            return validate_po(doc, ctx)
        return []

    def crosschecks(self) -> Sequence[CrossCheck]:
        """Invoice-vs-PO checks."""
        return CHECKS
