"""Item matching across documents: deterministic rules first, an LLM only for what rules miss.

``MATCHING=rules`` uses exact / normalised / fuzzy string rules only.
``MATCHING=rules+llm`` additionally sends the still-unmatched items to a text LLM, which must
return a structured verdict with a reason for each.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from papertrail.core.validators.fuzzy import normalize_text, text_similarity

if TYPE_CHECKING:
    from papertrail.core.llm import LLMClient, Usage

MatchMethod = Literal["exact", "normalized", "fuzzy", "llm", "none"]


class MatchVerdict(BaseModel):
    """Which right-hand item (if any) a left-hand item corresponds to."""

    left: int
    right: int | None
    method: MatchMethod
    score: float
    reason: str


class _LLMMatch(BaseModel):
    left: int
    right: int | None = None
    reason: str = ""


class _LLMMatches(BaseModel):
    matches: list[_LLMMatch]


_SYSTEM = """You decide whether items on two business documents refer to the same thing.
Domain: {domain}.
For every LEFT item, give the index of the RIGHT item that is the same product (same drug
ingredient / same goods or service), or null if none is. Brand vs generic names of the same
ingredient match; different strengths or different products do not. Do not match on vague
similarity. Reply as JSON: {{"matches": [{{"left": 0, "right": 2 or null, "reason": "<=15 words"}}]}}"""


@dataclass
class Matcher:
    """Rule-first item matcher. ``llm``/``model`` are only used in ``rules+llm`` mode."""

    mode: str = "rules"
    llm: LLMClient | None = None
    model: str | None = None
    threshold: float = 88.0

    def __post_init__(self) -> None:
        self.llm_calls = 0

    @property
    def uses_llm(self) -> bool:
        """True when unmatched items will be sent to the LLM."""
        return self.mode == "rules+llm" and self.llm is not None and self.model is not None

    def match(
        self,
        left: Sequence[str],
        right: Sequence[str],
        *,
        domain: str,
        normalize: Callable[[str], str] | None = None,
    ) -> list[MatchVerdict]:
        """Return one verdict per left item. ``normalize`` maps items to a canonical key
        (e.g. brand -> generic ingredient) and is tried before fuzzy matching."""
        verdicts: list[MatchVerdict] = []
        left_n = [normalize_text(s) for s in left]
        right_n = [normalize_text(s) for s in right]
        left_c = [normalize(s) if normalize else None for s in left]
        right_c = [normalize(s) if normalize else None for s in right]
        for i, item in enumerate(left_n):
            verdict = MatchVerdict(left=i, right=None, method="none", score=0.0, reason="no rule matched")
            if item in right_n:
                j = right_n.index(item)
                verdict = MatchVerdict(left=i, right=j, method="exact", score=100.0, reason="identical text")
            elif left_c[i] and left_c[i] in right_c:
                j = right_c.index(left_c[i])
                verdict = MatchVerdict(
                    left=i, right=j, method="normalized", score=100.0,
                    reason=f"both normalise to '{left_c[i]}'",
                )
            elif right_n:
                scores = [text_similarity(item, r) for r in right_n]
                j = max(range(len(scores)), key=scores.__getitem__)
                if scores[j] >= self.threshold:
                    verdict = MatchVerdict(
                        left=i, right=j, method="fuzzy", score=scores[j],
                        reason=f"text similarity {scores[j]:.0f}",
                    )
            verdicts.append(verdict)

        unmatched = [v.left for v in verdicts if v.right is None]
        if unmatched and right and self.uses_llm:
            for v in self._llm_match(left, right, unmatched, domain):
                verdicts[v.left] = v
        return verdicts

    def _llm_match(
        self, left: Sequence[str], right: Sequence[str], indices: list[int], domain: str
    ) -> list[MatchVerdict]:
        assert self.llm is not None and self.model is not None
        lines = ["LEFT:"] + [f"{n}. {left[i]}" for n, i in enumerate(indices)]
        lines += ["RIGHT:"] + [f"{j}. {r}" for j, r in enumerate(right)]
        parsed, _ = self.llm.parse(
            _LLMMatches, model=self.model, system=_SYSTEM.format(domain=domain),
            user="\n".join(lines), max_tokens=1200,
        )
        self.llm_calls += 1
        out: list[MatchVerdict] = []
        for m in parsed.matches:
            if not 0 <= m.left < len(indices):
                continue
            right_idx = m.right if m.right is not None and 0 <= m.right < len(right) else None
            out.append(
                MatchVerdict(
                    left=indices[m.left], right=right_idx, method="llm" if right_idx is not None else "none",
                    score=0.0, reason=f"LLM: {m.reason}".strip(),
                )
            )
        return out

    @property
    def usage(self) -> Usage | None:
        """Token usage of the underlying client, if any."""
        return self.llm.usage if self.llm else None
