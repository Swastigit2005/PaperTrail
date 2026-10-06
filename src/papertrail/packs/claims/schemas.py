"""Claims pack schemas: a doctor's prescription and a pharmacy/hospital bill."""

from __future__ import annotations

from pydantic import BaseModel

from papertrail.core.fields import Field, LenientDate, Number, doc_field, doc_list
from papertrail.core.pack import DocType


class PrescribedMedicine(BaseModel):
    """One medicine line on a prescription."""

    name: Field[str] = doc_field("medicine name exactly as written (brand or generic), without strength")
    strength: Field[str] = doc_field("strength, e.g. '650 mg', '5 mg/5 ml'")
    dosage: Field[str] = doc_field("amount per dose, e.g. '1 tab', '1/2 tab', '5 ml'")
    frequency: Field[str] = doc_field("how often as written, e.g. 'BD', 'TDS', '1-0-1', 'SOS'")
    duration: Field[str] = doc_field("course length as written, e.g. '5 days', '1 week'")


class Prescription(BaseModel):
    """A doctor's prescription."""

    doctor_name: Field[str] = doc_field("prescribing doctor's name")
    registration_number: Field[str] = doc_field("doctor's medical council registration number")
    clinic_name: Field[str] = doc_field("clinic or hospital name in the letterhead")
    patient_name: Field[str] = doc_field("patient's full name")
    patient_age: Field[str] = doc_field("patient's age as written, e.g. '34 Y'")
    date: Field[LenientDate] = doc_field("prescription date")
    diagnosis: Field[str] = doc_field("diagnosis / complaint if written")
    medicines: list[PrescribedMedicine] = doc_list("every medicine under Rx, in order")


class BillLine(BaseModel):
    """One line item on a pharmacy bill."""

    medicine: Field[str] = doc_field("product name as printed, including strength/form if printed")
    batch: Field[str] = doc_field("batch number if printed")
    qty: Field[Number] = doc_field("quantity billed (units: tablets/capsules, or bottles/tubes)")
    rate: Field[Number] = doc_field("price per unit")
    amount: Field[Number] = doc_field("line amount")


class MedicalBill(BaseModel):
    """A pharmacy or hospital bill for medicines."""

    pharmacy_name: Field[str] = doc_field("pharmacy / hospital name")
    bill_number: Field[str] = doc_field("bill / invoice number")
    patient_name: Field[str] = doc_field("patient name printed on the bill")
    doctor_name: Field[str] = doc_field("referring doctor if printed")
    date: Field[LenientDate] = doc_field("bill date")
    line_items: list[BillLine] = doc_list("every billed item, in order")
    discount: Field[Number] = doc_field("total discount amount, null if none shown")
    total: Field[Number] = doc_field("net amount payable")


PRESCRIPTION = DocType(
    name="prescription",
    title="medical prescription",
    schema=Prescription,
    description="a doctor's prescription (printed or handwritten) listing medicines under Rx",
    keywords=("rx", "prescription", "dr.", "reg. no", "diagnosis", "mbbs", "md", "advice", "follow up"),
    hints=(
        "Indian prescriptions: 1-0-1 means morning-afternoon-night doses; OD=once daily, BD=twice, "
        "TDS=thrice, QID=four times, HS=at bedtime, SOS=as needed. 'x 5 days' is the duration. "
        "Keep frequency and duration exactly as written. Handwriting may be messy: if a medicine "
        "name is not legible, set it null with null_reason 'illegible' rather than guessing."
    ),
)

MEDICAL_BILL = DocType(
    name="bill",
    title="pharmacy bill",
    schema=MedicalBill,
    description="a pharmacy or hospital bill itemising medicines with quantity, rate and amount",
    keywords=("bill", "invoice", "batch", "qty", "mrp", "amount", "pharmacy", "chemist", "net amount", "gst"),
    hints="Amounts are in rupees. 'Net Amt'/'Net Payable' is the total. Expiry dates are not bill dates.",
)
