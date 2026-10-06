"""Payables pack schemas: a GST tax invoice and the purchase order it bills against."""

from __future__ import annotations

from pydantic import BaseModel

from papertrail.core.fields import Field, LenientDate, Number, doc_field, doc_list
from papertrail.core.pack import DocType


class InvoiceLine(BaseModel):
    """One line on a GST tax invoice."""

    description: Field[str] = doc_field("goods/service description as printed")
    hsn: Field[str] = doc_field("HSN or SAC code (digits only)")
    qty: Field[Number] = doc_field("quantity")
    rate: Field[Number] = doc_field("unit rate before tax")
    amount: Field[Number] = doc_field("taxable line amount (qty x rate)")
    gst_rate: Field[Number] = doc_field("GST rate % for the line if printed, e.g. 18")


class GSTInvoice(BaseModel):
    """An Indian GST tax invoice."""

    seller_name: Field[str] = doc_field("supplier / seller name")
    seller_gstin: Field[str] = doc_field("supplier GSTIN, 15 characters")
    buyer_name: Field[str] = doc_field("buyer / bill-to name")
    buyer_gstin: Field[str] = doc_field("buyer GSTIN, 15 characters")
    invoice_number: Field[str] = doc_field("tax invoice number")
    invoice_date: Field[LenientDate] = doc_field("invoice date")
    po_reference: Field[str] = doc_field("purchase order number referenced on the invoice")
    place_of_supply: Field[str] = doc_field("place of supply (state) if printed")
    line_items: list[InvoiceLine] = doc_list("every invoice line, in order")
    taxable_value: Field[Number] = doc_field("total taxable value before GST")
    cgst: Field[Number] = doc_field("total CGST amount; null if not charged")
    sgst: Field[Number] = doc_field("total SGST/UTGST amount; null if not charged")
    igst: Field[Number] = doc_field("total IGST amount; null if not charged")
    grand_total: Field[Number] = doc_field("invoice total including GST")


class POLine(BaseModel):
    """One line on a purchase order."""

    description: Field[str] = doc_field("item description as printed")
    hsn: Field[str] = doc_field("HSN/SAC code if printed")
    qty: Field[Number] = doc_field("ordered quantity")
    rate: Field[Number] = doc_field("agreed unit rate before tax")
    amount: Field[Number] = doc_field("line amount if printed")


class PurchaseOrder(BaseModel):
    """A buyer's purchase order to a vendor."""

    po_number: Field[str] = doc_field("purchase order number")
    po_date: Field[LenientDate] = doc_field("PO date")
    buyer_name: Field[str] = doc_field("issuing company")
    vendor_name: Field[str] = doc_field("vendor / supplier name")
    vendor_gstin: Field[str] = doc_field("vendor GSTIN")
    line_items: list[POLine] = doc_list("every ordered line, in order")
    total: Field[Number] = doc_field("PO total if printed")


INVOICE = DocType(
    name="invoice",
    title="GST tax invoice",
    schema=GSTInvoice,
    description="an Indian GST tax invoice with GSTINs, HSN codes and CGST/SGST or IGST",
    keywords=("tax invoice", "gstin", "hsn", "cgst", "sgst", "igst", "taxable value", "invoice no"),
    hints=(
        "GSTINs are 15 characters: 2 digits, 5 letters, 4 digits, letter, 1 char, 'Z', 1 char - copy them "
        "character by character; do not correct them. Intra-state invoices show CGST and SGST; inter-state "
        "show IGST. The PO reference may be labelled 'PO No', 'Order Ref' or 'Buyer's Order No'."
    ),
)

PURCHASE_ORDER = DocType(
    name="purchase_order",
    title="purchase order",
    schema=PurchaseOrder,
    description="a purchase order issued by a buyer listing items, quantities and agreed rates",
    keywords=("purchase order", "po no", "po number", "order date", "delivery", "terms", "vendor", "ship to"),
)
