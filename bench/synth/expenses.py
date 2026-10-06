"""Synthetic expenses documents (receipt + expense claim form) for the demo samples. Not benchmarked."""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

from bench.synth.data import identity
from bench.synth.render import HANDWRITING, MONO, MONO_BOLD, SANS, TYPE, Canvas, font, money

MENUS = (("Masala dosa", 140.0), ("Filter coffee", 60.0), ("Paneer tikka", 320.0), ("Veg biryani", 280.0),
         ("Fresh lime soda", 90.0), ("Butter naan", 70.0), ("Dal makhani", 260.0), ("Gulab jamun", 110.0))
MERCHANTS = ("Cafe Aroma", "Udupi Grand", "Spice Route Kitchen", "Hotel Saravana", "The Chai Stop")


def build_case(rng: random.Random, fake, claim_extra: float = 0.0) -> dict[str, Any]:
    """Receipt + claim truth. ``claim_extra`` > 0 makes the claimed amount exceed the receipt."""
    person = identity(fake, rng)
    merchant = rng.choice(MERCHANTS)
    day = date(2026, 9, 1) + timedelta(days=rng.randint(0, 20))
    items = [{"description": n, "amount": a} for n, a in rng.sample(MENUS, rng.randint(2, 4))]
    subtotal = sum(i["amount"] for i in items)
    tax = round(subtotal * 0.05, 2)
    total = round(subtotal + tax, 2)
    receipt = {"merchant": merchant, "receipt_number": f"R-{rng.randint(1000, 9999)}", "date": day.isoformat(),
               "items": items, "tax": tax, "total": total, "payment_mode": rng.choice(["UPI", "Card", "Cash"])}
    claim = {"employee_name": person.full, "employee_id": f"E-{rng.randint(1000, 9999)}",
             "claim_date": (day + timedelta(days=rng.randint(2, 10))).isoformat(), "expense_date": day.isoformat(),
             "merchant": merchant, "category": "Meals", "amount_claimed": round(total + claim_extra, 2),
             "purpose": rng.choice(["Client lunch", "Team offsite meal", "Vendor meeting"])}
    return {"receipt": receipt, "expense_claim": claim}


def render_receipt(t: dict[str, Any], rng: random.Random) -> Canvas:
    """Thermal-style restaurant receipt."""
    c = Canvas(700, 820 + 50 * len(t["items"]), rng, paper=(250, 250, 248))
    f, fb = font(MONO, 26), font(MONO_BOLD, 30)
    y, (yy, mm, dd) = 30, t["date"].split("-")
    c.text((350, y), t["merchant"].upper(), fb, anchor="ma", path="merchant")
    c.text((350, y + 45), "GSTIN on request | Thank you!", f, anchor="ma")
    c.text((30, y + 110), f"Bill: {t['receipt_number']}", f, path="receipt_number")
    c.text((670, y + 110), f"{dd}/{mm}/{yy}", f, anchor="ra", path="date")
    y += 160
    c.text((30, y), "-" * 42, f)
    y += 40
    for i, it in enumerate(t["items"]):
        c.text((30, y), it["description"], f, path=f"items[{i}].description")
        c.text((670, y), money(it["amount"]), f, anchor="ra", path=f"items[{i}].amount")
        y += 50
    c.text((30, y), "-" * 42, f)
    c.text((30, y + 40), "GST 5%", f)
    c.text((670, y + 40), money(t["tax"]), f, anchor="ra", path="tax")
    c.text((30, y + 90), "TOTAL", fb)
    c.text((670, y + 90), money(t["total"]), fb, anchor="ra", path="total")
    c.text((30, y + 150), f"Paid by {t['payment_mode']}", f, path="payment_mode")
    return c


def render_claim(t: dict[str, Any], rng: random.Random) -> Canvas:
    """Printed expense claim form filled in by hand."""
    c = Canvas(1200, 1100, rng)
    hw = rng.choice(HANDWRITING[:3])
    c.text((600, 40), "EXPENSE CLAIM FORM", font(SANS, 46), anchor="ma")
    c.text((600, 105), "Employee reimbursement", font(SANS, 26), anchor="ma")
    rows = (("Employee name", "employee_name"), ("Employee ID", "employee_id"), ("Claim date", "claim_date"),
            ("Expense date", "expense_date"), ("Merchant", "merchant"), ("Category", "category"),
            ("Amount claimed (Rs.)", "amount_claimed"), ("Purpose", "purpose"))
    y = 190
    for label, key in rows:
        c.text((60, y + 12), f"{label}:", font(TYPE, 30))
        c.hline(y + 58, 420, 1140, width=1)
        value = t[key]
        if key.endswith("date"):
            yy, mm, dd = value.split("-")
            value = f"{dd}-{mm}-{yy}"
        elif key == "amount_claimed":
            value = money(value)
        c.hand((440, y), str(value), hw, 44, path=key)
        y += 95
    c.text((60, y + 60), "Approved by: ____________________", font(TYPE, 28))
    c.signature(820, y + 40)
    return c
