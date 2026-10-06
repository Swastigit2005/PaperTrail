"""Prompt construction shared by both pipelines.

Instead of a full JSON Schema (thousands of tokens once every leaf is a Field object), the model
gets a compact template generated from the Pydantic schema, which keeps prompts within the free
tier's tokens-per-minute budget.
"""

from __future__ import annotations

import types
import typing
from datetime import date
from typing import Any, get_args, get_origin

from pydantic import BaseModel

from papertrail.core.fields import Field

RULES = """Every leaf in the template is a compact field object F:
  {"v": <typed value or null>, "c": <confidence 0..1>, "e": "<exact source text, only if different from v>",
   "n": "<null reason, only when v is null>"}
Examples: {"v": 2.5, "c": 0.9}   {"v": "2026-09-03", "c": 0.95, "e": "03/09/26"}
          {"v": null, "c": 0.8, "n": "illegible"}
Rules:
- Never guess. If a value is illegible, cut off, covered or absent, set v to null and n to "illegible",
  "cut off", "covered", "not present" or a short explanation. Do not infer values that are not written.
- c is your probability that v is exactly right as written on the document; lower it for hard-to-read text.
- e must be copied from the document, not paraphrased; omit e when it would equal v.
- Dates as YYYY-MM-DD (documents are Indian: numeric dates are day-first). Numbers as plain JSON
  numbers without currency symbols or thousands separators.
- Lists: include every row present, in document order. An empty list if there are none.
- Reply with one compact JSON object (no indentation) matching the template, nothing else."""


def _type_label(tp: Any) -> str:
    origin = get_origin(tp)
    if origin is typing.Annotated:
        return _type_label(get_args(tp)[0])
    if origin in (typing.Union, types.UnionType):
        return " | ".join(_type_label(a) for a in get_args(tp) if a is not type(None))
    if tp is float or tp is int:
        return "number"
    if tp is str:
        return "string"
    if tp is bool:
        return "boolean"
    if tp is date:
        return "YYYY-MM-DD"
    if origin is typing.Literal:
        return " | ".join(repr(a) for a in get_args(tp))
    return getattr(tp, "__name__", str(tp))


def _field_param(tp: Any) -> Any | None:
    if isinstance(tp, type) and issubclass(tp, Field):
        args = tp.__pydantic_generic_metadata__.get("args") or ()
        return args[0] if args else Any
    return None


def _render(model: type[BaseModel], indent: int) -> list[str]:
    pad = "  " * indent
    lines = []
    for name, info in model.model_fields.items():
        tp = info.annotation
        note = f"  // {info.description}" if info.description else ""
        param = _field_param(tp)
        if param is not None:
            lines.append(f'{pad}"{name}": F<{_type_label(param)}>,{note}')
        elif get_origin(tp) is list and isinstance(get_args(tp)[0], type) and issubclass(get_args(tp)[0], BaseModel):
            lines.append(f'{pad}"{name}": [{{{note}')
            lines.extend(_render(get_args(tp)[0], indent + 2))
            lines.append(f"{pad}  }}, ...],")
        elif isinstance(tp, type) and issubclass(tp, BaseModel):
            lines.append(f'{pad}"{name}": {{{note}')
            lines.extend(_render(tp, indent + 1))
            lines.append(f"{pad}}},")
        else:
            lines.append(f'{pad}"{name}": {_type_label(tp)},{note}')
    return lines


def template(model: type[BaseModel]) -> str:
    """Compact JSON-like template of a schema; ``F<t>`` marks a Field holding a ``t``."""
    return "{\n" + "\n".join(_render(model, 1)) + "\n}"


def system_prompt(title: str, description: str, schema: type[BaseModel], hints: str = "") -> str:
    """System prompt for extracting one document type."""
    parts = [
        f"You extract structured data from a {title}: {description}",
        RULES,
        "Template:\n" + template(schema),
    ]
    if hints:
        parts.append("Document conventions:\n" + hints)
    return "\n\n".join(parts)
