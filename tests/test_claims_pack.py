"""Claims pack: posology parsing, drug normalisation, and a failing case per check."""

from datetime import date

import pytest

from papertrail.core.crosscheck import CheckContext
from papertrail.core.decision import Outcome
from papertrail.core.engine import evaluate
from papertrail.core.fields import known, missing
from papertrail.core.matching import Matcher
from papertrail.core.pack import get_pack
from papertrail.packs.claims.drugs import brand_ingredients, canonical_drug
from papertrail.packs.claims.posology import allowed_units, doses_per_day, duration_days, expected_units, units_per_dose
from papertrail.packs.claims.schemas import BillLine, MedicalBill, PrescribedMedicine, Prescription
from tests.conftest import fake_llm

TODAY = date(2026, 10, 6)


@pytest.mark.parametrize(("text", "n"), [("1-0-1", 2), ("1-1-1", 3), ("0-0-1", 1), ("1/2-0-1/2", 1), ("BD", 2),
                                         ("TDS", 3), ("tid", 3), ("QID", 4), ("OD", 1), ("once daily", 1),
                                         ("HS", 1), ("twice a day", 2), ("8 hourly", 3), ("1-0-0-1", 2)])
def test_doses_per_day(text, n):
    assert doses_per_day(text) == n


def test_posology_unknowns_and_durations():
    assert doses_per_day("SOS") is None and doses_per_day("as directed") is None and doses_per_day(None) is None
    assert duration_days("x 5 days") == 5 and duration_days("1 week") == 7 and duration_days("2 wks") == 14
    assert duration_days("1 month") == 30 and duration_days("10D") == 10 and duration_days("till review") is None
    assert units_per_dose("1/2 tab") == 0.5 and units_per_dose("2 caps") == 2 and units_per_dose(None) == 1
    assert units_per_dose("5 ml") is None
    assert expected_units("1 tab", "1-0-1", "5 days") == 10
    assert allowed_units(10) == 15 and allowed_units(6) == 15 and allowed_units(21) == 30


def test_drug_canonicalisation_offline():
    assert brand_ingredients("Dolo 650 Tab") == ("paracetamol",)
    assert canonical_drug("Dolo 650") == canonical_drug("Paracetamol 650 mg") == "acetaminophen"
    assert canonical_drug("Augmentin 625 Duo") == canonical_drug("Clavam 625") == "amoxicillin+clavulanate"
    assert canonical_drug("Tab. Rosuvastatin 10mg") == "rosuvastatin"


def med(name, freq="1-0-1", dur="5 days", dosage="1 tab"):
    return PrescribedMedicine(name=known(name), strength=missing(), dosage=known(dosage), frequency=known(freq),
                              duration=known(dur))


def line(name, qty, rate):
    return BillLine(medicine=known(name), batch=missing(), qty=known(float(qty)), rate=known(float(rate)),
                    amount=known(round(qty * rate, 2)))


def rx_doc(patient="Rahul Sharma", day=date(2026, 9, 10), meds=None):
    return Prescription(doctor_name=known("Dr. A. Rao"), registration_number=known("KMC 12345"),
                        clinic_name=known("City Clinic"), patient_name=known(patient), patient_age=known("34 Y"),
                        date=known(day), diagnosis=known("fever"),
                        medicines=meds if meds is not None else [med("Paracetamol 650 mg", "TDS", "3 days"),
                                                                 med("Amoxicillin 500 mg", "1-0-1", "5 days")])


def bill_doc(patient="Mr. Rahul Sharma", day=date(2026, 9, 10), lines=None, total=None, discount=None):
    items = lines if lines is not None else [line("Dolo 650 Tab", 9, 2.0), line("Mox 500 Cap", 10, 7.5)]
    t = total if total is not None else round(sum(i.amount.value for i in items) - (discount or 0), 2)
    return MedicalBill(pharmacy_name=known("Apna Pharmacy"), bill_number=known("B-77"), patient_name=known(patient),
                       doctor_name=missing(), date=known(day), line_items=items,
                       discount=known(discount) if discount else missing(), total=known(t))


def run(rx, bill, matcher=None):
    ctx = CheckContext(matcher=matcher or Matcher(), today=TODAY)
    validations, outcomes, decision = evaluate(get_pack("claims"), {"prescription": rx, "bill": bill}, ctx)
    codes = {f.code for f in validations} | {f.code for o in outcomes for f in o.findings}
    return decision, codes - {"drug_validated"}


def test_clean_pair_with_brand_names_approves():
    decision, codes = run(rx_doc(), bill_doc())
    assert decision.outcome == Outcome.APPROVE, decision.reasons
    assert codes == set()


def test_unprescribed_medicine_flags_with_field():
    bill = bill_doc(lines=[line("Dolo 650 Tab", 9, 2.0), line("Montair 10", 10, 18.0)])
    decision, codes = run(rx_doc(), bill)
    assert decision.outcome == Outcome.FLAG and "unprescribed_medicine" in codes
    assert decision.reasons[0].fields == ["bill.line_items[1].medicine"]


def test_unknown_brand_rules_miss_llm_matches():
    rx = rx_doc(meds=[med("Cefuroxime 500 mg", "BD", "5 days")])
    bill = bill_doc(lines=[line("Zifi-CV 500", 10, 30.0)])  # not in the brand map
    _, codes = run(rx, bill)
    assert "unprescribed_medicine" in codes  # rules alone: false alarm
    llm = fake_llm({"matches": [{"left": 0, "right": 0, "reason": "same cephalosporin brand"}]})
    decision, codes = run(rx, bill, Matcher(mode="rules+llm", llm=llm, model="match-model"))
    assert "unprescribed_medicine" not in codes


def test_patient_name_hard_and_soft():
    decision, codes = run(rx_doc(), bill_doc(patient="Priya Nair"))
    assert decision.outcome == Outcome.FLAG and "patient_name_mismatch" in codes
    decision, codes = run(rx_doc(), bill_doc(patient="R. Sharma"))
    assert "patient_name_mismatch" not in codes  # initials tolerated
    decision, codes = run(rx_doc(), bill_doc(patient="Rahul Verma"))
    assert decision.outcome in (Outcome.NEEDS_REVIEW, Outcome.FLAG) and "patient_name_mismatch" in codes


def test_bill_before_prescription_and_too_late():
    decision, codes = run(rx_doc(), bill_doc(day=date(2026, 9, 8)))
    assert decision.outcome == Outcome.FLAG and "bill_before_prescription" in codes
    decision, codes = run(rx_doc(day=date(2026, 8, 1)), bill_doc(day=date(2026, 9, 10)))
    assert decision.outcome == Outcome.NEEDS_REVIEW and codes == {"bill_too_late"}


def test_implausible_quantity():
    bill = bill_doc(lines=[line("Dolo 650 Tab", 9, 2.0), line("Mox 500 Cap", 60, 7.5)])
    decision, codes = run(rx_doc(), bill)
    assert decision.outcome == Outcome.FLAG and "implausible_quantity" in codes
    assert "bill.line_items[1].qty" in decision.reasons[0].fields


def test_strip_rounding_is_plausible():
    bill = bill_doc(lines=[line("Dolo 650 Tab", 15, 2.0), line("Mox 500 Cap", 15, 7.5)])  # full strips
    assert run(rx_doc(), bill)[0].outcome == Outcome.APPROVE


def test_sos_medicine_skips_quantity_check():
    rx = rx_doc(meds=[med("Paracetamol 650", "SOS", "5 days")])
    assert "implausible_quantity" not in run(rx, bill_doc(lines=[line("Dolo 650", 30, 2.0)]))[1]


def test_bill_arithmetic_error_line_and_total():
    bad_line = line("Dolo 650 Tab", 9, 2.0)
    bad_line.amount = known(36.0)
    _, codes = run(rx_doc(), bill_doc(lines=[bad_line], total=36.0))
    assert "arithmetic_error" in codes
    decision, codes = run(rx_doc(), bill_doc(total=200.0))
    assert decision.outcome == Outcome.FLAG and "arithmetic_error" in codes
    assert run(rx_doc(), bill_doc(discount=5.0))[0].outcome == Outcome.APPROVE


def test_unreadable_medicine_name_needs_review():
    bill = bill_doc()
    bill.line_items[0].medicine = missing("illegible")
    decision, codes = run(rx_doc(), bill)
    assert decision.outcome == Outcome.NEEDS_REVIEW and "unprescribed_medicine" in codes


def test_combination_generic_matches_brand():
    assert canonical_drug("Amoxicillin + Clavulanic Acid 625 mg") == canonical_drug("Augmentin 625 Duo")


def test_unknown_drug_info_when_confident_review_when_not():
    from papertrail.packs.claims.validators import drug_name_findings

    class NoHit:
        def lookup(self, name):
            from papertrail.core.validators.rxnorm import DrugLookup
            return DrugLookup(query=name, normalized_query=name.lower(), found=False, suggestion="cefuroxime")

    assert drug_name_findings("r", "Zifi-CV 500", NoHit(), 1.0)[0].code == "drug_not_in_reference"
    low = drug_name_findings("r", "Zifl-CV 500", NoHit(), 0.4)[0]
    assert low.code == "unknown_drug" and low.severity == "soft" and "cefuroxime" in low.message


def test_unknown_brand_has_no_canonical_key():
    assert canonical_drug("Zifi 200") is None and canonical_drug("Glyciphage 500") is None
    assert canonical_drug("Glycomet 500") == "metformin"
