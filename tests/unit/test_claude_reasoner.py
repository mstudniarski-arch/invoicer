from datetime import date
from decimal import Decimal

from invoicer.adapters.claude_reasoner import (
    REASON_PROMPT,
    ClaudeExceptionReasoner,
    build_reason_message,
)
from invoicer.models import (
    Citation,
    Classification,
    CountryBucket,
    Invoice,
    LineItem,
    Party,
    TaxTreatment,
)
from invoicer.ports import ExceptionReasoner
from invoicer.rag.models import RetrievedChunk
from invoicer.reasoning import ClassificationJudgment


def _foreign_invoice() -> Invoice:
    line = LineItem(
        description="Subskrypcja SaaS",
        quantity=Decimal("1"),
        unit_net=Decimal("1000.00"),
        vat_rate=Decimal("0.00"),
        net=Decimal("1000.00"),
        vat=Decimal("0.00"),
        gross=Decimal("1000.00"),
    )
    return Invoice(
        seller=Party(name="Foreign Ltd", country="GB", vat_id="GB123", address="London Str 1"),
        buyer=Party(name="Tajny Nabywca", nip="5260001246", country="PL", address="Sekretna 9"),
        number="INV/7",
        issue_date=date(2026, 6, 1),
        currency="GBP",
        lines=[line],
        total_net=Decimal("1000.00"),
        total_vat=Decimal("0.00"),
        total_gross=Decimal("1000.00"),
    )


def test_message_text_includes_allowlist_fields():
    text = build_reason_message(_foreign_invoice()).content
    assert REASON_PROMPT in text
    assert "GB" in text  # kraj sprzedawcy
    assert "GBP" in text  # waluta
    assert "Subskrypcja SaaS" in text  # opis pozycji (usluga vs towar)


def test_message_does_not_leak_buyer_pii():
    text = build_reason_message(_foreign_invoice()).content
    assert "Tajny Nabywca" not in text  # nazwa nabywcy
    assert "Sekretna 9" not in text  # adres nabywcy
    assert "London Str 1" not in text  # adres sprzedawcy


def test_prompt_has_injection_defense():
    assert "DANE" in REASON_PROMPT
    assert "instrukcje" in REASON_PROMPT.lower()


class _FakeStructured:
    def __init__(self, result):
        self.result = result
        self.received = None

    def invoke(self, messages):
        self.received = messages
        return self.result


class _FakeLLM:
    def __init__(self, result):
        self.structured = _FakeStructured(result)
        self.schema = None

    def with_structured_output(self, schema):
        self.schema = schema
        return self.structured


def _base() -> Classification:
    return Classification(
        treatment=TaxTreatment.IMPORT_USLUG,
        country_bucket=CountryBucket.POZA_UE,
        confidence=0.6,
        rationale_pl="deterministyczne",
    )


def test_claude_reasoner_satisfies_protocol():
    assert isinstance(ClaudeExceptionReasoner(llm=_FakeLLM(None)), ExceptionReasoner)


def test_reason_merges_judgment_with_deterministic_bucket():
    judgment = ClassificationJudgment(
        treatment=TaxTreatment.IMPORT_USLUG,
        confidence=0.85,
        rationale_pl="SaaS z UK -> import uslug.",
        human_must_confirm=["stawka 23%"],
        currency_note="GBP -> NBP",
    )
    llm = _FakeLLM(judgment)
    out = ClaudeExceptionReasoner(llm=llm).reason(_foreign_invoice(), _base())
    assert out.treatment == TaxTreatment.IMPORT_USLUG
    assert out.country_bucket == CountryBucket.POZA_UE  # zachowany z base (deterministyczny)
    assert out.confidence == 0.85
    assert out.rationale_pl == "SaaS z UK -> import uslug."
    assert llm.schema is ClassificationJudgment
    assert llm.structured.received == [build_reason_message(_foreign_invoice())]


def test_default_construction_does_not_raise():
    reasoner = ClaudeExceptionReasoner()
    assert reasoner._model == "claude-sonnet-4-6"


def _ctx():
    return [
        RetrievedChunk(
            source_id="vat-art-28b",
            article_ref="art. 28b ust. 1",
            title="VAT 28b",
            url="u",
            text="Miejscem swiadczenia uslug na rzecz podatnika jest siedziba uslugobiorcy.",
        )
    ]


def test_grounded_message_includes_context_and_citation_instruction():
    text = build_reason_message(_foreign_invoice(), context=_ctx()).content
    assert "art. 28b ust. 1" in text  # kontekst prawny wstrzykniety
    assert "Miejscem swiadczenia uslug" in text
    assert "cytuj" in text.lower()  # instrukcja cytowania


def test_no_context_message_matches_legacy_form():
    # Bez kontekstu wiadomosc jest identyczna jak wczesniej (wsteczna zgodnosc).
    assert (
        build_reason_message(_foreign_invoice()).content
        == build_reason_message(_foreign_invoice(), context=None).content
    )


def test_reason_threads_citations_through():
    judgment = ClassificationJudgment(
        treatment=TaxTreatment.IMPORT_USLUG,
        confidence=0.8,
        rationale_pl="art. 28b -> import uslug",
        human_must_confirm=[],
        currency_note="",
        citations=[
            Citation(
                source_id="vat-art-28b",
                article_ref="art. 28b ust. 1",
                quoted_span="Miejscem swiadczenia uslug",
            )
        ],
    )
    out = ClaudeExceptionReasoner(llm=_FakeLLM(judgment)).reason(
        _foreign_invoice(), _base(), _ctx()
    )
    assert out.citations[0].article_ref == "art. 28b ust. 1"
    assert out.country_bucket == CountryBucket.POZA_UE  # zachowany deterministyczny bucket
