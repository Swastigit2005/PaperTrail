"""Synthetic claims pairs: prescription + pharmacy bill, with exact ground truth.

Clean pairs deliberately mix brand and generic names (including long-tail brands that the pack's
brand map does not know) and spell the patient's name differently on the two documents, so a
naive matcher raises false alarms. Discrepant pairs carry 1-2 seeded discrepancies.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from bench.synth.data import DIAGNOSES, DRUGS, DURATIONS, FREQUENCIES, Drug, identity
from bench.synth.render import HANDWRITING, MONO, MONO_BOLD, SANS, TYPE, TYPE_BOLD, Canvas, font, money, qty_str
from papertrail.core.validators.fuzzy import name_similarity
from papertrail.packs.claims.posology import allowed_units

DISCREPANCIES = ("unprescribed_medicine", "patient_name_mismatch", "bill_before_prescription",
                 "implausible_quantity", "arithmetic_error")  # fmt: skip


@dataclass
class RxLine:
    drug: Drug
    name: str  # as written on the prescription
    strength: str | None
    dosage: str | None
    frequency: str
    per_day: int
    duration: str
    days: int


@dataclass
class BillItem:
    drug: Drug
    medicine: str
    batch: str
    qty: float
    rate: float
    amount: float


@dataclass
class ClaimsCase:
    rx: dict[str, Any]
    bill: dict[str, Any]
    discrepancies: list[dict[str, Any]] = field(default_factory=list)


def _name_on_rx(drug: Drug, rng: random.Random) -> tuple[str, str | None]:
    roll = rng.random()
    if roll < 0.45 or not (drug.brands or drug.longtail):
        return drug.generic, drug.strength
    pool = list(drug.brands) + list(drug.longtail)  # long-tail brands weighted like known brands
    return rng.choice(pool), None


def _name_on_bill(drug: Drug, rx_name: str, rng: random.Random) -> str:
    """Bill naming: often a (different) brand, sometimes the generic, sometimes the same as the Rx."""
    form = drug.form
    roll = rng.random()
    if roll < 0.25:
        return f"{rx_name} {form}" if rx_name != drug.generic else f"{drug.generic} {drug.strength} {form}"
    if roll < 0.45:
        return f"{drug.generic.upper()} {drug.strength.replace(' ', '').upper()} {form.upper()}"
    pool = [b for b in list(drug.brands) + list(drug.longtail) if b != rx_name] or [drug.generic]
    return f"{rng.choice(pool)} {form}"


def build_case(rng: random.Random, fake, discrepancies: list[str]) -> ClaimsCase:
    """Generate truth for one prescription + bill pair with the requested discrepancies."""
    patient = identity(fake, rng)
    doctor = f"Dr. {fake.first_name()} {fake.last_name()}"
    rx_date = date(2026, 6, 1) + timedelta(days=rng.randint(0, 110))
    drugs = rng.sample(DRUGS, rng.randint(2, 4))
    lines: list[RxLine] = []
    for d in drugs:
        name, strength = _name_on_rx(d, rng)
        freq, per_day = rng.choice(FREQUENCIES)
        dur, days = rng.choice(DURATIONS)
        dosage = rng.choice([None, None, "1 tab" if d.form == "Tab" else "1 cap"])
        lines.append(RxLine(d, name, strength, dosage, freq, per_day, dur, days))

    bill_date = rx_date + timedelta(days=rng.randint(0, 3))
    bill_patient = rng.choice(patient.variants)
    items: list[BillItem] = []
    for ln in lines:
        need = ln.per_day * ln.days
        strip = rng.choice([10, 15])
        qty = float(rng.choice([need, math.ceil(need / strip) * strip]))
        rate = round(ln.drug.unit_rate * rng.uniform(0.9, 1.25), 2)
        items.append(BillItem(ln.drug, _name_on_bill(ln.drug, ln.name, rng), f"B{rng.randint(10000, 99999)}",
                              qty, rate, round(qty * rate, 2)))
    rng.shuffle(items)
    discount = round(sum(i.amount for i in items) * 0.1, 2) if rng.random() < 0.3 else None

    seeded: list[dict[str, Any]] = []
    total_delta = 0.0
    for kind in discrepancies:
        if kind == "unprescribed_medicine":
            used = {ln.drug.generic for ln in lines}
            extra = rng.choice([d for d in DRUGS if d.generic not in used])
            qty = float(rng.choice([10, 15]))
            rate = round(extra.unit_rate * rng.uniform(0.9, 1.2), 2)
            item = BillItem(extra, f"{rng.choice(list(extra.brands) or [extra.generic])} {extra.form}",
                            f"B{rng.randint(10000, 99999)}", qty, rate, round(qty * rate, 2))
            pos = rng.randint(0, len(items))
            items.insert(pos, item)
            seeded.append({"type": kind, "_line": item, "_attr": "medicine",
                           "detail": f"{item.medicine} not prescribed"})
        elif kind == "patient_name_mismatch":
            while True:
                other = identity(fake, rng)
                if name_similarity(other.full, patient.full) < 50:
                    break
            bill_patient = other.full
            seeded.append({"type": kind, "fields": ["bill.patient_name", "prescription.patient_name"],
                           "detail": f"{other.full} vs {patient.full}"})
        elif kind == "bill_before_prescription":
            bill_date = rx_date - timedelta(days=rng.randint(2, 12))
            seeded.append({"type": kind, "fields": ["bill.date", "prescription.date"], "detail": "bill predates Rx"})
        elif kind == "implausible_quantity":
            prescribed = [i for i in items if any(i.drug is ln.drug for ln in lines)]
            item = rng.choice(prescribed)
            ln = next(ln for ln in lines if ln.drug is item.drug)
            limit = allowed_units(ln.per_day * ln.days)
            item.qty = float(math.ceil(limit * rng.uniform(2.0, 4.0) / 10) * 10)
            item.amount = round(item.qty * item.rate, 2)
            seeded.append({"type": kind, "_line": item, "_attr": "qty",
                           "detail": f"{item.qty:g} billed, course needs {ln.per_day * ln.days}"})
        elif kind == "arithmetic_error":
            if rng.random() < 0.5:
                item = rng.choice(items)
                item.amount = round(item.amount + rng.choice([-1, 1]) * max(20.0, item.amount * rng.uniform(0.2, 0.5)),
                                    2)
                seeded.append({"type": kind, "_line": item, "_attr": "amount", "detail": "line amount != qty x rate"})
            else:
                total_delta = rng.choice([-1, 1]) * round(rng.uniform(50, 400), 0)
                seeded.append({"type": kind, "fields": ["bill.total"], "detail": f"total off by {total_delta:+g}"})
        else:
            raise ValueError(kind)

    for s in seeded:  # line-level refs resolve against the final line order
        if "_line" in s:
            line, attr = s.pop("_line"), s.pop("_attr")
            s["fields"] = [f"bill.line_items[{next(i for i, x in enumerate(items) if x is line)}].{attr}"]
    total = round(sum(i.amount for i in items) - (discount or 0.0) + total_delta, 2)
    rx_truth = {
        "doctor_name": doctor,
        "registration_number": f"{rng.choice(['KMC', 'MMC', 'DMC', 'TNMC'])} {rng.randint(10000, 99999)}",
        "clinic_name": f"{fake.last_name()} {rng.choice(['Clinic', 'Health Centre', 'Polyclinic', 'Nursing Home'])}",
        "patient_name": patient.full,
        "patient_age": f"{rng.randint(4, 82)} Y",
        "date": rx_date.isoformat(),
        "diagnosis": rng.choice(DIAGNOSES),
        "medicines": [{"name": ln.name, "strength": ln.strength, "dosage": ln.dosage, "frequency": ln.frequency,
                       "duration": ln.duration, "_form": ln.drug.form} for ln in lines],
    }
    bill_truth = {
        "pharmacy_name": f"{fake.last_name()} {rng.choice(['Medicals', 'Pharmacy', 'Chemists', 'Medical Stores'])}",
        "bill_number": f"{rng.choice(['B', 'CB', 'INV'])}-{rng.randint(1000, 99999)}",
        "patient_name": bill_patient,
        "doctor_name": doctor if rng.random() < 0.6 else None,
        "date": bill_date.isoformat(),
        "line_items": [{"medicine": i.medicine, "batch": i.batch, "qty": i.qty, "rate": i.rate, "amount": i.amount}
                       for i in items],
        "discount": discount,
        "total": total,
    }
    return ClaimsCase(rx_truth, bill_truth, seeded)


# --- rendering --------------------------------------------------------------------------------


def _ddmmyyyy(iso: str, sep: str = "/") -> str:
    y, m, d = iso.split("-")
    return f"{d}{sep}{m}{sep}{y}"


def render_prescription(t: dict[str, Any], rng: random.Random) -> tuple[Canvas, str]:
    """Render a prescription with one of three templates. Returns the canvas and template name."""
    template = rng.choice(["letterhead_hand", "pad_hand", "printed"])
    c = Canvas(1200, 1600, rng)
    hw = rng.choice(HANDWRITING)
    size = 46 if hw != "HomemadeApple-Regular.ttf" else 34
    date_s = _ddmmyyyy(t["date"], rng.choice(["/", "-", "."]))
    if template in ("letterhead_hand", "printed"):
        c.text((600, 50), t["clinic_name"], font(SANS, 52), anchor="ma", path="clinic_name")
        c.text((600, 120), f"{t['doctor_name']}, MBBS, MD", font(SANS, 34), anchor="ma", path="doctor_name")
        c.text((600, 168), f"Reg. No: {t['registration_number']}", font(SANS, 28), anchor="ma",
               path="registration_number")
        c.hline(220, width=3)
        y = 260
    else:
        c.text((60, 50), t["clinic_name"], font(TYPE_BOLD, 40), path="clinic_name")
        c.hand((60, 110), t["doctor_name"], hw, size, path="doctor_name")
        c.text((60, 175), "Reg. No:", font(TYPE, 26))
        c.hand((200, 165), t["registration_number"], hw, int(size * 0.8), path="registration_number")
        y = 250
    if template == "printed":
        f = font(SANS, 32)
        c.text((60, y), f"Patient: {t['patient_name']}", f, path="patient_name")
        c.text((760, y), f"Date: {date_s}", f, path="date")
        c.text((60, y + 50), f"Age: {t['patient_age']}", f, path="patient_age")
        c.text((60, y + 100), f"Diagnosis: {t['diagnosis']}", f, path="diagnosis")
        y += 180
        c.text((60, y), "Rx", font(TYPE_BOLD, 56))
        y += 80
        cols = (60, 560, 760, 950)
        for x, h in zip(cols, ("Medicine", "Dose", "Frequency", "Duration"), strict=True):
            c.text((x, y), h, font(SANS, 28), fill=(80, 80, 80))
        y += 50
        c.hline(y - 8, width=1)
        for i, m in enumerate(t["medicines"]):
            name = m["name"] + (f" {m['strength']}" if m["strength"] else "")
            c.text((cols[0], y), name, f, path=f"medicines[{i}].name")
            if m["strength"]:
                c._record(f"medicines[{i}].strength", c.draw.textbbox((cols[0], y), name, font=f))
            c.text((cols[1], y), m["dosage"] or "-", f, path=f"medicines[{i}].dosage" if m["dosage"] else None)
            c.text((cols[2], y), m["frequency"], f, path=f"medicines[{i}].frequency")
            c.text((cols[3], y), m["duration"], f, path=f"medicines[{i}].duration")
            y += 64
    else:
        c.text((60, y + 10), "Name:", font(TYPE, 28))
        c.hand((170, y), t["patient_name"], hw, size, path="patient_name")
        c.text((760, y + 10), "Date:", font(TYPE, 28))
        c.hand((850, y), date_s, hw, size, path="date")
        c.text((60, y + 80), "Age:", font(TYPE, 28))
        c.hand((150, y + 70), t["patient_age"], hw, size, path="patient_age")
        c.text((60, y + 150), "Dx:", font(TYPE, 28))
        c.hand((140, y + 140), t["diagnosis"], hw, size, path="diagnosis")
        y += 250
        c.text((60, y), "Rx", font(TYPE_BOLD, 64))
        y += 100
        for i, m in enumerate(t["medicines"]):
            form = f"{m['_form']}."
            name = m["name"] + (f" {m['strength']}" if m["strength"] else "")
            x = 90
            box = c.hand((x, y), f"{i + 1}. {form} {name}", hw, size, path=f"medicines[{i}].name")
            if m["strength"]:
                c._record(f"medicines[{i}].strength", box)
            line2 = "   ".join(p for p in (m["dosage"], m["frequency"], m["duration"]) if p)
            c.hand((150, y + size + 14), line2, hw, int(size * 0.9), path=f"medicines[{i}].frequency")
            c._record(f"medicines[{i}].duration", c.boxes[f"medicines[{i}].frequency"])
            if m["dosage"]:
                c._record(f"medicines[{i}].dosage", c.boxes[f"medicines[{i}].frequency"])
            y += size * 2 + 50
    c.signature(820, 1450)
    c.text((820, 1490), "Signature", font(TYPE, 24))
    return c, template


def render_bill(t: dict[str, Any], rng: random.Random) -> tuple[Canvas, str]:
    """Render a pharmacy bill: thermal receipt or A5 table."""
    template = rng.choice(["thermal", "table"])
    date_s = _ddmmyyyy(t["date"], rng.choice(["/", "-"]))
    if template == "thermal":
        n = len(t["line_items"])
        c = Canvas(720, 900 + 110 * n, rng, paper=(250, 250, 250))
        f, fb = font(MONO, 26), font(MONO_BOLD, 30)
        c.text((360, 30), t["pharmacy_name"].upper(), fb, anchor="ma", path="pharmacy_name")
        c.text((360, 75), "Retail Chemist & Druggist", f, anchor="ma")
        c.text((30, 130), f"Bill No: {t['bill_number']}", f, path="bill_number")
        c.text((30, 170), f"Date: {date_s}", f, path="date")
        c.text((30, 210), f"Patient: {t['patient_name']}", f, path="patient_name")
        y = 250
        if t["doctor_name"]:
            c.text((30, y), f"Dr: {t['doctor_name']}", f, path="doctor_name")
            y += 40
        c.text((30, y), "-" * 44, f)
        y += 36
        c.text((30, y), "Item / Batch", f)
        c.text((690, y), "Qty  Rate   Amount", f, anchor="ra")
        y += 36
        c.text((30, y), "-" * 44, f)
        y += 40
        for i, it in enumerate(t["line_items"]):
            c.text((30, y), it["medicine"], f, path=f"line_items[{i}].medicine")
            y += 36
            c.text((30, y), it["batch"], font(MONO, 22), path=f"line_items[{i}].batch")
            c.text((400, y), qty_str(it["qty"]), f, anchor="ra", path=f"line_items[{i}].qty")
            c.text((540, y), money(it["rate"]), f, anchor="ra", path=f"line_items[{i}].rate")
            c.text((690, y), money(it["amount"]), f, anchor="ra", path=f"line_items[{i}].amount")
            y += 60
        c.text((30, y), "-" * 44, f)
        y += 40
        if t["discount"]:
            c.text((30, y), "Discount", f)
            c.text((690, y), money(t["discount"]), f, anchor="ra", path="discount")
            y += 40
        c.text((30, y), "NET AMOUNT Rs.", fb)
        c.text((690, y), money(t["total"]), fb, anchor="ra", path="total")
        c.text((360, y + 80), "Get well soon!", f, anchor="ma")
        return c, template

    c = Canvas(1600, 1100, rng)
    f, fb = font(SANS, 30), font(SANS, 34)
    c.text((60, 40), t["pharmacy_name"], font(SANS, 48), path="pharmacy_name")
    c.text((1540, 50), "TAX INVOICE / BILL", fb, anchor="ra")
    c.text((60, 120), f"Bill No.: {t['bill_number']}", f, path="bill_number")
    c.text((1540, 120), f"Date: {date_s}", f, anchor="ra", path="date")
    c.text((60, 170), f"Patient Name: {t['patient_name']}", f, path="patient_name")
    if t["doctor_name"]:
        c.text((900, 170), f"Ref. Dr.: {t['doctor_name']}", f, path="doctor_name")
    y = 240
    cols = (60, 140, 760, 1000, 1180, 1540)
    c.rect((40, y - 10, 1560, y + 50), fill=(235, 235, 235))
    for x, h in zip(cols, ("Sr", "Product", "Batch", "Qty", "Rate", "Amount"), strict=True):
        c.text((x, y), h, fb, anchor="ra" if h == "Amount" else "la")
    y += 70
    for i, it in enumerate(t["line_items"]):
        c.text((cols[0], y), str(i + 1), f)
        c.text((cols[1], y), it["medicine"], f, path=f"line_items[{i}].medicine")
        c.text((cols[2], y), it["batch"], f, path=f"line_items[{i}].batch")
        c.text((cols[3], y), qty_str(it["qty"]), f, path=f"line_items[{i}].qty")
        c.text((cols[4], y), money(it["rate"]), f, path=f"line_items[{i}].rate")
        c.text((cols[5], y), money(it["amount"]), f, anchor="ra", path=f"line_items[{i}].amount")
        y += 56
    c.hline(y + 10, 40, 1560)
    y += 40
    if t["discount"]:
        c.text((1180, y), "Discount:", f)
        c.text((1540, y), money(t["discount"]), f, anchor="ra", path="discount")
        y += 50
    c.text((1180, y), "Net Payable:", fb)
    c.text((1540, y), money(t["total"]), fb, anchor="ra", path="total")
    c.text((60, 1020), "Goods once sold will not be taken back. Subject to local jurisdiction.", font(SANS, 22))
    return c, template
