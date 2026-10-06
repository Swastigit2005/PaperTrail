"""Turn findings and field confidences into approve / needs_review / flag, with reasons.

The engine only recommends; a human always makes the final call.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel
from pydantic import Field as PField

from papertrail.core.fields import iter_fields, wildcard
from papertrail.core.findings import Finding, Severity


class Outcome(StrEnum):
    """Recommended routing for a case."""

    APPROVE = "approve"
    NEEDS_REVIEW = "needs_review"
    FLAG = "flag"


class Reason(BaseModel):
    """One human-readable reason, pointing at the exact fields involved."""

    code: str
    severity: Severity
    message: str
    fields: list[str] = PField(default_factory=list)


class Decision(BaseModel):
    """The routing recommendation and every reason behind it."""

    outcome: Outcome
    reasons: list[Reason]


@dataclass(frozen=True)
class DecisionPolicy:
    """Per-pack decision rules.

    ``critical_fields`` maps doc type -> field paths (``[*]`` wildcards allowed) whose low
    confidence or absence should send the case to review. ``review_only`` lists finding codes that
    are hard in principle but should only trigger review in this pack.
    """

    min_confidence: float = 0.75
    critical_fields: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    review_only: frozenset[str] = frozenset()


def confidence_findings(docs: Mapping[str, BaseModel], policy: DecisionPolicy) -> list[Finding]:
    """Findings for critical fields that are missing or below the confidence threshold."""
    out: list[Finding] = []
    for doc_type, doc in docs.items():
        wanted = set(policy.critical_fields.get(doc_type, ()))
        if not wanted:
            continue
        for path, fld in iter_fields(doc):
            if path not in wanted and wildcard(path) not in wanted:
                continue
            ref = f"{doc_type}.{path}"
            if fld.value is None:
                out.append(
                    Finding(code="critical_field_missing", severity=Severity.SOFT,
                            message=f"{ref} is empty ({fld.null_reason})", fields=[ref])
                )
            elif fld.confidence < policy.min_confidence:
                out.append(
                    Finding(code="low_confidence", severity=Severity.SOFT,
                            message=f"{ref} read with low confidence ({fld.confidence:.2f}): {fld.value!r}",
                            fields=[ref], detail={"confidence": fld.confidence})
                )
    return out


def decide(
    findings: list[Finding], docs: Mapping[str, BaseModel], policy: DecisionPolicy
) -> Decision:
    """Hard findings flag; soft findings or low-confidence critical fields need review."""
    all_findings = list(findings) + confidence_findings(docs, policy)
    reasons: list[Reason] = []
    worst = Outcome.APPROVE
    for f in all_findings:
        severity = f.severity
        if severity == Severity.HARD and f.code in policy.review_only:
            severity = Severity.SOFT
        if severity == Severity.INFO:
            continue
        reasons.append(Reason(code=f.code, severity=severity, message=f.message, fields=f.fields))
        if severity == Severity.HARD:
            worst = Outcome.FLAG
        elif worst == Outcome.APPROVE:
            worst = Outcome.NEEDS_REVIEW
    reasons.sort(key=lambda r: 0 if r.severity == Severity.HARD else 1)
    if not reasons:
        reasons = [
            Reason(code="all_clear", severity=Severity.INFO,
                   message="All validations and cross-checks passed; critical fields read with high confidence.")
        ]
    return Decision(outcome=worst, reasons=reasons)
