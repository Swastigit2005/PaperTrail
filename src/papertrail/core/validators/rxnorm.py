"""RxNorm drug-name validation (free NLM API, no key).

Adapted from the RxNorm validator in Hands-On-AI-Engineering's medical_prescription_digitizer
(MIT, see THIRD_PARTY_NOTICES.md). Extended with: strength/dose-form stripping, brand -> generic
ingredient normalisation, closest-match suggestions for misspellings, an on-disk cache, timeouts
with retries, and a distinction between "not found" and "service unavailable".
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel
from pydantic import Field as PField

RXNAV_BASE_URL = "https://rxnav.nlm.nih.gov/REST"
REQUEST_TIMEOUT = 10.0

_STRENGTH = re.compile(
    r"\b\d+(\.\d+)?\s*(mg|mcg|µg|g|gm|ml|iu|units?|%|mg/ml|mg/5ml)\b|\b\d+(\.\d+)?\b", re.I
)
_FORMS = re.compile(
    r"\b(tab|tabs|tablet|tablets|cap|caps|capsule|capsules|syp|syrup|susp|suspension|inj|"
    r"injection|oint|ointment|cream|gel|drops?|sachet|er|sr|xr|cr|dt|md|forte|plus)\b\.?",
    re.I,
)


def strip_strength(name: str) -> str:
    """Reduce 'Tab. Amoxicillin 500 mg' to 'amoxicillin' for lookup."""
    text = _FORMS.sub(" ", _STRENGTH.sub(" ", name))
    text = re.sub(r"[^A-Za-z\- ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


class DrugLookup(BaseModel):
    """Outcome of validating one drug name."""

    query: str
    normalized_query: str
    found: bool
    rxcui: str | None = None
    name: str | None = None
    ingredients: list[str] = PField(default_factory=list)
    suggestion: str | None = None
    error: str | None = None  # set when RxNorm could not be reached: result is unknown

    @property
    def generic_names(self) -> list[str]:
        """Lowercased ingredient names, falling back to the matched name."""
        names = self.ingredients or ([self.name] if self.name else [])
        return [n.lower() for n in names]


class RxNormClient:
    """Small RxNav client with caching and retries. Pass ``http`` to inject a mock transport."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        http: httpx.Client | None = None,
        timeout: float = REQUEST_TIMEOUT,
        retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http or httpx.Client(base_url=RXNAV_BASE_URL, timeout=timeout)
        self._retries = retries
        self._sleep = sleep
        self._cache_dir = cache_dir / "rxnorm" if cache_dir else None
        self._memo: dict[str, DrugLookup] = {}

    # -- public -------------------------------------------------------------------------

    def lookup(self, drug_name: str) -> DrugLookup:
        """Validate a drug name; returns ingredients for brands and a suggestion when not found."""
        query = strip_strength(drug_name)
        if not query:
            return DrugLookup(query=drug_name, normalized_query="", found=False)
        if query in self._memo:
            return self._memo[query].model_copy(update={"query": drug_name})
        cached = self._read_cache(query)
        if cached is not None:
            result = cached.model_copy(update={"query": drug_name})
        else:
            try:
                result = self._lookup_remote(drug_name, query)
            except httpx.HTTPError as exc:  # do not cache outages
                return DrugLookup(
                    query=drug_name, normalized_query=query, found=False, error=f"rxnorm_unavailable: {exc}"
                )
            self._write_cache(query, result)
        self._memo[query] = result
        return result

    # -- internals ----------------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                resp = self._http.get(path, params=params)
                if resp.status_code >= 500 or resp.status_code == 429:
                    raise httpx.HTTPStatusError("server error", request=resp.request, response=resp)
                resp.raise_for_status()
                return resp.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last = exc
                if attempt < self._retries:
                    self._sleep(0.5 * 2**attempt)
        assert last is not None
        raise last

    def _lookup_remote(self, original: str, query: str) -> DrugLookup:
        data = self._get("/rxcui.json", {"name": query, "search": 2})
        ids = data.get("idGroup", {}).get("rxnormId") or []
        if ids:
            rxcui = ids[0]
            name, ingredients = self._describe(rxcui)
            return DrugLookup(
                query=original, normalized_query=query, found=True, rxcui=rxcui,
                name=name, ingredients=ingredients,
            )
        return DrugLookup(
            query=original, normalized_query=query, found=False, suggestion=self._suggest(query)
        )

    def _describe(self, rxcui: str) -> tuple[str | None, list[str]]:
        props = self._get(f"/rxcui/{rxcui}/properties.json").get("properties") or {}
        name, tty = props.get("name"), props.get("tty")
        if tty in ("IN", "PIN", "MIN"):
            return name, [name] if name else []
        related = self._get(f"/rxcui/{rxcui}/related.json", {"tty": "IN"})
        ingredients: list[str] = []
        for group in related.get("relatedGroup", {}).get("conceptGroup") or []:
            for concept in group.get("conceptProperties") or []:
                if concept.get("name"):
                    ingredients.append(concept["name"])
        return name, ingredients

    def _suggest(self, query: str) -> str | None:
        data = self._get("/approximateTerm.json", {"term": query, "maxEntries": 3})
        for cand in data.get("approximateGroup", {}).get("candidate") or []:
            if cand.get("name"):
                return str(cand["name"]).lower()
            if cand.get("rxcui"):
                props = self._get(f"/rxcui/{cand['rxcui']}/properties.json").get("properties") or {}
                if props.get("name"):
                    return str(props["name"]).lower()
        return None

    def _cache_path(self, query: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / f"{hashlib.sha1(query.encode()).hexdigest()}.json"

    def _read_cache(self, query: str) -> DrugLookup | None:
        path = self._cache_path(query)
        if path is None or not path.exists():
            return None
        try:
            return DrugLookup.model_validate_json(path.read_text())
        except (OSError, ValueError):
            return None

    def _write_cache(self, query: str, result: DrugLookup) -> None:
        path = self._cache_path(query)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result.model_dump()))
        except OSError:
            pass  # read-only filesystem: caching is best-effort
