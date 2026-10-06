"""Vocabulary for synthetic documents: real drug and goods names, HSN codes, fake identities.

All people, companies, addresses and GSTINs are fake (Faker en_IN + generated checksums).
Drug brand names are real trademarks of their owners, used only as vocabulary.
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass, field

from papertrail.core.validators.gstin import STATE_CODES, make_gstin


@dataclass(frozen=True)
class Drug:
    """A generic with strength/form, typical per-unit price, and brand names."""

    generic: str
    strength: str
    form: str  # "Tab" | "Cap"
    unit_rate: float
    brands: tuple[str, ...] = ()  # in the pack's brand map: rules can normalise these
    longtail: tuple[str, ...] = ()  # real brands NOT in the pack's map: rules-only matching misses these


DRUGS: tuple[Drug, ...] = (
    Drug("Paracetamol", "650 mg", "Tab", 2.0, ("Dolo 650", "Calpol 650", "Crocin 650"), ("Pacimol 650",)),
    Drug("Amoxicillin", "500 mg", "Cap", 7.5, ("Mox 500", "Novamox 500")),
    Drug("Amoxicillin + Clavulanic Acid", "625 mg", "Tab", 22.0, ("Augmentin 625 Duo", "Clavam 625"),
         ("Moxikind-CV 625",)),
    Drug("Azithromycin", "500 mg", "Tab", 24.0, ("Azithral 500", "Azee 500"), ("Azax 500",)),
    Drug("Pantoprazole", "40 mg", "Tab", 8.0, ("Pan 40", "Pantocid 40"), ("Pantop 40",)),
    Drug("Cetirizine", "10 mg", "Tab", 2.0, ("Cetzine", "Okacet"), ("Cetcip",)),
    Drug("Montelukast", "10 mg", "Tab", 16.0, ("Montair 10",)),
    Drug("Metformin", "500 mg", "Tab", 3.0, ("Glycomet 500",), ("Glyciphage 500",)),
    Drug("Amlodipine", "5 mg", "Tab", 4.0, ("Amlong 5",), ("Amlokind 5",)),
    Drug("Atorvastatin", "10 mg", "Tab", 9.0, ("Atorva 10",), ("Atocor 10",)),
    Drug("Rosuvastatin", "10 mg", "Tab", 14.0, ("Rosuvas 10",), ("Rozavel 10",)),
    Drug("Telmisartan", "40 mg", "Tab", 7.0, ("Telma 40",), ("Telvas 40",)),
    Drug("Cefixime", "200 mg", "Tab", 12.0, ("Taxim-O 200",), ("Zifi 200",)),
    Drug("Ondansetron", "4 mg", "Tab", 6.0, ("Ondem 4", "Emeset 4")),
    Drug("Ibuprofen", "400 mg", "Tab", 2.0, ("Brufen 400",)),
    Drug("Aceclofenac", "100 mg", "Tab", 5.0, ("Zerodol 100",)),
    Drug("Metronidazole", "400 mg", "Tab", 2.0, ("Flagyl 400", "Metrogyl 400")),
    Drug("Levocetirizine", "5 mg", "Tab", 3.0, ("Levocet 5",)),
    Drug("Fexofenadine", "120 mg", "Tab", 12.0, ("Allegra 120",)),
    Drug("Omeprazole", "20 mg", "Cap", 5.0, ("Omez 20",)),
    Drug("Levothyroxine", "50 mcg", "Tab", 1.5, ("Thyronorm 50", "Eltroxin 50")),
)  # fmt: skip

# (frequency as written, doses/day), (duration as written, days)
FREQUENCIES = (("1-0-1", 2), ("1-1-1", 3), ("BD", 2), ("TDS", 3), ("OD", 1), ("0-0-1", 1), ("1-0-0", 1),
               ("twice daily", 2), ("HS", 1))  # fmt: skip
DURATIONS = (("3 days", 3), ("5 days", 5), ("x 5 days", 5), ("7 days", 7), ("1 week", 7), ("10 days", 10),
             ("2 weeks", 14))  # fmt: skip
DIAGNOSES = ("Fever", "URTI", "Acute gastritis", "Allergic rhinitis", "Type 2 DM", "Hypertension", "Pharyngitis",
             "Viral fever", "UTI", "Migraine", "Dyslipidemia", "Bronchitis")  # fmt: skip


@dataclass(frozen=True)
class Item:
    """A purchasable item: PO wording, invoice paraphrases, HSN/SAC, price range and GST rate."""

    po_desc: str
    inv_variants: tuple[str, ...]
    hsn: str
    unit: str
    rate_range: tuple[float, float]
    gst: float


ITEMS: tuple[Item, ...] = (
    Item("Copier paper A4 75 GSM (500 sheets)", ("A4 paper ream", "JK Copier A4 75gsm ream", "A4 Copier Paper 500 Sht"),
         "4802", "Ream", (240, 320), 18.0),
    Item("HP 88A black toner cartridge", ("Toner cartridge HP 88A", "HP CC388A Toner Black", "Laser toner 88A"),
         "8443", "Nos", (2200, 2800), 18.0),
    Item("Ball pens blue (box of 10)", ("Reynolds 045 blue ballpoint, pack of 10", "Blue ball pen box/10"),
         "9608", "Box", (50, 80), 18.0),
    Item("Whiteboard markers assorted (set of 4)", ("Camlin WB marker 4 colour set", "Dry erase markers x4"),
         "9608", "Set", (120, 180), 18.0),
    Item("Stapler heavy duty", ("Kangaro HD-45 stapler", "Heavy-duty stapling machine"), "8472", "Nos",
         (350, 450), 18.0),
    Item("Ergonomic office chair", ("Mesh office chair with lumbar support", "Revolving chair ergonomic"), "9401",
         "Nos", (6500, 9000), 18.0),
    Item("LED tube light 20W", ("20 W LED batten", "Philips LED tubelight 20W"), "9405", "Nos", (250, 350), 18.0),
    Item("Cat6 ethernet cable (305 m box)", ("CAT-6 LAN cable roll 305m", "D-Link Cat6 UTP cable box"), "8544",
         "Box", (6000, 8000), 18.0),
    Item("Wireless mouse", ("Logitech M235 cordless mouse", "2.4GHz wireless optical mouse"), "8471", "Nos",
         (500, 800), 18.0),
    Item("USB keyboard", ("Wired USB keyboard (full size)", "Dell KB216 keyboard"), "8471", "Nos", (600, 900), 18.0),
    Item("Hand sanitizer 5 litre can", ("Sanitiser 5L jerry can", "Alcohol hand rub 5 ltr"), "3808", "Can",
         (900, 1300), 18.0),
    Item("Laptop stand aluminium", ("Adjustable aluminium laptop riser", "Laptop stand (alu)"), "8473", "Nos",
         (1200, 1800), 18.0),
    Item("Box files (pack of 10)", ("Lever arch box file x10", "Office box file set of 10"), "4820", "Pack",
         (150, 250), 18.0),
    Item("Packaged tea 1 kg", ("Tata Tea Premium 1kg", "CTC tea pack 1 kg"), "0902", "Kg", (450, 600), 5.0),
    Item("Sugar 5 kg", ("Refined sugar 5kg bag", "Sugar (5 KG)"), "1701", "Bag", (220, 280), 5.0),
    Item("Housekeeping services (monthly)", ("Facility housekeeping - monthly charges", "Cleaning services per month"),
         "998533", "Month", (18000, 25000), 18.0),
)  # fmt: skip


def pan(rng: random.Random, entity: str = "C") -> str:
    """A fake PAN with the right shape (4th letter = holder type)."""
    letters = string.ascii_uppercase
    return "".join(rng.choice(letters) for _ in range(3)) + entity + rng.choice(letters) + \
        f"{rng.randint(0, 9999):04d}" + rng.choice(letters)


def gstin(rng: random.Random, state: str | None = None) -> str:
    """A fake, checksum-valid GSTIN."""
    states = [s for s in STATE_CODES if s not in ("97", "99", "25", "28")]
    return make_gstin(state or rng.choice(states), pan(rng), entity=rng.choice("1112"))


def corrupt_checksum(value: str, rng: random.Random) -> str:
    """Same GSTIN with a wrong check character (format still valid)."""
    chars = [c for c in string.ascii_uppercase + string.digits if c != value[-1]]
    return value[:-1] + rng.choice(chars)


@dataclass
class Identity:
    """A fake person with name variants that refer to the same individual."""

    first: str
    last: str
    title: str
    variants: list[str] = field(default_factory=list)

    @property
    def full(self) -> str:
        """'Rahul Sharma'."""
        return f"{self.first} {self.last}"


def identity(fake, rng: random.Random) -> Identity:
    """Fake person + same-person spelling variants (title, initial, surname-first, caps)."""
    male = rng.random() < 0.5
    first = fake.first_name_male() if male else fake.first_name_female()
    last = fake.last_name()
    title = "Mr." if male else rng.choice(["Mrs.", "Ms."])
    variants = [f"{first} {last}", f"{title} {first} {last}", f"{first[0]}. {last}", f"{last.upper()} {first.upper()}"]
    return Identity(first, last, title, variants)
