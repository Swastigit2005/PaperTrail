"""A pack defined entirely outside papertrail/ registers and runs without touching core/."""

import re
from pathlib import Path

from pydantic import BaseModel

from papertrail.core.crosscheck import CheckContext, CrossCheck
from papertrail.core.decision import DecisionPolicy, Outcome
from papertrail.core.engine import evaluate
from papertrail.core.fields import Field, Number, doc_field, known
from papertrail.core.findings import hard
from papertrail.core.matching import Matcher
from papertrail.core.pack import DocType, Pack, get_pack, list_packs, register, unregister

CORE = Path(__file__).resolve().parents[1] / "src" / "papertrail" / "core"


class Ticket(BaseModel):
    fare: Field[Number] = doc_field("fare")


class Booking(BaseModel):
    fare: Field[Number] = doc_field("fare")


def fares_match(docs, ctx):
    if docs["ticket"].fare.value != docs["booking"].fare.value:
        return [hard("fare_mismatch", "fares differ", "ticket.fare", "booking.fare")]
    return []


def test_dummy_pack_registers_and_evaluates():
    @register
    class TravelPack(Pack):
        name = "dummy_travel"
        title = "Travel"
        description = "test-only pack"
        doc_types = (
            DocType("ticket", "ticket", Ticket, "a ticket"),
            DocType("booking", "booking", Booking, "a booking"),
        )
        policy = DecisionPolicy(critical_fields={"ticket": ("fare",)})

        def crosschecks(self):
            return (CrossCheck("fare_mismatch", "fares match", fares_match),)

    try:
        pack = get_pack("dummy_travel")
        assert pack in list_packs()
        ctx = CheckContext(matcher=Matcher())
        same = {"ticket": Ticket(fare=known(10.0)), "booking": Booking(fare=known(10.0))}
        _, outcomes, decision = evaluate(pack, same, ctx)
        assert decision.outcome == Outcome.APPROVE and outcomes[0].status == "pass"
        differ = {"ticket": Ticket(fare=known(10.0)), "booking": Booking(fare=known(12.0))}
        _, _, decision = evaluate(pack, differ, ctx)
        assert decision.outcome == Outcome.FLAG and decision.reasons[0].fields == ["ticket.fare", "booking.fare"]
    finally:
        unregister("dummy_travel")


def test_builtin_packs_discovered():
    assert "expenses" in {p.name for p in list_packs()}


def test_core_never_imports_a_specific_pack():
    offenders = []
    for path in CORE.rglob("*.py"):
        if re.search(r"papertrail\.packs\.\w+", path.read_text()):
            offenders.append(path.name)
    assert offenders == []
