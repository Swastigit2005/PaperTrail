"""Findings: the common currency of validators, cross-checks and decisions."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel
from pydantic import Field as PField


class Severity(StrEnum):
    """How much a finding should move the decision."""

    INFO = "info"  # recorded, no effect on the decision
    SOFT = "soft"  # needs a human look -> needs_review
    HARD = "hard"  # a discrepancy -> flag


class Finding(BaseModel):
    """A single validation or cross-check result that points at exact fields.

    ``fields`` uses ``<doc_type>.<path>`` references, e.g. ``bill.line_items[1].amount``.
    """

    code: str
    severity: Severity
    message: str
    fields: list[str] = PField(default_factory=list)
    unverifiable: bool = False
    detail: dict[str, Any] = PField(default_factory=dict)


def hard(code: str, message: str, *fields: str, **detail: Any) -> Finding:
    """A discrepancy that should flag the case."""
    return Finding(code=code, severity=Severity.HARD, message=message, fields=list(fields), detail=detail)


def soft(code: str, message: str, *fields: str, **detail: Any) -> Finding:
    """A mismatch that a human should review."""
    return Finding(code=code, severity=Severity.SOFT, message=message, fields=list(fields), detail=detail)


def info(code: str, message: str, *fields: str, **detail: Any) -> Finding:
    """A note with no effect on the decision."""
    return Finding(code=code, severity=Severity.INFO, message=message, fields=list(fields), detail=detail)


def unverifiable(code: str, message: str, *fields: str) -> Finding:
    """A check that could not run because an input field was null: route to review."""
    return Finding(
        code=code, severity=Severity.SOFT, message=message, fields=list(fields), unverifiable=True
    )
