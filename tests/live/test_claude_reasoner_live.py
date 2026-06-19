import os
from datetime import date
from decimal import Decimal

import pytest

from invoicer.adapters.claude_reasoner import ClaudeExceptionReasoner
from invoicer.models import Classification, CountryBucket, Invoice, LineItem, Party, TaxTreatment

pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"), reason="wymaga ANTHROPIC_API_KEY (test live)"
)


def _uk_saas_invoice() -> Invoice:
    line = LineItem(
        description="SaaS subscription",
        quantity=Decimal("1"),
        unit_net=Decimal("1000.00"),
        vat_rate=Decimal("0.00"),
        net=Decimal("1000.00"),
        vat=Decimal("0.00"),
        gross=Decimal("1000.00"),
    )
    return Invoice(
        seller=Party(name="UK SaaS Ltd", country="GB", vat_id="GB1"),
        buyer=Party(name="Klient", nip="5260001246", country="PL"),
        number="INV/7",
        issue_date=date(2026, 6, 1),
        currency="GBP",
        lines=[line],
        total_net=Decimal("1000.00"),
        total_vat=Decimal("0.00"),
        total_gross=Decimal("1000.00"),
    )


def test_real_judge_classifies_uk_saas_as_import_uslug():
    base = Classification(
        treatment=TaxTreatment.IMPORT_USLUG, country_bucket=CountryBucket.POZA_UE, confidence=0.6
    )
    out = ClaudeExceptionReasoner().reason(_uk_saas_invoice(), base)
    assert out.country_bucket == CountryBucket.POZA_UE
    assert out.treatment == TaxTreatment.IMPORT_USLUG  # SaaS = usluga
    assert out.rationale_pl  # niepuste uzasadnienie
