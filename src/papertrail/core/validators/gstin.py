"""GSTIN (Indian GST identification number) format and checksum validation.

Layout (15 chars): 2-digit state code, 10-char PAN, entity number (1-9, A-Z),
the letter 'Z', and a check character computed with a base-36 Luhn-style algorithm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_FORMAT = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

STATE_CODES: dict[str, str] = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan",
    "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura", "17": "Meghalaya",
    "18": "Assam", "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat", "25": "Daman and Diu (pre-2020)",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra", "28": "Andhra Pradesh (pre-2014)",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu",
    "34": "Puducherry", "35": "Andaman and Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh", "97": "Other Territory", "99": "Centre Jurisdiction",
}  # fmt: skip


@dataclass(frozen=True)
class GstinCheck:
    """Result of validating one GSTIN."""

    gstin: str
    valid: bool
    reason: str | None = None

    @property
    def state_code(self) -> str | None:
        """The 2-digit state code when the format is valid, else None."""
        return self.gstin[:2] if _FORMAT.match(self.gstin) else None


def checksum_char(first14: str) -> str:
    """Compute the GSTIN check character for the first 14 characters."""
    total = 0
    for i, ch in enumerate(first14.upper()):
        product = _CHARSET.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _CHARSET[(36 - total % 36) % 36]


def normalize(gstin: str) -> str:
    """Uppercase and drop spaces, dots and dashes that OCR or typing adds."""
    return re.sub(r"[\s.\-]", "", gstin).upper()


def validate_gstin(raw: str) -> GstinCheck:
    """Validate format, state code and checksum of a GSTIN."""
    gstin = normalize(raw)
    if len(gstin) != 15:
        return GstinCheck(gstin, False, f"length {len(gstin)}, expected 15")
    if not _FORMAT.match(gstin):
        return GstinCheck(gstin, False, "does not match the GSTIN format")
    if gstin[:2] not in STATE_CODES:
        return GstinCheck(gstin, False, f"unknown state code {gstin[:2]}")
    expected = checksum_char(gstin[:14])
    if gstin[14] != expected:
        return GstinCheck(gstin, False, f"checksum mismatch: last char {gstin[14]}, expected {expected}")
    return GstinCheck(gstin, True)


def make_gstin(state_code: str, pan: str, entity: str = "1") -> str:
    """Build a checksum-valid GSTIN (used by the synthetic generator and tests)."""
    body = f"{state_code}{pan.upper()}{entity}Z"
    return body + checksum_char(body)


def same_state(gstin_a: str, gstin_b: str) -> bool | None:
    """True if both GSTINs share a state code (intra-state supply); None if either is malformed."""
    a, b = validate_gstin(gstin_a).state_code, validate_gstin(gstin_b).state_code
    if a is None or b is None:
        return None
    return a == b
