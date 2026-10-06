"""Claims pack: health-insurance claim intake (prescription + pharmacy bill)."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from papertrail.core.crosscheck import CheckContext, CrossCheck
from papertrail.core.decision import DecisionPolicy
from papertrail.core.findings import Finding
from papertrail.core.pack import Pack, register
from papertrail.packs.claims.crosschecks import CHECKS
from papertrail.packs.claims.schemas import MEDICAL_BILL, PRESCRIPTION, MedicalBill, Prescription
from papertrail.packs.claims.validators import validate_bill, validate_prescription


@register
class ClaimsPack(Pack):
    """Prescription + bill: drug validation, arithmetic, and bill-vs-prescription consistency."""

    name = "claims"
    title = "Health insurance claims"
    description = "Pharmacy bills checked against the prescription: drugs, patient, dates and quantities."
    doc_types = (PRESCRIPTION, MEDICAL_BILL)
    policy = DecisionPolicy(
        min_confidence=0.75,
        critical_fields={
            "prescription": ("patient_name", "date", "medicines[*].name"),
            "bill": ("patient_name", "date", "total", "line_items[*].medicine", "line_items[*].qty",
                     "line_items[*].amount"),
        },
    )

    def validate(self, doc_type: str, doc: BaseModel, ctx: CheckContext) -> list[Finding]:
        """Dispatch to the prescription or bill validator."""
        if isinstance(doc, Prescription):
            return validate_prescription(doc, ctx)
        if isinstance(doc, MedicalBill):
            return validate_bill(doc, ctx)
        return []

    def crosschecks(self) -> Sequence[CrossCheck]:
        """Bill-vs-prescription checks."""
        return CHECKS
