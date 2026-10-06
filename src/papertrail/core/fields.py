"""Field-level extraction output.

Every extracted leaf value is wrapped in ``Field[T]`` carrying the value, a 0-1 confidence,
the short evidence text it was read from, and - when the value is null - the reason why.
Unreadable or absent values are null with a reason; the extractor must never guess.

``Field`` is deliberately lenient on input (LLMs return bare values, "Rs. 1,200", "high"
confidence, ...) and strict on output: whatever arrives, the result is a well-formed Field
and an unparseable value becomes null with ``null_reason`` explaining what was rejected.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError, model_validator
from pydantic import Field as PField

from papertrail.core.validators.dates import parse_date

T = TypeVar("T")

_WORD_CONFIDENCE = {"high": 0.9, "medium": 0.6, "med": 0.6, "low": 0.3}
_CLIP = 160
_FIELD_KEYS = frozenset({"value", "confidence", "evidence", "null_reason"})


def _clip(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:_CLIP] or None


def _coerce_confidence(raw: Any) -> float:
    if isinstance(raw, bool):
        return 1.0 if raw else 0.0
    if isinstance(raw, int | float):
        conf = float(raw)
    elif isinstance(raw, str):
        word = raw.strip().lower()
        if word in _WORD_CONFIDENCE:
            return _WORD_CONFIDENCE[word]
        try:
            conf = float(word.rstrip("%")) / (100 if word.endswith("%") else 1)
        except ValueError:
            return 0.5
    else:
        return 0.5
    if conf > 1.0 and conf <= 100.0:  # model answered in percent
        conf /= 100.0
    return min(1.0, max(0.0, conf))


def _as_field_dict(data: Any) -> dict[str, Any]:
    """Normalise anything an LLM might emit for a field into the Field dict shape."""
    if isinstance(data, dict) and set(data) <= _FIELD_KEYS | {"note", "notes", "source"}:
        out = dict(data)
        out["confidence"] = _coerce_confidence(out.get("confidence", 0.5))
        out["evidence"] = _clip(out.get("evidence"))
        out["null_reason"] = _clip(out.get("null_reason"))
        return out
    if data is None:
        return {"value": None, "confidence": 0.0, "null_reason": "not returned by extractor"}
    # A bare value with no metadata: accept it, but with neutral confidence.
    return {"value": data, "confidence": 0.5, "evidence": None}


class Field(BaseModel, Generic[T]):
    """One extracted value with its confidence, evidence and null reason."""

    model_config = ConfigDict(extra="ignore")

    value: T | None = None
    confidence: float = 0.0
    evidence: str | None = None
    null_reason: str | None = None

    @model_validator(mode="wrap")
    @classmethod
    def _lenient(cls, data: Any, handler: Any) -> Any:
        if isinstance(data, Field):
            data = data.model_dump()
        data = _as_field_dict(data)
        try:
            return handler(data)
        except ValidationError:
            raw = data.get("value")
            return handler(
                {
                    "value": None,
                    "confidence": 0.0,
                    "evidence": data.get("evidence") or _clip(raw),
                    "null_reason": _clip(f"unparseable value: {raw!r}"),
                }
            )

    @model_validator(mode="after")
    def _consistent(self) -> Field[T]:
        if isinstance(self.value, str) and not self.value.strip():
            self.value = None
            self.null_reason = self.null_reason or "empty value"
        if self.value is None:
            self.null_reason = self.null_reason or "not found in document"
        else:
            self.null_reason = None
        return self

    @property
    def present(self) -> bool:
        """True when the field has a value."""
        return self.value is not None


def doc_field(description: str) -> Any:
    """Schema attribute helper: a described Field that defaults to null if the model omits it.

    Use as ``total: Field[Number] = doc_field("amount paid incl. taxes")``.
    """
    return PField(
        default_factory=lambda: Field(value=None, null_reason="not returned by extractor"),
        description=description,
    )


def doc_list(description: str) -> Any:
    """Schema attribute helper for a list of line items that defaults to empty."""
    return PField(default_factory=list, description=description)


def known(value: T, confidence: float = 1.0, evidence: str | None = None) -> Field[T]:
    """Build a Field holding a known value (ground truth, tests, oracle runs)."""
    return Field(value=value, confidence=confidence, evidence=evidence)


def missing(reason: str = "not present") -> Field[Any]:
    """Build a null Field with an explicit reason."""
    return Field(value=None, confidence=1.0, null_reason=reason)


# --- lenient scalar types used inside Field[...] -------------------------------------------

_CURRENCY = re.compile(r"(?i)(₹|rs\.?|inr|\$|usd)")


def parse_number(raw: Any) -> Any:
    """Parse '₹ 1,23,456.50', 'Rs. 12', '12/-' into a float; pass non-strings through."""
    if isinstance(raw, str):
        text = _CURRENCY.sub("", raw).replace(",", "").replace("/-", "").strip()
        if not text:
            raise ValueError("empty number")
        return float(text)
    return raw


def _parse_date_value(raw: Any) -> Any:
    if isinstance(raw, str):
        return parse_date(raw)
    return raw


Number = Annotated[float, BeforeValidator(parse_number)]
"""A float that accepts currency symbols and Indian digit grouping."""

LenientDate = Annotated[date, BeforeValidator(_parse_date_value)]
"""A date that accepts ISO and common Indian (day-first) formats."""


# --- traversal --------------------------------------------------------------------------------


def iter_fields(model: BaseModel, prefix: str = "") -> Iterator[tuple[str, Field[Any]]]:
    """Yield ``(path, field)`` for every Field in a document, e.g. ``line_items[2].rate``."""
    for name in type(model).model_fields:
        value = getattr(model, name)
        path = f"{prefix}{name}"
        if isinstance(value, Field):
            yield path, value
        elif isinstance(value, BaseModel):
            yield from iter_fields(value, f"{path}.")
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, Field):
                    yield f"{path}[{i}]", item
                elif isinstance(item, BaseModel):
                    yield from iter_fields(item, f"{path}[{i}].")


def wildcard(path: str) -> str:
    """Replace list indices with ``*``: ``line_items[2].rate`` -> ``line_items[*].rate``."""
    return re.sub(r"\[\d+\]", "[*]", path)


def value_of(field: Field[T] | None) -> T | None:
    """Return a field's value, or None for a missing field."""
    return None if field is None else field.value
