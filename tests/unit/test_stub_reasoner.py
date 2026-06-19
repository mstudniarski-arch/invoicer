from invoicer.adapters.stub_reasoner import IdentityReasoner, StubExceptionReasoner
from invoicer.models import Classification, CountryBucket, TaxTreatment
from invoicer.ports import ExceptionReasoner


def _base() -> Classification:
    return Classification(
        treatment=TaxTreatment.IMPORT_USLUG,
        country_bucket=CountryBucket.POZA_UE,
        confidence=0.6,
        rationale_pl="deterministyczne",
    )


def test_identity_reasoner_satisfies_protocol():
    assert isinstance(IdentityReasoner(), ExceptionReasoner)


def test_identity_reasoner_returns_base_unchanged():
    base = _base()
    out = IdentityReasoner().reason(invoice=None, base=base)
    assert out == base


def test_stub_reasoner_returns_preset():
    preset = Classification(
        treatment=TaxTreatment.IMPORT_TOWAROW,
        country_bucket=CountryBucket.POZA_UE,
        confidence=0.9,
        rationale_pl="towar",
    )
    out = StubExceptionReasoner(preset).reason(invoice=None, base=_base())
    assert out.treatment == TaxTreatment.IMPORT_TOWAROW
    assert isinstance(StubExceptionReasoner(preset), ExceptionReasoner)
