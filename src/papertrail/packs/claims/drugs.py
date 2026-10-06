"""Drug-name normalisation for Indian prescriptions and bills.

RxNorm (US) knows generics and US brands but not most Indian brands, so a curated map of common
Indian brands to their generic ingredients is checked first. Brand names are trademarks of their
owners and are used here only as real-world vocabulary for synthetic test documents.
"""

from __future__ import annotations

import re

from papertrail.core.validators.rxnorm import RxNormClient, strip_strength

# brand (lowercase, first word(s)) -> generic ingredient(s)
INDIAN_BRANDS: dict[str, tuple[str, ...]] = {
    "dolo": ("paracetamol",),
    "crocin": ("paracetamol",),
    "calpol": ("paracetamol",),
    "combiflam": ("ibuprofen", "paracetamol"),
    "brufen": ("ibuprofen",),
    "meftal": ("mefenamic acid",),
    "zerodol": ("aceclofenac",),
    "augmentin": ("amoxicillin", "clavulanate"),
    "clavam": ("amoxicillin", "clavulanate"),
    "mox": ("amoxicillin",),
    "novamox": ("amoxicillin",),
    "azithral": ("azithromycin",),
    "azee": ("azithromycin",),
    "taxim-o": ("cefixime",),
    "ciplox": ("ciprofloxacin",),
    "norflox": ("norfloxacin",),
    "flagyl": ("metronidazole",),
    "metrogyl": ("metronidazole",),
    "pan": ("pantoprazole",),
    "pantocid": ("pantoprazole",),
    "omez": ("omeprazole",),
    "razo": ("rabeprazole",),
    "ondem": ("ondansetron",),
    "emeset": ("ondansetron",),
    "allegra": ("fexofenadine",),
    "cetzine": ("cetirizine",),
    "okacet": ("cetirizine",),
    "levocet": ("levocetirizine",),
    "montair": ("montelukast",),
    "asthalin": ("salbutamol",),
    "glycomet": ("metformin",),
    "telma": ("telmisartan",),
    "amlong": ("amlodipine",),
    "ecosprin": ("aspirin",),
    "atorva": ("atorvastatin",),
    "rosuvas": ("rosuvastatin",),
    "thyronorm": ("levothyroxine",),
    "eltroxin": ("levothyroxine",),
    "limcee": ("ascorbic acid",),
}

# Indian / INN generic names -> the RxNorm spelling, so both sides compare equal.
SYNONYMS: dict[str, str] = {
    "paracetamol": "acetaminophen",
    "salbutamol": "albuterol",
    "clavulanic acid": "clavulanate",
    "amoxycillin": "amoxicillin",
    "vitamin c": "ascorbic acid",
}


def _canon_ingredient(name: str) -> str:
    name = name.strip().lower()
    return SYNONYMS.get(name, name)


def brand_ingredients(name: str) -> tuple[str, ...] | None:
    """Ingredients for a known Indian brand (matched on the leading word(s)), else None."""
    words = re.sub(r"[^a-z0-9\- ]", " ", name.lower()).split()
    for n in (2, 1):
        key = " ".join(words[:n])
        if key in INDIAN_BRANDS:
            return INDIAN_BRANDS[key]
    return None


def canonical_drug(name: str, rxnorm: RxNormClient | None = None) -> str:
    """Canonical ingredient key: brand map, then RxNorm ingredients, then the stripped name.

    ``'Dolo 650 Tab'`` and ``'Paracetamol 650 mg'`` both become ``'acetaminophen'``.
    """
    ingredients = brand_ingredients(name)
    if ingredients is None and rxnorm is not None:
        lookup = rxnorm.lookup(name)
        if lookup.found and lookup.ingredients:
            ingredients = tuple(lookup.ingredients)
    if ingredients is None:
        stripped = strip_strength(name)
        ingredients = (stripped,) if stripped else ()
    return "+".join(sorted({_canon_ingredient(i) for i in ingredients}))
