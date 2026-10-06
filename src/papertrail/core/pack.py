"""Pack interface and registry.

A pack is a business workflow (claims, payables, ...). It declares its document types and
schemas, per-document validators, cross-document checks and decision policy. Packs live in
``papertrail.packs.<name>`` (auto-discovered) or in any module listed in ``PAPERTRAIL_PACKS``,
and register themselves with ``@register``. Core never imports a specific pack.
"""

from __future__ import annotations

import importlib
import inspect
import os
import pkgutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel

from papertrail.core.crosscheck import CheckContext, CrossCheck
from papertrail.core.decision import Decision, DecisionPolicy, decide
from papertrail.core.findings import Finding


@dataclass(frozen=True)
class DocType:
    """One document type a pack understands.

    ``keywords`` drive cheap text-based auto-detection; ``hints`` are appended to the
    extraction prompt (abbreviations, layout conventions).
    """

    name: str
    title: str
    schema: type[BaseModel]
    description: str
    keywords: tuple[str, ...] = ()
    hints: str = ""


class Pack:
    """Base class for packs. Override the class attributes and the hook methods you need."""

    name: ClassVar[str]
    title: ClassVar[str]
    description: ClassVar[str]
    doc_types: ClassVar[tuple[DocType, ...]]
    policy: ClassVar[DecisionPolicy] = DecisionPolicy()
    benchmarked: ClassVar[bool] = True

    def doc_type(self, name: str) -> DocType:
        """Look up one of this pack's document types by name."""
        for dt in self.doc_types:
            if dt.name == name:
                return dt
        raise KeyError(f"pack '{self.name}' has no document type '{name}'")

    def validate(self, doc_type: str, doc: BaseModel, ctx: CheckContext) -> list[Finding]:
        """Per-document validators. Field refs in findings must be prefixed with ``doc_type``."""
        return []

    def crosschecks(self) -> Sequence[CrossCheck]:
        """Document-vs-document checks for this pack."""
        return ()

    def decide(self, findings: list[Finding], docs: Mapping[str, BaseModel]) -> Decision:
        """Decision rules; the default applies ``policy`` via the generic decision engine."""
        return decide(findings, docs, self.policy)

    @property
    def samples_dir(self) -> Path:
        """``samples/`` folder next to the pack's module."""
        return Path(inspect.getfile(type(self))).parent / "samples"


_REGISTRY: dict[str, Pack] = {}
_discovered = False


def register(cls: type[Pack]) -> type[Pack]:
    """Class decorator: instantiate and register a pack under ``cls.name``."""
    for attr in ("name", "title", "description", "doc_types"):
        if not hasattr(cls, attr):
            raise TypeError(f"pack {cls.__name__} must define '{attr}'")
    names = [dt.name for dt in cls.doc_types]
    if len(set(names)) != len(names):
        raise TypeError(f"pack {cls.name} has duplicate document type names: {names}")
    _REGISTRY[cls.name] = cls()
    return cls


def unregister(name: str) -> None:
    """Remove a pack (used by tests)."""
    _REGISTRY.pop(name, None)


def discover_packs() -> None:
    """Import every module under ``papertrail.packs`` plus any listed in ``PAPERTRAIL_PACKS``."""
    global _discovered
    if _discovered:
        return
    import papertrail.packs as builtin

    for info in pkgutil.iter_modules(builtin.__path__):
        importlib.import_module(f"{builtin.__name__}.{info.name}")
    for module in filter(None, (m.strip() for m in os.getenv("PAPERTRAIL_PACKS", "").split(","))):
        importlib.import_module(module)
    _discovered = True


def get_pack(name: str) -> Pack:
    """Return a registered pack by name (discovering packs on first use)."""
    discover_packs()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown pack '{name}'; available: {sorted(_REGISTRY)}") from None


def list_packs() -> list[Pack]:
    """All registered packs, sorted by name."""
    discover_packs()
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]
