"""Bill-vs-prescription checks for the claims pack."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel

from papertrail.core.crosscheck import CheckContext, CrossCheck
from papertrail.core.findings import Finding, hard, soft, unverifiable
from papertrail.core.matching import MatchVerdict
from papertrail.core.validators.dates import days_between
from papertrail.core.validators.fuzzy import name_similarity
from papertrail.packs.claims.drugs import canonical_drug
from papertrail.packs.claims.posology import allowed_units, expected_units
from papertrail.packs.claims.schemas import MedicalBill, Prescription

MAX_DAYS_AFTER_PRESCRIPTION = 30
NAME_MATCH = 85.0  # >= : same person
NAME_MISMATCH = 60.0  # < : different person (hard); in between: review


def _docs(docs: Mapping[str, BaseModel]) -> tuple[Prescription, MedicalBill]:
    rx, bill = docs["prescription"], docs["bill"]
    assert isinstance(rx, Prescription) and isinstance(bill, MedicalBill)
    return rx, bill


def match_bill_to_prescription(docs: Mapping[str, BaseModel], ctx: CheckContext) -> list[MatchVerdict | None]:
    """For each bill line: which prescription medicine it is (memoised per case on the context)."""
    key = "claims.matches"
    rx, bill = _docs(docs)
    memo = ctx.services.get(key)
    if memo is not None and memo[0] is rx and memo[1] is bill:  # same documents: reuse
        return memo[2]
    rx_idx = [i for i, m in enumerate(rx.medicines) if m.name.value]
    rx_names = [rx.medicines[i].name.value or "" for i in rx_idx]
    bill_idx = [i for i, line in enumerate(bill.line_items) if line.medicine.value]
    bill_names = [bill.line_items[i].medicine.value or "" for i in bill_idx]
    rxnorm = ctx.services.get("rxnorm")
    verdicts = ctx.matcher.match(
        bill_names, rx_names, domain="medicines (brand vs generic names of the same ingredient match)",
        normalize=lambda name: canonical_drug(name, rxnorm),
    )
    result: list[MatchVerdict | None] = [None] * len(bill.line_items)
    for v in verdicts:
        right = rx_idx[v.right] if v.right is not None else None
        result[bill_idx[v.left]] = v.model_copy(update={"left": bill_idx[v.left], "right": right})
    ctx.services[key] = (rx, bill, result)
    return result


def billed_medicines_prescribed(docs: Mapping[str, BaseModel], ctx: CheckContext) -> list[Finding]:
    """Every billed medicine must appear on the prescription (after brand/generic normalisation)."""
    rx, bill = _docs(docs)
    findings: list[Finding] = []
    if not rx.medicines:
        return [unverifiable("unprescribed_medicine", "no medicines could be read from the prescription",
                             "prescription.medicines")]
    for i, (line, verdict) in enumerate(zip(bill.line_items, match_bill_to_prescription(docs, ctx), strict=True)):
        ref = f"bill.line_items[{i}].medicine"
        if line.medicine.value is None:
            findings.append(unverifiable("unprescribed_medicine", f"{ref} unreadable; cannot check it was prescribed",
                                         ref))
        elif verdict is None or verdict.right is None:
            findings.append(hard("unprescribed_medicine",
                                 f"'{line.medicine.value}' is billed but not on the prescription", ref,
                                 match_method=verdict.method if verdict else "none"))
    return findings


def patient_names_match(docs: Mapping[str, BaseModel], ctx: CheckContext) -> list[Finding]:
    """Same patient on both documents (fuzzy: titles, order and initials tolerated)."""
    rx, bill = _docs(docs)
    refs = ("bill.patient_name", "prescription.patient_name")
    a, b = bill.patient_name.value, rx.patient_name.value
    if not a or not b:
        return [unverifiable("patient_name_mismatch", "patient name missing on one document", *refs)]
    score = name_similarity(a, b)
    if score >= NAME_MATCH:
        return []
    if score < NAME_MISMATCH:
        return [hard("patient_name_mismatch", f"bill patient '{a}' vs prescription patient '{b}'", *refs,
                     similarity=score)]
    return [soft("patient_name_mismatch", f"patient names only partly match: '{a}' vs '{b}' ({score:.0f})", *refs,
                 similarity=score)]


def bill_date_valid(docs: Mapping[str, BaseModel], ctx: CheckContext) -> list[Finding]:
    """Bill dated on/after the prescription and within MAX_DAYS_AFTER_PRESCRIPTION days."""
    rx, bill = _docs(docs)
    refs = ("bill.date", "prescription.date")
    if bill.date.value is None or rx.date.value is None:
        return [unverifiable("bill_before_prescription", "cannot compare bill and prescription dates", *refs)]
    gap = days_between(rx.date.value, bill.date.value)
    if gap < 0:
        return [hard("bill_before_prescription",
                     f"bill dated {bill.date.value}, {-gap} day(s) before the prescription ({rx.date.value})", *refs)]
    if gap > MAX_DAYS_AFTER_PRESCRIPTION:
        limit = MAX_DAYS_AFTER_PRESCRIPTION
        return [soft("bill_too_late", f"bill is {gap} days after the prescription (limit {limit})", *refs)]
    return []


def quantities_plausible(docs: Mapping[str, BaseModel], ctx: CheckContext) -> list[Finding]:
    """Billed qty must not exceed dose x frequency x duration (rounded up to a full strip)."""
    rx, bill = _docs(docs)
    findings: list[Finding] = []
    for i, (line, verdict) in enumerate(zip(bill.line_items, match_bill_to_prescription(docs, ctx), strict=True)):
        if verdict is None or verdict.right is None or line.qty.value is None:
            continue
        med = rx.medicines[verdict.right]
        need = expected_units(med.dosage.value, med.frequency.value, med.duration.value)
        if need is None:
            continue  # as-needed, liquid, or unreadable posology: nothing to compare
        limit = allowed_units(need)
        if line.qty.value > limit:
            m = f"prescription.medicines[{verdict.right}]"
            findings.append(hard(
                "implausible_quantity",
                f"bill.line_items[{i}] bills {line.qty.value:g} units of '{line.medicine.value}' but the course "
                f"({med.dosage.value or '1'} x {med.frequency.value} x {med.duration.value}) needs {need:g} "
                f"(allowing up to {limit:g})",
                f"bill.line_items[{i}].qty", f"{m}.dosage", f"{m}.frequency", f"{m}.duration",
                expected=need, allowed=limit,
            ))
    return findings


CHECKS = (
    CrossCheck("unprescribed_medicine", "Every billed medicine was prescribed", billed_medicines_prescribed),
    CrossCheck("patient_name_mismatch", "Patient name matches on both documents", patient_names_match),
    CrossCheck("bill_before_prescription",
               f"Bill dated on/after the prescription, within {MAX_DAYS_AFTER_PRESCRIPTION} days", bill_date_valid),
    CrossCheck("implausible_quantity", "Billed quantity fits dose x frequency x duration", quantities_plausible),
)
