# Adding a pack

A **pack** is one business workflow: the document types it reads, how each document is
validated, how documents are checked against each other, and how a case is routed. The engine
in `src/papertrail/core/` is generic. **Adding a pack never requires editing `core/`.** A test
(`tests/test_pack_registry.py`) registers a throwaway pack from outside the package to keep that
promise honest.

The `expenses` pack was built by following this guide step by step; use it as the worked
example.

## 0. Pick the documents and the questions

Write down, in plain words:

- the documents in a case (e.g. *receipt* + *expense claim form*);
- what can be wrong **inside** one document (totals don't add up, date in the future);
- what can be wrong **between** them (claimed amount ≠ receipt total);
- which of those are hard discrepancies (**flag**) and which need a human look (**review**).

## 1. Create the folder

```
src/papertrail/packs/<name>/
  __init__.py      # the Pack subclass + @register
  schemas.py       # one Pydantic model per document type
  validators.py    # per-document checks
  crosschecks.py   # document-vs-document checks
  samples/         # sample pairs for the demo (images + manifest.json)
```

Built-in packs in `papertrail/packs/` are discovered automatically. A pack in another package
is loaded by listing its module in `PAPERTRAIL_PACKS=my_company.packs.travel`.

## 2. Schemas: every leaf is a `Field[T]`

```python
from pydantic import BaseModel
from papertrail.core.fields import Field, LenientDate, Number, doc_field, doc_list

class ReceiptLine(BaseModel):
    description: Field[str] = doc_field("item name as printed")
    amount: Field[Number] = doc_field("line amount")

class Receipt(BaseModel):
    merchant: Field[str] = doc_field("business name printed at the top")
    date: Field[LenientDate] = doc_field("transaction date")
    items: list[ReceiptLine] = doc_list("purchased items")
    total: Field[Number] = doc_field("amount paid, incl. taxes")
```

- `Field[T]` carries `value`, `confidence`, `evidence` and `null_reason`. The extractor is told to
  leave unreadable values null with a reason. It never guesses.
- Use `Number` for money and quantities (it accepts `₹ 1,23,456.50`), and `LenientDate` for dates
  (it reads day-first formats).
- Declare attributes with `doc_field(...)` / `doc_list(...)`. They default to null or empty, so a
  partially read document still parses, and a missing field is visibly null with a reason.
- The descriptions go into the prompt. They are the cheapest accuracy lever you have.

## 3. Declare document types

```python
from papertrail.core.pack import DocType

RECEIPT = DocType(
    name="receipt", title="shop receipt", schema=Receipt,
    description="a till or tax receipt from a merchant",
    keywords=("receipt", "cash memo", "thank you", "gst"),   # for free auto-detection
    hints="Totals may be labelled 'Net Amt' or 'Grand Total'.",  # appended to the prompt
)
```

`name` is the prefix for field references in findings (`receipt.total`).

## 4. Per-document validators

`validate(doc_type, doc, ctx)` returns a list of `Finding`s. Reuse the core validators:

| Need | Use |
|---|---|
| qty × rate = amount, parts sum to total | `core.validators.arithmetic.line_amount_check`, `sum_check` |
| plausible dates | `core.validators.dates.sanity_problem` |
| GSTIN format + checksum, state codes | `core.validators.gstin.validate_gstin`, `same_state` |
| person / description similarity | `core.validators.fuzzy.name_similarity`, `text_similarity` |
| drug names | `core.validators.rxnorm.RxNormClient` (pass it via `ctx.services["rxnorm"]`) |
| duplicates | `core.validators.duplicates.fingerprint` + `ctx.history` |

Build findings with `hard(...)`, `soft(...)`, `info(...)` or, when an input field is null,
`unverifiable(...)` from `core.findings`. Always pass the exact field references:

```python
hard("receipt_total_mismatch", "receipt.total 1,200.00 != items 1,150.00",
     "receipt.total", "receipt.items[0].amount")
```

## 5. Cross-checks

Each check is a `CrossCheck(code, description, run)` where `run(docs, ctx) -> list[Finding]`.
`docs` maps doc-type name to the parsed document. Keep each check small and single-purpose:
the benchmark scores detection **per check code**.

For item matching (medicines, invoice lines), use `ctx.matcher.match(left, right, domain=...,
normalize=...)`. It applies exact, normalised and fuzzy rules, and only in `MATCHING=rules+llm`
asks the LLM about items that rules left unmatched. Every verdict carries a method and a reason.

The engine catches exceptions in a check and reports them as `error` outcomes (needs review),
so one buggy rule cannot crash a case.

## 6. Decision policy

```python
from papertrail.core.decision import DecisionPolicy

policy = DecisionPolicy(
    min_confidence=0.75,
    critical_fields={"receipt": ("total", "date"), "expense_claim": ("amount_claimed",)},
    review_only=frozenset({"merchant_mismatch"}),   # hard in principle, review in this pack
)
```

Default routing: any hard finding → **flag**; any soft finding, unverifiable check, or critical
field that is null or below `min_confidence` → **needs_review**; otherwise → **approve**.
Override `Pack.decide` only if the pack needs something this policy can't express.

## 7. Register the pack

```python
from papertrail.core.pack import Pack, register

@register
class ExpensesPack(Pack):
    name = "expenses"
    title = "Expense reimbursement"
    description = "Receipt + expense claim: amount, policy window, duplicates."
    doc_types = (RECEIPT, EXPENSE_CLAIM)
    policy = policy
    benchmarked = False

    def validate(self, doc_type, doc, ctx): ...
    def crosschecks(self): return CHECKS
```

## 8. Samples and tests

- Put sample pairs in `samples/` with a `manifest.json` (see an existing pack). The demo picks
  them up automatically.
- Add `tests/test_<name>_pack.py` with hand-made documents built from `known(...)`/`missing(...)`
  fields: at least one clean case that approves and one case per cross-check that fails it.
- Run `pytest` and `ruff check .`

## Checklist

- [ ] No file under `src/papertrail/core/` changed (`git diff --stat src/papertrail/core` is empty)
- [ ] Every schema leaf is a `Field[...]` with a description
- [ ] Every finding names the exact fields it is about
- [ ] Null inputs produce `unverifiable(...)`, never a silent pass
- [ ] Tests: one approve case plus one failing case per cross-check
