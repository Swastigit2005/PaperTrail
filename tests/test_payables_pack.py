"""Payables pack: tax split, slabs, arithmetic, and a failing case per invoice-vs-PO check."""

from datetime import date

from papertrail.core.crosscheck import CheckContext
from papertrail.core.decision import Outcome
from papertrail.core.engine import evaluate
from papertrail.core.fields import known, missing
from papertrail.core.matching import Matcher
from papertrail.core.pack import get_pack
from papertrail.core.validators.gstin import make_gstin
from papertrail.packs.payables.schemas import GSTInvoice, InvoiceLine, POLine, PurchaseOrder
from papertrail.packs.payables.validators import valid_slabs
from tests.conftest import fake_llm

TODAY = date(2026, 10, 6)
SELLER = make_gstin("29", "AABCS1234F")  # Karnataka
BUYER_KA = make_gstin("29", "AAACB5678K")
BUYER_MH = make_gstin("27", "AAACB5678K")


def inv_line(desc, hsn, qty, rate, gst=18.0):
    return InvoiceLine(description=known(desc), hsn=known(hsn), qty=known(float(qty)), rate=known(float(rate)),
                       amount=known(round(qty * rate, 2)), gst_rate=known(gst))


def po_line(desc, hsn, qty, rate):
    return POLine(description=known(desc), hsn=known(hsn), qty=known(float(qty)), rate=known(float(rate)),
                  amount=known(round(qty * rate, 2)))


def invoice(lines=None, seller=SELLER, buyer=BUYER_KA, po_ref="PO-2026-0042", rate=18.0, split="auto",
            day=date(2026, 9, 15), grand_delta=0.0):
    lines = lines if lines is not None else [inv_line("Copier paper A4 500 sheets", "4802", 50, 240.0),
                                             inv_line("Toner cartridge HP 88A", "8443", 4, 2500.0)]
    taxable = round(sum(x.amount.value for x in lines), 2)
    tax = round(taxable * rate / 100, 2)
    intra = split == "intra" or (split == "auto" and seller[:2] == buyer[:2])
    cgst = sgst = round(tax / 2, 2) if intra else None
    igst = None if intra else tax
    return GSTInvoice(
        seller_name=known("Sharma Stationers Pvt Ltd"), seller_gstin=known(seller), buyer_name=known("Acme Labs"),
        buyer_gstin=known(buyer), invoice_number=known("INV/26-27/118"), invoice_date=known(day),
        po_reference=known(po_ref), place_of_supply=missing(), line_items=lines, taxable_value=known(taxable),
        cgst=known(cgst) if cgst is not None else missing(), sgst=known(sgst) if sgst is not None else missing(),
        igst=known(igst) if igst is not None else missing(), grand_total=known(round(taxable + tax + grand_delta, 2)),
    )


def po(lines=None, vendor=SELLER):
    lines = lines if lines is not None else [po_line("A4 paper ream (500 sheets)", "4802", 50, 240.0),
                                             po_line("HP 88A black toner", "8443", 5, 2500.0)]
    return PurchaseOrder(po_number=known("PO-2026-0042"), po_date=known(date(2026, 9, 1)), buyer_name=known("Acme"),
                         vendor_name=known("Sharma Stationers"), vendor_gstin=known(vendor), line_items=lines,
                         total=missing())


def run(inv, order, matcher=None):
    ctx = CheckContext(matcher=matcher or Matcher(), today=TODAY)
    validations, outcomes, decision = evaluate(get_pack("payables"), {"invoice": inv, "purchase_order": order}, ctx)
    codes = {f.code for f in validations} | {f.code for o in outcomes for f in o.findings}
    return decision, codes


def test_slab_regimes():
    assert 12.0 in valid_slabs(date(2025, 9, 21)) and 12.0 not in valid_slabs(date(2025, 9, 22))
    assert 40.0 in valid_slabs(date(2026, 1, 1)) and 28.0 not in valid_slabs(date(2026, 1, 1))


def test_clean_pair_with_paraphrased_lines_approves():
    decision, codes = run(invoice(), po())
    assert decision.outcome == Outcome.APPROVE, decision.reasons
    assert codes <= {"rate_below_po"}


def test_inter_state_igst_is_valid():
    assert run(invoice(buyer=BUYER_MH), po())[0].outcome == Outcome.APPROVE


def test_wrong_tax_split_both_directions():
    assert "wrong_tax_split" in run(invoice(buyer=BUYER_MH, split="intra"), po())[1]
    assert "wrong_tax_split" in run(invoice(buyer=BUYER_KA, split="inter"), po())[1]


def test_invalid_gstin_checksum():
    bad = SELLER[:-1] + ("A" if SELLER[-1] != "A" else "B")
    decision, codes = run(invoice(seller=bad), po(vendor=bad))
    assert decision.outcome == Outcome.FLAG and "invalid_gstin" in codes


def test_gstin_mismatch_with_po():
    other = make_gstin("29", "AAKCX9999Q")
    decision, codes = run(invoice(seller=other), po())
    assert decision.outcome == Outcome.FLAG and "gstin_mismatch" in codes


def test_po_reference_mismatch_and_normalisation():
    assert "po_reference_mismatch" in run(invoice(po_ref="PO-2026-0043"), po())[1]
    assert "po_reference_mismatch" not in run(invoice(po_ref="PO No. PO 2026 0042"), po())[1]


def test_rate_above_po():
    lines = [inv_line("Copier paper A4 500 sheets", "4802", 50, 260.0), inv_line("Toner cartridge HP 88A", "8443", 4,
                                                                                   2500.0)]
    decision, codes = run(invoice(lines=lines), po())
    assert decision.outcome == Outcome.FLAG and "rate_above_po" in codes
    within = [inv_line("Copier paper A4 500 sheets", "4802", 50, 244.0)]
    assert "rate_above_po" not in run(invoice(lines=within), po())[1]  # +1.7% within tolerance


def test_qty_over_po_summed_across_lines():
    lines = [inv_line("Toner cartridge HP 88A", "8443", 3, 2500.0), inv_line("HP 88A black toner", "8443", 3, 2500.0)]
    decision, codes = run(invoice(lines=lines), po())
    assert "qty_over_po" in codes  # 3 + 3 > 5 ordered


def test_line_not_on_po():
    lines = [inv_line("Copier paper A4 500 sheets", "4802", 50, 240.0), inv_line("Office chair ergonomic", "9401", 1,
                                                                                   7000.0)]
    decision, codes = run(invoice(lines=lines), po())
    assert decision.outcome == Outcome.FLAG and "line_not_on_po" in codes


def test_paraphrase_without_unique_hsn_needs_llm():
    order = po(lines=[po_line("Ball pens blue (box of 10)", "9608", 10, 50.0),
                      po_line("Gel pens black (box of 10)", "9608", 10, 80.0)])
    lines = [inv_line("Reynolds 045 blue ballpoint, pack of 10", "9608", 10, 50.0)]
    _, codes = run(invoice(lines=lines), order)
    assert "line_not_on_po" in codes  # rules: no fuzzy match and HSN 9608 is ambiguous
    llm = fake_llm({"matches": [{"left": 0, "right": 0, "reason": "blue ball pens, pack of 10"}]})
    decision, codes = run(invoice(lines=lines), order, Matcher(mode="rules+llm", llm=llm, model="match-model"))
    assert "line_not_on_po" not in codes and decision.outcome == Outcome.APPROVE


def test_total_mismatch_and_arithmetic():
    decision, codes = run(invoice(grand_delta=500.0), po())
    assert decision.outcome == Outcome.FLAG and "total_mismatch" in codes
    inv = invoice()
    inv.line_items[0].amount = known(13000.0)
    assert "arithmetic_error" in run(inv, po())[1]


def test_invalid_slab_rate():
    decision, codes = run(invoice(rate=12.0, day=date(2026, 9, 15)), po())  # 12% abolished from 22 Sep 2025
    assert "invalid_gst_slab" in codes


def test_unreadable_gstin_is_unverifiable():
    inv = invoice()
    inv.seller_gstin = missing("illegible")
    decision, codes = run(inv, po())
    assert decision.outcome == Outcome.NEEDS_REVIEW and {"invalid_gstin", "gstin_mismatch"} <= codes
