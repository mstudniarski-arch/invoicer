from datetime import date, datetime
from decimal import Decimal

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.adapters.stub_extractor import StubExtractor
from invoicer.graph.build import build_invoice_graph
from invoicer.ledger import Ledger
from invoicer.models import (
    Check,
    CheckStatus,
    Citation,
    Classification,
    CountryBucket,
    GroundingStatus,
    Invoice,
    InvoiceDocument,
    LineItem,
    Party,
    TaxTreatment,
    ValidationResult,
)
from invoicer.rag.models import RetrievedChunk
from invoicer.runner import (
    _CHECKPOINT_ALLOWED_TYPES,
    build_demo_graph,
    document_from_upload,
    resume_document,
    start_document,
)


def _invoice() -> Invoice:
    line = LineItem(
        description="Usluga",
        quantity=Decimal("1"),
        unit_net=Decimal("1000.00"),
        vat_rate=Decimal("0.23"),
        net=Decimal("1000.00"),
        vat=Decimal("230.00"),
        gross=Decimal("1230.00"),
    )
    return Invoice(
        seller=Party(name="ACME", nip="5260001246", country="PL"),
        buyer=Party(name="Klient", country="PL"),
        number="FV/1",
        issue_date=date(2026, 6, 1),
        currency="PLN",
        lines=[line],
        total_net=Decimal("1000.00"),
        total_vat=Decimal("230.00"),
        total_gross=Decimal("1230.00"),
        extraction_confidence=0.95,
    )


def _doc() -> InvoiceDocument:
    return InvoiceDocument(
        sender="a@b.pl", received_at=datetime(2026, 6, 1), filename="x.pdf", content=b"%PDF"
    )


def _graph(tmp_path):
    return build_invoice_graph(
        extractor=StubExtractor(_invoice()),
        ledger=Ledger(tmp_path / "l.jsonl"),
        sink=MockSubiektSink(),
        clock=lambda: "2026-06-01T10:00:00",
    )


def test_start_document_returns_human_review_payload(tmp_path):
    payload = start_document(_graph(tmp_path), _doc(), thread_id="t1")
    assert payload["number"] == "FV/1"
    assert "treatment" in payload


def test_resume_document_approve_books(tmp_path):
    graph = _graph(tmp_path)
    start_document(graph, _doc(), thread_id="t2")
    final = resume_document(graph, thread_id="t2", decision="approve")
    assert final["booking"].booking_id == "MOCK-FV/1"


def test_resume_document_reject_does_not_book(tmp_path):
    graph = _graph(tmp_path)
    start_document(graph, _doc(), thread_id="t3")
    final = resume_document(graph, thread_id="t3", decision="reject")
    assert final.get("booking") is None


def test_document_from_upload_wraps_bytes():
    doc = document_from_upload("faktura.pdf", b"%PDF-1.4 x")
    assert doc.filename == "faktura.pdf"
    assert doc.content == b"%PDF-1.4 x"
    assert doc.sender  # niepuste (domyslny nadawca demo)


def test_build_demo_graph_returns_runnable_graph(tmp_path):
    graph = build_demo_graph(ledger_path=tmp_path / "demo.jsonl")
    assert hasattr(graph, "invoke")  # skompilowany graf LangGraph


class _FakeSource:
    def __init__(self, docs):
        self._docs = docs

    def fetch(self, sender):
        return self._docs


class _PredicateDetector:
    def __init__(self, predicate):
        self._predicate = predicate

    def is_invoice(self, document):
        return self._predicate(document)


def _pdf_doc(filename: str) -> InvoiceDocument:
    return InvoiceDocument(
        sender="a@b.pl", received_at=datetime(2026, 6, 23), filename=filename, content=b"%PDF"
    )


def test_fetch_invoice_documents_keeps_only_invoices():
    from invoicer.runner import fetch_invoice_documents

    d1, d2 = _pdf_doc("faktura.pdf"), _pdf_doc("cv.pdf")
    source = _FakeSource([d1, d2])
    detector = _PredicateDetector(lambda d: d.filename == "faktura.pdf")
    assert fetch_invoice_documents(source, detector, "a@b.pl") == [d1]


def test_fetch_invoice_documents_empty_when_none_are_invoices():
    from invoicer.runner import fetch_invoice_documents

    source = _FakeSource([_pdf_doc("cv.pdf")])
    detector = _PredicateDetector(lambda _d: False)
    assert fetch_invoice_documents(source, detector, "a@b.pl") == []


def test_persistent_checkpointer_resumes_across_graph_instances(tmp_path):
    from invoicer.runner import persistent_checkpointer

    db = str(tmp_path / "cp.sqlite")
    ledger_path = tmp_path / "l.jsonl"

    def _make_graph():
        return build_invoice_graph(
            extractor=StubExtractor(_invoice()),
            ledger=Ledger(ledger_path),
            sink=MockSubiektSink(),
            clock=lambda: "2026-06-01T10:00:00",
            checkpointer=persistent_checkpointer(db),
        )

    start_document(_make_graph(), _doc(), thread_id="p1")  # pauza, stan w SQLite
    final = resume_document(_make_graph(), thread_id="p1", decision="approve")  # nowa instancja
    assert final["booking"].booking_id == "MOCK-FV/1"


def test_human_review_payload_includes_seller_nip(tmp_path):
    payload = start_document(_graph(tmp_path), _doc(), thread_id="nip1")
    assert payload["seller_nip"] == "5260001246"


def test_request_invoice_approval_registers_and_sends(tmp_path):
    from invoicer.adapters.stub_approval import StubApprovalChannel
    from invoicer.approvals import PendingApprovals
    from invoicer.runner import request_invoice_approval

    channel = StubApprovalChannel()
    registry = PendingApprovals(str(tmp_path / "p.sqlite"))
    payload = request_invoice_approval(
        _graph(tmp_path), channel, registry, _doc(), thread_id="w1", phone="whatsapp:+48500"
    )
    assert payload["number"] == "FV/1"
    assert channel.sent == [payload]  # request wyslany z payloadem (sprzedawca/NIP/kwota)
    assert registry.resolve_oldest("whatsapp:+48500") == "w1"  # zarejestrowany pending


def _state_with_all_custom_types() -> dict:
    return {
        "document": _doc(),
        "invoice": _invoice(),
        "validation": ValidationResult(
            checks=[Check(name="nip", status=CheckStatus.PASS)],
            is_duplicate=False,
        ),
        "classification": Classification(
            treatment=TaxTreatment.KRAJOWA,
            country_bucket=CountryBucket.PL,
            rationale_pl="x",
        ),
    }


def test_strict_serializer_without_allowlist_loses_custom_types(tmp_path):
    """Sanity (baseline): strict serializer bez allowlist gubi typy — wraca raw dict.

    Demonstruje problem, ktory naprawia persistent_checkpointer (LANGGRAPH_STRICT_MSGPACK=true
    w przyszlej wersji LangGraph zablokuje nieuznane typy domyslnie).
    """
    strict = JsonPlusSerializer(allowed_msgpack_modules=None)
    state = _state_with_all_custom_types()
    type_, blob = strict.dumps_typed(state)
    loaded = strict.loads_typed((type_, blob))
    # Bez allowlist deserializer zwraca raw dict, nie InvoiceDocument/Invoice/...
    assert not isinstance(loaded["invoice"], Invoice)
    assert not isinstance(loaded["document"], InvoiceDocument)


def test_persistent_checkpointer_registers_invoicer_models_in_allowlist(tmp_path):
    """Fix konfiguracji: persistent_checkpointer JAWNIE rejestruje typy invoicer.models.

    Przed fixem default sentinel -> _allowed_msgpack_modules == True (warn-but-allow);
    po fixie -> set zawierajacy kluczowe typy. Odporne na LANGGRAPH_STRICT_MSGPACK=true.
    """
    from invoicer.runner import persistent_checkpointer

    saver = persistent_checkpointer(str(tmp_path / "cp.sqlite"))
    allowed = saver.serde._allowed_msgpack_modules
    # Nie sentinel default -> nasz fix ustawił JAWNĄ liste
    assert isinstance(allowed, set), (
        f"oczekiwano set z allowlist, jest {type(allowed).__name__} ({allowed!r})"
    )
    expected = {
        ("invoicer.models", "InvoiceDocument"),
        ("invoicer.models", "Invoice"),
        ("invoicer.models", "ValidationResult"),
        ("invoicer.models", "Classification"),
        ("invoicer.models", "CheckStatus"),
        ("invoicer.models", "TaxTreatment"),
        ("invoicer.models", "CountryBucket"),
    }
    missing = expected - allowed
    assert not missing, f"brak w allowlist: {missing}"


def test_persistent_checkpointer_round_trip_preserves_custom_types(tmp_path):
    """Behavioral sanity: serde z fixem dalej poprawnie round-trip’uje state."""
    from invoicer.runner import persistent_checkpointer

    saver = persistent_checkpointer(str(tmp_path / "cp.sqlite"))
    state = _state_with_all_custom_types()
    type_, blob = saver.serde.dumps_typed(state)
    loaded = saver.serde.loads_typed((type_, blob))
    assert isinstance(loaded["document"], InvoiceDocument)
    assert isinstance(loaded["invoice"], Invoice)
    assert isinstance(loaded["validation"], ValidationResult)
    assert isinstance(loaded["classification"], Classification)
    assert loaded["invoice"].number == "FV/1"
    assert loaded["validation"].is_duplicate is False


def test_rag_types_are_in_checkpoint_allowlist():
    assert RetrievedChunk in _CHECKPOINT_ALLOWED_TYPES
    assert Citation in _CHECKPOINT_ALLOWED_TYPES
    assert GroundingStatus in _CHECKPOINT_ALLOWED_TYPES


def test_checkpoint_serde_roundtrips_rag_state():
    serde = JsonPlusSerializer(allowed_msgpack_modules=_CHECKPOINT_ALLOWED_TYPES)
    chunk = RetrievedChunk(source_id="s", article_ref="a", title="t", url="u", text="x", score=0.9)
    classification = Classification(
        treatment=TaxTreatment.IMPORT_USLUG,
        country_bucket=CountryBucket.POZA_UE,
        citations=[Citation(source_id="s", article_ref="a", quoted_span="x")],
        grounding_status=GroundingStatus.UNSUPPORTED,
    )
    state = {"legal_context": [chunk], "classification": classification}
    restored = serde.loads_typed(serde.dumps_typed(state))
    assert restored["legal_context"][0] == chunk
    assert isinstance(restored["legal_context"][0], RetrievedChunk)
    assert restored["classification"].citations[0].quoted_span == "x"
    assert restored["classification"].grounding_status == GroundingStatus.UNSUPPORTED
