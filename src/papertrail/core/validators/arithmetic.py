"""Arithmetic checks over extracted Fields: line amounts and totals, within a tolerance."""

from __future__ import annotations

from collections.abc import Sequence

from papertrail.core.fields import Field
from papertrail.core.findings import Finding, Severity, unverifiable


def close(a: float, b: float, abs_tol: float = 1.0, rel_tol: float = 0.005) -> bool:
    """True when two money amounts agree within max(abs_tol, rel_tol * |b|) (rounding slack)."""
    return abs(a - b) <= max(abs_tol, rel_tol * abs(b))


def line_amount_check(
    qty: Field[float],
    rate: Field[float],
    amount: Field[float],
    *,
    paths: tuple[str, str, str],
    code: str = "line_amount_mismatch",
    severity: Severity = Severity.HARD,
) -> Finding | None:
    """Check qty x rate == amount for one line. ``paths`` are (qty, rate, amount) refs."""
    if qty.value is None or rate.value is None or amount.value is None:
        if amount.value is None:
            return unverifiable(code, f"cannot verify {paths[2]}: value missing", *paths)
        return None  # amount alone is still usable for totals; nothing to compare
    expected = qty.value * rate.value
    if close(amount.value, expected):
        return None
    return Finding(
        code=code,
        severity=severity,
        message=(
            f"{paths[2]} is {amount.value:,.2f} but {paths[0]} x {paths[1]} = "
            f"{qty.value:g} x {rate.value:,.2f} = {expected:,.2f}"
        ),
        fields=list(paths),
        detail={"expected": round(expected, 2), "found": amount.value},
    )


def sum_check(
    parts: Sequence[tuple[str, Field[float]]],
    total: tuple[str, Field[float]],
    *,
    code: str,
    label: str,
    severity: Severity = Severity.HARD,
    abs_tol: float = 1.0,
) -> Finding | None:
    """Check that the part values add up to ``total``. ``label`` names the total in messages."""
    total_path, total_field = total
    if total_field.value is None:
        return unverifiable(code, f"cannot verify {label}: {total_path} missing", total_path)
    missing_parts = [p for p, f in parts if f.value is None]
    if missing_parts:
        return unverifiable(
            code, f"cannot verify {label}: {', '.join(missing_parts)} missing", *missing_parts
        )
    expected = sum(f.value or 0.0 for _, f in parts)
    if close(total_field.value, expected, abs_tol=abs_tol):
        return None
    return Finding(
        code=code,
        severity=severity,
        message=f"{label} {total_field.value:,.2f} does not equal the sum of its parts {expected:,.2f}",
        fields=[total_path, *(p for p, _ in parts)],
        detail={"expected": round(expected, 2), "found": total_field.value},
    )
