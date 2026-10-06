"""Per-document validators for the payables pack: GSTINs, arithmetic, GST slabs, tax split."""

from __future__ import annotations

from datetime import date

from papertrail.core.crosscheck import CheckContext
from papertrail.core.fields import Field
from papertrail.core.findings import Finding, hard, soft, unverifiable
from papertrail.core.validators.arithmetic import close, line_amount_check, sum_check
from papertrail.core.validators.dates import sanity_problem
from papertrail.core.validators.gstin import same_state, validate_gstin
from papertrail.packs.payables.schemas import GSTInvoice, PurchaseOrder

# GST 2.0 rationalisation (56th GST Council meeting), effective 22 September 2025:
# the 12% and 28% slabs were folded into 5%/18%, with a 40% rate for luxury/sin goods.
# 0.25% (rough precious stones) and 3% (gold, silver) are special rates in both regimes.
GST2_EFFECTIVE = date(2025, 9, 22)
SLABS_BEFORE_GST2 = frozenset({0.0, 0.25, 3.0, 5.0, 12.0, 18.0, 28.0})
SLABS_FROM_GST2 = frozenset({0.0, 0.25, 3.0, 5.0, 18.0, 40.0})


def valid_slabs(invoice_date: date | None) -> frozenset[float]:
    """GST slabs valid on the invoice date (both regimes if the date is unknown)."""
    if invoice_date is None:
        return SLABS_BEFORE_GST2 | SLABS_FROM_GST2
    return SLABS_FROM_GST2 if invoice_date >= GST2_EFFECTIVE else SLABS_BEFORE_GST2


def gstin_findings(ref: str, fld: Field) -> list[Finding]:
    """Format and checksum of one GSTIN field."""
    if not fld.value:
        return [unverifiable("invalid_gstin", f"{ref} missing; cannot validate", ref)]
    check = validate_gstin(fld.value)
    if check.valid:
        return []
    return [hard("invalid_gstin", f"{ref} '{fld.value}' is not a valid GSTIN: {check.reason}", ref)]


def _amount(fld: Field) -> float:
    return float(fld.value or 0.0)


def tax_findings(inv: GSTInvoice) -> list[Finding]:
    """GST slab validity and CGST/SGST vs IGST split from the GSTIN state codes."""
    findings: list[Finding] = []
    taxable = inv.taxable_value.value
    cgst, sgst, igst = _amount(inv.cgst), _amount(inv.sgst), _amount(inv.igst)
    slabs = valid_slabs(inv.invoice_date.value)

    for i, line in enumerate(inv.line_items):
        if line.gst_rate.value is not None and line.gst_rate.value not in slabs:
            findings.append(hard("invalid_gst_slab", f"invoice.line_items[{i}].gst_rate {line.gst_rate.value:g}% is "
                                 f"not a GST slab on {inv.invoice_date.value}", f"invoice.line_items[{i}].gst_rate"))
    if taxable:
        effective = round((cgst + sgst + igst) / taxable * 100, 2)
        line_rates = {line.gst_rate.value for line in inv.line_items if line.gst_rate.value is not None}
        single_rate = len(line_rates) <= 1
        if single_rate and not any(abs(effective - s) <= 0.15 for s in slabs):
            findings.append(hard("invalid_gst_slab", f"total GST is {effective:g}% of taxable value, not a valid slab",
                                 "invoice.cgst", "invoice.sgst", "invoice.igst", "invoice.taxable_value"))

    intra = None
    if inv.seller_gstin.value and inv.buyer_gstin.value:
        intra = same_state(inv.seller_gstin.value, inv.buyer_gstin.value)
    refs = ("invoice.cgst", "invoice.sgst", "invoice.igst", "invoice.seller_gstin", "invoice.buyer_gstin")
    if intra is None:
        if cgst or sgst or igst:
            findings.append(unverifiable("wrong_tax_split", "cannot decide intra/inter-state: a GSTIN is unreadable",
                                         *refs))
    elif intra:
        if igst > 0 or not close(cgst, sgst, abs_tol=0.5):
            findings.append(hard("wrong_tax_split", f"intra-state supply (same state code) must charge equal "
                                 f"CGST and SGST and no IGST; got CGST {cgst:,.2f}, SGST {sgst:,.2f}, IGST {igst:,.2f}",
                                 *refs))
    elif cgst > 0 or sgst > 0:
        findings.append(hard("wrong_tax_split", f"inter-state supply (different state codes) must charge IGST only; "
                             f"got CGST {cgst:,.2f}, SGST {sgst:,.2f}", *refs))
    return findings


def validate_invoice(inv: GSTInvoice, ctx: CheckContext) -> list[Finding]:
    """GSTINs, line and total arithmetic, slabs and tax split."""
    findings = gstin_findings("invoice.seller_gstin", inv.seller_gstin)
    findings += gstin_findings("invoice.buyer_gstin", inv.buyer_gstin)
    if inv.invoice_date.value and (problem := sanity_problem(inv.invoice_date.value, today=ctx.today)):
        findings.append(soft("implausible_date", f"invoice.invoice_date: {problem}", "invoice.invoice_date"))
    for i, line in enumerate(inv.line_items):
        p = f"invoice.line_items[{i}]"
        if f := line_amount_check(line.qty, line.rate, line.amount, paths=(f"{p}.qty", f"{p}.rate", f"{p}.amount"),
                                  code="arithmetic_error"):
            findings.append(f)
    if inv.line_items:
        parts = [(f"invoice.line_items[{i}].amount", line.amount) for i, line in enumerate(inv.line_items)]
        if f := sum_check(parts, ("invoice.taxable_value", inv.taxable_value), code="arithmetic_error",
                          label="invoice.taxable_value"):
            findings.append(f)
    tax_parts = [("invoice.taxable_value", inv.taxable_value)] + [
        (f"invoice.{k}", getattr(inv, k)) for k in ("cgst", "sgst", "igst") if getattr(inv, k).value is not None
    ]
    if f := sum_check(tax_parts, ("invoice.grand_total", inv.grand_total), code="total_mismatch",
                      label="invoice.grand_total", abs_tol=1.0):
        findings.append(f)
    findings += tax_findings(inv)
    return findings


def validate_po(po: PurchaseOrder, ctx: CheckContext) -> list[Finding]:
    """Vendor GSTIN and line arithmetic on the PO."""
    findings = gstin_findings("purchase_order.vendor_gstin", po.vendor_gstin)
    for i, line in enumerate(po.line_items):
        if line.amount.value is None:
            continue
        p = f"purchase_order.line_items[{i}]"
        if f := line_amount_check(line.qty, line.rate, line.amount, paths=(f"{p}.qty", f"{p}.rate", f"{p}.amount"),
                                  code="po_arithmetic_error"):
            findings.append(f)
    return findings
