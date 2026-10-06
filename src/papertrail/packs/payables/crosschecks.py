"""Invoice-vs-purchase-order checks (three-way-match style, without the goods receipt)."""

from __future__ import annotations

import re
from collections.abc import Mapping

from pydantic import BaseModel

from papertrail.core.crosscheck import CheckContext, CrossCheck
from papertrail.core.findings import Finding, hard, info, soft, unverifiable
from papertrail.core.matching import MatchVerdict
from papertrail.core.validators.gstin import normalize as normalize_gstin
from papertrail.packs.payables.schemas import GSTInvoice, PurchaseOrder

RATE_TOLERANCE = 0.02  # invoice rate may exceed the PO rate by at most 2%


def _docs(docs: Mapping[str, BaseModel]) -> tuple[GSTInvoice, PurchaseOrder]:
    inv, po = docs["invoice"], docs["purchase_order"]
    assert isinstance(inv, GSTInvoice) and isinstance(po, PurchaseOrder)
    return inv, po


def _hsn(code: str | None) -> str | None:
    digits = re.sub(r"\D", "", code or "")
    return digits[:4] if len(digits) >= 4 else None  # compare at heading (4-digit) level


def _po_number(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper().replace("PO NO", "").replace("PONO", ""))


def match_lines(docs: Mapping[str, BaseModel], ctx: CheckContext) -> list[MatchVerdict | None]:
    """For each invoice line: the PO line it bills (memoised per pair of documents)."""
    key = "payables.matches"
    inv, po = _docs(docs)
    memo = ctx.services.get(key)
    if memo is not None and memo[0] is inv and memo[1] is po:
        return memo[2]
    inv_idx = [i for i, line in enumerate(inv.line_items) if line.description.value]
    po_idx = [j for j, line in enumerate(po.line_items) if line.description.value]
    verdicts = ctx.matcher.match(
        [inv.line_items[i].description.value or "" for i in inv_idx],
        [po.line_items[j].description.value or "" for j in po_idx],
        domain="office/industrial goods on an invoice vs a purchase order (paraphrases of the same item match)",
        left_keys=[_hsn(inv.line_items[i].hsn.value) for i in inv_idx],
        right_keys=[_hsn(po.line_items[j].hsn.value) for j in po_idx],
    )
    result: list[MatchVerdict | None] = [None] * len(inv.line_items)
    for v in verdicts:
        right = po_idx[v.right] if v.right is not None else None
        result[inv_idx[v.left]] = v.model_copy(update={"left": inv_idx[v.left], "right": right})
    ctx.services[key] = (inv, po, result)
    return result


def vendor_gstin_matches(docs: Mapping[str, BaseModel], ctx: CheckContext) -> list[Finding]:
    """The invoicing supplier is the vendor the PO was issued to."""
    inv, po = _docs(docs)
    refs = ("invoice.seller_gstin", "purchase_order.vendor_gstin")
    if not inv.seller_gstin.value or not po.vendor_gstin.value:
        return [unverifiable("gstin_mismatch", "cannot compare vendor GSTINs", *refs)]
    if normalize_gstin(inv.seller_gstin.value) != normalize_gstin(po.vendor_gstin.value):
        return [hard("gstin_mismatch", f"invoice seller GSTIN {inv.seller_gstin.value} differs from the PO vendor "
                     f"GSTIN {po.vendor_gstin.value}", *refs)]
    return []


def po_reference_matches(docs: Mapping[str, BaseModel], ctx: CheckContext) -> list[Finding]:
    """The invoice quotes this PO's number."""
    inv, po = _docs(docs)
    refs = ("invoice.po_reference", "purchase_order.po_number")
    if not inv.po_reference.value or not po.po_number.value:
        return [unverifiable("po_reference_mismatch", "PO reference missing on invoice or PO", *refs)]
    if _po_number(inv.po_reference.value) != _po_number(po.po_number.value):
        return [hard("po_reference_mismatch", f"invoice references PO '{inv.po_reference.value}', this PO is "
                     f"'{po.po_number.value}'", *refs)]
    return []


def lines_on_po(docs: Mapping[str, BaseModel], ctx: CheckContext) -> list[Finding]:
    """Every invoice line corresponds to a PO line; matched lines should share the HSN heading."""
    inv, po = _docs(docs)
    findings: list[Finding] = []
    for i, (line, v) in enumerate(zip(inv.line_items, match_lines(docs, ctx), strict=True)):
        ref = f"invoice.line_items[{i}].description"
        if line.description.value is None:
            findings.append(unverifiable("line_not_on_po", f"{ref} unreadable; cannot match to the PO", ref))
        elif v is None or v.right is None:
            findings.append(hard("line_not_on_po", f"invoice line '{line.description.value}' is not on the PO", ref,
                                 match_method=v.method if v else "none"))
        else:
            a, b = _hsn(line.hsn.value), _hsn(po.line_items[v.right].hsn.value)
            if a and b and a != b:
                findings.append(soft("hsn_mismatch", f"invoice line {i} HSN {line.hsn.value} vs PO line {v.right} HSN "
                                     f"{po.line_items[v.right].hsn.value}", f"invoice.line_items[{i}].hsn",
                                     f"purchase_order.line_items[{v.right}].hsn"))
    return findings


def quantities_within_po(docs: Mapping[str, BaseModel], ctx: CheckContext) -> list[Finding]:
    """Billed quantity (summed over invoice lines mapped to the same PO line) <= ordered quantity."""
    inv, po = _docs(docs)
    billed: dict[int, tuple[float, list[int]]] = {}
    for i, (line, v) in enumerate(zip(inv.line_items, match_lines(docs, ctx), strict=True)):
        if v is None or v.right is None or line.qty.value is None:
            continue
        total, idx = billed.get(v.right, (0.0, []))
        billed[v.right] = (total + line.qty.value, [*idx, i])
    findings: list[Finding] = []
    for j, (qty, idx) in billed.items():
        ordered = po.line_items[j].qty.value
        refs = [*(f"invoice.line_items[{i}].qty" for i in idx), f"purchase_order.line_items[{j}].qty"]
        if ordered is None:
            findings.append(unverifiable("qty_over_po", f"PO line {j} quantity unreadable", *refs))
        elif qty > ordered + 1e-9:
            findings.append(hard("qty_over_po", f"invoice bills {qty:g} of '{po.line_items[j].description.value}', "
                                 f"PO ordered {ordered:g}", *refs))
    return findings


def rates_within_po(docs: Mapping[str, BaseModel], ctx: CheckContext) -> list[Finding]:
    """Invoice unit rate within RATE_TOLERANCE above the agreed PO rate."""
    inv, po = _docs(docs)
    findings: list[Finding] = []
    for i, (line, v) in enumerate(zip(inv.line_items, match_lines(docs, ctx), strict=True)):
        if v is None or v.right is None:
            continue
        po_line = po.line_items[v.right]
        refs = (f"invoice.line_items[{i}].rate", f"purchase_order.line_items[{v.right}].rate")
        if line.rate.value is None or po_line.rate.value is None:
            findings.append(unverifiable("rate_above_po", f"rate unreadable for invoice line {i}", *refs))
        elif line.rate.value > po_line.rate.value * (1 + RATE_TOLERANCE) + 0.01:
            pct = (line.rate.value / po_line.rate.value - 1) * 100 if po_line.rate.value else float("inf")
            findings.append(hard("rate_above_po", f"invoice rate {line.rate.value:,.2f} for '{line.description.value}' "
                                 f"is {pct:.1f}% above the PO rate {po_line.rate.value:,.2f}", *refs))
        elif line.rate.value < po_line.rate.value - 0.01:
            findings.append(info("rate_below_po", f"invoice line {i} rate is below the PO rate", *refs))
    return findings


CHECKS = (
    CrossCheck("gstin_mismatch", "Invoice seller GSTIN equals the PO vendor GSTIN", vendor_gstin_matches),
    CrossCheck("po_reference_mismatch", "Invoice references this PO number", po_reference_matches),
    CrossCheck("line_not_on_po", "Every invoice line is on the PO (description + HSN)", lines_on_po),
    CrossCheck("qty_over_po", "Billed quantity does not exceed the ordered quantity", quantities_within_po),
    CrossCheck("rate_above_po", f"Invoice rate within {RATE_TOLERANCE:.0%} of the PO rate", rates_within_po),
)
