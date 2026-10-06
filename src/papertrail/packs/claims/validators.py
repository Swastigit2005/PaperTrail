"""Per-document validators for the claims pack: drug names, bill arithmetic, date sanity."""

from __future__ import annotations

from papertrail.core.crosscheck import CheckContext
from papertrail.core.fields import Field
from papertrail.core.findings import Finding, Severity, info, soft
from papertrail.core.validators.arithmetic import line_amount_check, sum_check
from papertrail.core.validators.dates import sanity_problem
from papertrail.core.validators.rxnorm import RxNormClient
from papertrail.packs.claims.drugs import brand_ingredients
from papertrail.packs.claims.schemas import MedicalBill, Prescription


def _date_sanity(ref: str, fld: Field, ctx: CheckContext) -> list[Finding]:
    if fld.value is None:
        return []
    problem = sanity_problem(fld.value, today=ctx.today)
    return [soft("implausible_date", f"{ref}: {problem}", ref)] if problem else []


def drug_name_findings(ref: str, name: str | None, rxnorm: RxNormClient | None) -> list[Finding]:
    """Validate one drug name: known Indian brand, or RxNorm match; suggest the closest name if not."""
    if not name:
        return []
    ingredients = brand_ingredients(name)
    if ingredients:
        return [info("drug_validated", f"{ref} '{name}' is a known brand of {' + '.join(ingredients)}", ref,
                     source="brand_map", ingredients=list(ingredients))]
    if rxnorm is None:
        return []
    lookup = rxnorm.lookup(name)
    if lookup.error:
        return [info("drug_unverified", f"{ref}: RxNorm unavailable, '{name}' not checked", ref)]
    if lookup.found:
        return [info("drug_validated", f"{ref} '{name}' found in RxNorm (RXCUI {lookup.rxcui})", ref,
                     source="rxnorm", rxcui=lookup.rxcui, ingredients=lookup.ingredients)]
    hint = f"; closest RxNorm match: '{lookup.suggestion}'" if lookup.suggestion else ""
    return [soft("unknown_drug", f"{ref} '{name}' not found in RxNorm or the brand list{hint} (possible misread)",
                 ref, suggestion=lookup.suggestion)]


def validate_prescription(doc: Prescription, ctx: CheckContext) -> list[Finding]:
    """Date sanity and drug-name validation for every prescribed medicine."""
    findings = _date_sanity("prescription.date", doc.date, ctx)
    rxnorm = ctx.services.get("rxnorm")
    for i, med in enumerate(doc.medicines):
        findings += drug_name_findings(f"prescription.medicines[{i}].name", med.name.value, rxnorm)
    return findings


def validate_bill(doc: MedicalBill, ctx: CheckContext) -> list[Finding]:
    """Line arithmetic, total = sum(lines) - discount, date sanity, drug names."""
    findings = _date_sanity("bill.date", doc.date, ctx)
    rxnorm = ctx.services.get("rxnorm")
    for i, line in enumerate(doc.line_items):
        p = f"bill.line_items[{i}]"
        f = line_amount_check(line.qty, line.rate, line.amount, paths=(f"{p}.qty", f"{p}.rate", f"{p}.amount"),
                              code="arithmetic_error")
        if f:
            findings.append(f)
        findings += drug_name_findings(f"{p}.medicine", line.medicine.value, rxnorm)
    if doc.line_items:
        parts = [(f"bill.line_items[{i}].amount", line.amount) for i, line in enumerate(doc.line_items)]
        if doc.discount.value:
            negated = Field(value=-doc.discount.value, confidence=doc.discount.confidence)
            parts.append(("bill.discount", negated))
        f = sum_check(parts, ("bill.total", doc.total), code="arithmetic_error", label="bill.total",
                      severity=Severity.HARD)
        if f:
            findings.append(f)
    return findings
