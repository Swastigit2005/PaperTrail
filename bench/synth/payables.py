"""Synthetic payables pairs: GST tax invoice + purchase order, with exact ground truth.

Clean pairs paraphrase invoice descriptions and reuse HSN headings across items (pens and
markers, mouse and keyboard), so description-only or HSN-only matchers raise false alarms.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from bench.synth.data import ITEMS, Item, corrupt_checksum, gstin
from bench.synth.render import SANS, TYPE, TYPE_BOLD, Canvas, font, money, qty_str
from papertrail.core.validators.gstin import STATE_CODES

DISCREPANCIES = ("gstin_mismatch", "invalid_gstin", "rate_above_po", "qty_over_po", "line_not_on_po",
                 "wrong_tax_split", "total_mismatch")  # fmt: skip


@dataclass
class PayablesCase:
    invoice: dict[str, Any]
    po: dict[str, Any]
    discrepancies: list[dict[str, Any]] = field(default_factory=list)


def _po_number(rng: random.Random) -> str:
    return rng.choice([f"PO-2026-{rng.randint(1, 9999):04d}", f"45000{rng.randint(10000, 99999)}",
                       f"PO/{rng.choice(['ACM', 'BLR', 'MUM', 'HYD'])}/26-27/{rng.randint(1, 999):03d}"])


def build_case(rng: random.Random, fake, discrepancies: list[str]) -> PayablesCase:
    """Generate truth for one invoice + PO pair with the requested discrepancies."""
    states = [s for s in STATE_CODES if s not in ("97", "99", "25", "28")]
    seller_state = rng.choice(states)
    buyer_state = seller_state if rng.random() < 0.5 else rng.choice([s for s in states if s != seller_state])
    seller_gstin, buyer_gstin = gstin(rng, seller_state), gstin(rng, buyer_state)
    vendor, buyer = fake.company(), fake.company()
    po_no = _po_number(rng)
    po_date = date(2026, 4, 1) + timedelta(days=rng.randint(0, 130))
    inv_date = po_date + timedelta(days=rng.randint(3, 30))

    chosen: list[Item] = rng.sample(ITEMS, rng.randint(2, 5))
    po_lines = []
    for it in chosen:
        qty = float(rng.choice([1, 2, 4, 5, 10, 12, 20, 25, 50]) if it.unit != "Month" else 1)
        rate = float(round(rng.uniform(*it.rate_range)))
        po_lines.append({"description": it.po_desc, "hsn": it.hsn, "qty": qty, "rate": rate,
                         "amount": round(qty * rate, 2), "_item": it})

    inv_lines = []
    for pl in po_lines:
        if len(po_lines) > 2 and rng.random() < 0.15:
            continue  # not delivered yet: partial invoice
        it: Item = pl["_item"]
        desc = rng.choice(it.inv_variants) if rng.random() < 0.65 else it.po_desc
        qty = pl["qty"] if rng.random() < 0.8 else max(1.0, float(int(pl["qty"] * rng.uniform(0.4, 0.9))))
        rate = pl["rate"] if rng.random() < 0.85 else round(pl["rate"] * rng.uniform(0.95, 0.99), 2)
        inv_lines.append({"description": desc, "hsn": it.hsn, "qty": qty, "rate": rate, "gst_rate": it.gst,
                          "_po_index": po_lines.index(pl)})

    seeded: list[dict[str, Any]] = []
    split_override: str | None = None
    total_delta = 0.0
    invoice_seller_gstin = seller_gstin
    invoice_buyer_gstin = buyer_gstin
    for kind in discrepancies:
        if kind == "gstin_mismatch":
            invoice_seller_gstin = gstin(rng, seller_state)
            seeded.append({"type": kind, "fields": ["invoice.seller_gstin", "purchase_order.vendor_gstin"],
                           "detail": "invoice issued under a different GSTIN than the PO vendor"})
        elif kind == "invalid_gstin":
            invoice_buyer_gstin = corrupt_checksum(buyer_gstin, rng)
            seeded.append({"type": kind, "fields": ["invoice.buyer_gstin"], "detail": "buyer GSTIN checksum broken"})
        elif kind == "rate_above_po":
            ln = rng.choice([x for x in inv_lines if x["_po_index"] is not None])
            ln["rate"] = round(po_lines[ln["_po_index"]]["rate"] * rng.uniform(1.08, 1.3), 2)
            seeded.append({"type": kind, "_line": ln, "_attr": "rate", "detail": "rate above PO"})
        elif kind == "qty_over_po":
            ln = rng.choice([x for x in inv_lines if x["_po_index"] is not None])
            ordered = po_lines[ln["_po_index"]]["qty"]
            ln["qty"] = ordered + float(rng.randint(1, max(2, int(ordered * 0.5))))
            seeded.append({"type": kind, "_line": ln, "_attr": "qty",
                           "detail": f"{ln['qty']:g} billed vs {ordered:g} ordered"})
        elif kind == "line_not_on_po":
            hsns = {pl["hsn"] for pl in po_lines}
            extra = rng.choice([it for it in ITEMS if it.hsn not in hsns])
            qty = float(rng.choice([1, 2, 5, 10]))
            ln = {"description": rng.choice(extra.inv_variants), "hsn": extra.hsn, "qty": qty,
                  "rate": float(round(rng.uniform(*extra.rate_range))), "gst_rate": extra.gst, "_po_index": None}
            inv_lines.insert(rng.randint(0, len(inv_lines)), ln)
            seeded.append({"type": kind, "_line": ln, "_attr": "description",
                           "detail": f"{ln['description']} not ordered"})
        elif kind == "wrong_tax_split":
            split_override = "igst" if seller_state == buyer_state else "cgst_sgst"
            seeded.append({"type": kind, "fields": ["invoice.cgst", "invoice.sgst", "invoice.igst"],
                           "detail": f"{'IGST on intra' if split_override == 'igst' else 'CGST/SGST on inter'}-state"})
        elif kind == "total_mismatch":
            total_delta = rng.choice([-1, 1]) * float(round(rng.uniform(100, 5000)))
            seeded.append({"type": kind, "fields": ["invoice.grand_total"], "detail": f"total off by {total_delta:+g}"})
        else:
            raise ValueError(kind)

    for ln in inv_lines:
        ln["amount"] = round(ln["qty"] * ln["rate"], 2)
    taxable = round(sum(ln["amount"] for ln in inv_lines), 2)
    tax = round(sum(ln["amount"] * ln["gst_rate"] / 100 for ln in inv_lines), 2)
    intra = seller_state == buyer_state
    mode = split_override or ("cgst_sgst" if intra else "igst")
    cgst = sgst = round(tax / 2, 2) if mode == "cgst_sgst" else None
    igst = tax if mode == "igst" else None
    grand = round(taxable + tax + total_delta, 2)

    invoice = {
        "seller_name": vendor, "seller_gstin": invoice_seller_gstin, "buyer_name": buyer,
        "buyer_gstin": invoice_buyer_gstin, "invoice_number": f"{rng.choice(['INV', 'TI', 'GST'])}/26-27/"
        f"{rng.randint(1, 4999):04d}", "invoice_date": inv_date.isoformat(), "po_reference": po_no,
        "place_of_supply": f"{STATE_CODES[buyer_state]} ({buyer_state})",
        "line_items": [{k: v for k, v in ln.items() if not k.startswith("_")} for ln in inv_lines],
        "taxable_value": taxable, "cgst": cgst, "sgst": sgst, "igst": igst, "grand_total": grand,
    }
    po = {
        "po_number": po_no, "po_date": po_date.isoformat(), "buyer_name": buyer, "vendor_name": vendor,
        "vendor_gstin": seller_gstin,
        "line_items": [{k: v for k, v in pl.items() if not k.startswith("_")} for pl in po_lines],
        "total": round(sum(pl["amount"] for pl in po_lines), 2),
    }
    # line-level refs resolve against the final line order (a later insertion may shift indices)
    for s in seeded:
        if "_line" in s:
            line, attr = s.pop("_line"), s.pop("_attr")
            s["fields"] = [f"invoice.line_items[{next(i for i, x in enumerate(inv_lines) if x is line)}].{attr}"]
    return PayablesCase(invoice, po, seeded)


# --- rendering --------------------------------------------------------------------------------


def _ddmmyyyy(iso: str, sep: str = "/") -> str:
    y, m, d = iso.split("-")
    return f"{d}{sep}{m}{sep}{y}"


def render_invoice(t: dict[str, Any], rng: random.Random) -> tuple[Canvas, str]:
    """GST tax invoice: 'boxed' (classic) or 'modern' layout."""
    template = rng.choice(["boxed", "modern"])
    n = len(t["line_items"])
    c = Canvas(1700, 1500 + 70 * n, rng)
    f, fb, small = font(SANS, 30), font(SANS, 32), font(SANS, 26)
    date_s = _ddmmyyyy(t["invoice_date"], rng.choice(["/", "-", "."]))
    if template == "boxed":
        c.text((850, 30), "TAX INVOICE", font(SANS, 46), anchor="ma")
        c.rect((40, 100, 1660, 420))
        c.draw.line((850, 100, 850, 420), fill=(25, 25, 30), width=2)
        c.text((60, 115), t["seller_name"], fb, path="seller_name")
        c.text((60, 165), f"GSTIN: {t['seller_gstin']}", f, path="seller_gstin")
        c.text((60, 260), "Bill To:", small)
        c.text((60, 300), t["buyer_name"], fb, path="buyer_name")
        c.text((60, 350), f"GSTIN: {t['buyer_gstin']}", f, path="buyer_gstin")
        c.text((870, 115), f"Invoice No: {t['invoice_number']}", f, path="invoice_number")
        c.text((870, 165), f"Invoice Date: {date_s}", f, path="invoice_date")
        c.text((870, 215), f"Buyer's Order No: {t['po_reference']}", f, path="po_reference")
        c.text((870, 265), f"Place of Supply: {t['place_of_supply']}", f, path="place_of_supply")
        y = 450
    else:
        c.text((60, 40), t["seller_name"], font(SANS, 50), path="seller_name")
        c.text((60, 110), f"GSTIN {t['seller_gstin']}", f, path="seller_gstin")
        c.text((1640, 40), "Tax Invoice", font(SANS, 44), anchor="ra")
        c.text((1640, 110), f"# {t['invoice_number']}", f, anchor="ra", path="invoice_number")
        c.text((1640, 160), f"Dated {date_s}", f, anchor="ra", path="invoice_date")
        c.text((60, 220), "BILLED TO", small, fill=(100, 100, 100))
        c.text((60, 260), t["buyer_name"], fb, path="buyer_name")
        c.text((60, 310), f"GSTIN {t['buyer_gstin']}", f, path="buyer_gstin")
        c.text((1640, 260), f"Order Ref: {t['po_reference']}", f, anchor="ra", path="po_reference")
        y = 400
    cols = (60, 130, 780, 960, 1100, 1300, 1640)
    heads = ("#", "Description", "HSN/SAC", "Qty", "Rate", "GST %", "Amount")
    c.rect((40, y - 10, 1660, y + 50), fill=(232, 236, 240))
    for x, h in zip(cols, heads, strict=True):
        c.text((x, y), h, fb, anchor="ra" if h == "Amount" else "la")
    y += 70
    for i, ln in enumerate(t["line_items"]):
        c.text((cols[0], y), str(i + 1), f)
        c.text((cols[1], y), ln["description"], f, path=f"line_items[{i}].description")
        c.text((cols[2], y), ln["hsn"], f, path=f"line_items[{i}].hsn")
        c.text((cols[3], y), qty_str(ln["qty"]), f, path=f"line_items[{i}].qty")
        c.text((cols[4], y), money(ln["rate"]), f, path=f"line_items[{i}].rate")
        c.text((cols[5], y), f"{ln['gst_rate']:g}%", f, path=f"line_items[{i}].gst_rate")
        c.text((cols[6], y), money(ln["amount"]), f, anchor="ra", path=f"line_items[{i}].amount")
        y += 64
    c.hline(y + 5, 40, 1660)
    y += 40
    rows = [("Taxable Value", t["taxable_value"], "taxable_value")]
    if t["cgst"] is not None:
        rows += [("CGST", t["cgst"], "cgst"), ("SGST", t["sgst"], "sgst")]
    if t["igst"] is not None:
        rows += [("IGST", t["igst"], "igst")]
    rows += [("Grand Total (Rs.)", t["grand_total"], "grand_total")]
    for label, val, path in rows:
        fnt = font(SANS, 36) if path == "grand_total" else f
        c.text((1100, y), label, fnt)
        c.text((1640, y), money(val), fnt, anchor="ra", path=path)
        y += 56
    c.text((60, y + 60), "Certified that the particulars given above are true and correct.", small)
    c.text((1640, y + 140), f"For {t['seller_name']}", small, anchor="ra")
    c.signature(1380, y + 200)
    c.text((1640, y + 250), "Authorised Signatory", small, anchor="ra")
    return c, template


def render_po(t: dict[str, Any], rng: random.Random) -> tuple[Canvas, str]:
    """Purchase order: 'classic' or 'erp' layout."""
    template = rng.choice(["classic", "erp"])
    n = len(t["line_items"])
    c = Canvas(1700, 1300 + 70 * n, rng)
    f, fb = font(SANS if template == "classic" else TYPE, 30), font(SANS if template == "classic" else TYPE_BOLD, 32)
    date_s = _ddmmyyyy(t["po_date"], rng.choice(["/", "-"]))
    c.text((60, 40), t["buyer_name"], font(SANS, 48), path="buyer_name")
    c.text((1640, 40), "PURCHASE ORDER", font(SANS, 44), anchor="ra")
    c.text((1640, 110), f"{'PO No' if template == 'classic' else 'Purchasing Doc.'}: {t['po_number']}", f,
           anchor="ra", path="po_number")
    c.text((1640, 160), f"{'Date' if template == 'classic' else 'Doc. Date'}: {date_s}", f, anchor="ra",
           path="po_date")
    c.text((60, 230), "Vendor:", f)
    c.text((60, 275), t["vendor_name"], fb, path="vendor_name")
    c.text((60, 325), f"Vendor GSTIN: {t['vendor_gstin']}", f, path="vendor_gstin")
    y = 430
    cols = (60, 130, 800, 1000, 1200, 1640)
    heads = ("#", "Item", "HSN", "Qty", "Unit Price", "Amount")
    c.rect((40, y - 10, 1660, y + 50), fill=(236, 236, 236))
    for x, h in zip(cols, heads, strict=True):
        c.text((x, y), h, fb, anchor="ra" if h == "Amount" else "la")
    y += 70
    for i, ln in enumerate(t["line_items"]):
        c.text((cols[0], y), f"{(i + 1) * (10 if template == 'erp' else 1)}", f)
        c.text((cols[1], y), ln["description"], f, path=f"line_items[{i}].description")
        c.text((cols[2], y), ln["hsn"], f, path=f"line_items[{i}].hsn")
        c.text((cols[3], y), qty_str(ln["qty"]), f, path=f"line_items[{i}].qty")
        c.text((cols[4], y), money(ln["rate"]), f, path=f"line_items[{i}].rate")
        c.text((cols[5], y), money(ln["amount"]), f, anchor="ra", path=f"line_items[{i}].amount")
        y += 64
    c.hline(y + 5, 40, 1660)
    c.text((1200, y + 40), "Total (excl. GST)", fb)
    c.text((1640, y + 40), money(t["total"]), fb, anchor="ra", path="total")
    c.text((60, y + 160), "Terms: Delivery within 15 days. Payment 30 days from invoice. Prices exclusive of GST.",
           font(SANS, 24))
    c.signature(1380, y + 260)
    c.text((1640, y + 310), "Authorised by Procurement", font(SANS, 24), anchor="ra")
    return c, template
