from datetime import date, datetime
from decimal import Decimal

from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.adapters.stub_extractor import StubExtractor
from invoicer.graph.build import build_invoice_graph
from invoicer.ledger import Ledger
from invoicer.models import Invoice, InvoiceDocument, LineItem, Party
from invoicer.runner import build_demo_graph, document_from_upload, resume_document, start_document


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
