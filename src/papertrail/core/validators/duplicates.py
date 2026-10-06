"""Duplicate detection: perceptual image hash plus a normalised field fingerprint."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from papertrail.core.validators.fuzzy import normalize_text


def fingerprint(*parts: object) -> str:
    """Stable fingerprint of key fields (e.g. merchant, date, amount), normalised for OCR noise."""
    norm = []
    for part in parts:
        if isinstance(part, float):
            norm.append(f"{part:.2f}")
        else:
            norm.append(normalize_text(str(part)) if part is not None else "")
    return hashlib.sha1("|".join(norm).encode()).hexdigest()[:16]


def hamming(a: int, b: int) -> int:
    """Number of differing bits between two 64-bit hashes."""
    return bin(a ^ b).count("1")


@dataclass(frozen=True)
class DuplicateHit:
    """A previously seen document that looks like the current one."""

    case_id: str
    kind: str  # "image" | "fields"
    distance: int = 0


@dataclass
class DuplicateIndex:
    """In-memory index of documents already processed (one per session / benchmark run)."""

    max_distance: int = 6
    _entries: list[tuple[str, int | None, str | None]] = field(default_factory=list)

    def check(self, case_id: str, image_hash: int | None, fp: str | None) -> list[DuplicateHit]:
        """Return earlier cases (other than ``case_id``) with a near-identical image or same fingerprint."""
        hits: list[DuplicateHit] = []
        for other_id, other_hash, other_fp in self._entries:
            if other_id == case_id:
                continue
            if image_hash is not None and other_hash is not None:
                dist = hamming(image_hash, other_hash)
                if dist <= self.max_distance:
                    hits.append(DuplicateHit(other_id, "image", dist))
                    continue
            if fp is not None and fp == other_fp:
                hits.append(DuplicateHit(other_id, "fields"))
        return hits

    def add(self, case_id: str, image_hash: int | None, fp: str | None) -> None:
        """Remember a processed document."""
        self._entries.append((case_id, image_hash, fp))
