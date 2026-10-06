"""Generic rule engine for document-vs-document checks.

A pack contributes ``CrossCheck`` objects; each is a named function from the case's documents
(and a ``CheckContext``) to a list of Findings. The engine runs them, isolates failures, and
summarises each as pass / fail / unverifiable / error.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel
from pydantic import Field as PField

from papertrail.core.findings import Finding, Severity
from papertrail.core.images import PreparedImage
from papertrail.core.matching import Matcher
from papertrail.core.validators.duplicates import DuplicateIndex


@dataclass
class CheckContext:
    """Everything a validator or cross-check may use besides the documents themselves."""

    matcher: Matcher
    case_id: str = "case"
    today: date = field(default_factory=date.today)
    images: Mapping[str, PreparedImage] = field(default_factory=dict)
    history: DuplicateIndex | None = None
    services: dict[str, Any] = field(default_factory=dict)  # e.g. {"rxnorm": RxNormClient}


CheckFn = Callable[[Mapping[str, BaseModel], CheckContext], list[Finding]]


@dataclass(frozen=True)
class CrossCheck:
    """A named document-vs-document rule."""

    code: str
    description: str
    run: CheckFn


class CheckStatus(StrEnum):
    """Summary state of one cross-check."""

    PASS = "pass"
    FAIL = "fail"
    UNVERIFIABLE = "unverifiable"
    ERROR = "error"


class CheckOutcome(BaseModel):
    """Result of running one cross-check."""

    code: str
    description: str
    status: CheckStatus
    findings: list[Finding] = PField(default_factory=list)


def _status(findings: list[Finding]) -> CheckStatus:
    relevant = [f for f in findings if f.severity != Severity.INFO]
    if not relevant:
        return CheckStatus.PASS
    if all(f.unverifiable for f in relevant):
        return CheckStatus.UNVERIFIABLE
    return CheckStatus.FAIL


def run_crosschecks(
    checks: list[CrossCheck], docs: Mapping[str, BaseModel], ctx: CheckContext
) -> list[CheckOutcome]:
    """Run every check; an exception inside a check becomes an ERROR outcome, never a crash."""
    outcomes: list[CheckOutcome] = []
    for check in checks:
        try:
            findings = check.run(docs, ctx)
            status = _status(findings)
        except Exception as exc:  # a buggy rule must not take down the case
            findings = [
                Finding(
                    code=f"{check.code}_error",
                    severity=Severity.SOFT,
                    message=f"check '{check.code}' failed to run: {exc}",
                    unverifiable=True,
                )
            ]
            status = CheckStatus.ERROR
        outcomes.append(
            CheckOutcome(code=check.code, description=check.description, status=status, findings=findings)
        )
    return outcomes
