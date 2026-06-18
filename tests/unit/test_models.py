from datetime import date
from decimal import Decimal

from invoicer.models import (
    Check,
    CheckStatus,
    Invoice,
    LineItem,
    Party,
    ValidationResult,
)


def _sample_invoice() -> Invoice:
    line = LineItem(
        description="Usluga programistyczna",
        quantity=Decimal("1"),
        unit_net=Decimal("1000.00"),
        vat_rate=Decimal("0.23"),
        net=Decimal("1000.00"),
        vat=Decimal("230.00"),
        gross=Decimal("1230.00"),
    )
    return Invoice(
        seller=Party(name="ACME sp. z o.o.", nip="5260001246", country="PL"),
        buyer=Party(name="Klient sp. z o.o.", nip="1234563218", country="PL"),
        number="FV/2026/06/01",
        issue_date=date(2026, 6, 1),
        currency="PLN",
        lines=[line],
        total_net=Decimal("1000.00"),
        total_vat=Decimal("230.00"),
        total_gross=Decimal("1230.00"),
    )


def test_invoice_builds_and_holds_values():
    inv = _sample_invoice()
    assert inv.seller.country == "PL"
    assert inv.lines[0].gross == Decimal("1230.00")
    assert inv.total_gross == Decimal("1230.00")


def test_party_defaults_country_pl_and_optional_nip():
    p = Party(name="Foreign Ltd", country="GB")
    assert p.nip is None
    assert p.country == "GB"


def test_validation_result_partitions_checks():
    vr = ValidationResult(
        checks=[
            Check(name="nip", status=CheckStatus.PASS),
            Check(name="sums", status=CheckStatus.FAIL, detail="niespojne"),
            Check(name="lines", status=CheckStatus.WARN, detail="ostrzezenie"),
        ]
    )
    assert vr.ok is False
    assert [c.name for c in vr.hard_errors] == ["sums"]
    assert [c.name for c in vr.soft_flags] == ["lines"]
