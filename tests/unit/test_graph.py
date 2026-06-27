from datetime import date, datetime
from decimal import Decimal

from langgraph.types import Command

from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.adapters.stub_extractor import StubExtractor
from invoicer.adapters.stub_reasoner import StubExceptionReasoner
from invoicer.graph.build import build_invoice_graph
from invoicer.ledger import Ledger, LedgerEntry
from invoicer.models import (
    Classification,
    CountryBucket,
    Invoice,
    InvoiceDocument,
    LineItem,
    Party,
    TaxTreatment,
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


def _graph(ledger):
    return build_invoice_graph(
        extractor=StubExtractor(_invoice()),
        ledger=ledger,
        sink=MockSubiektSink(),
        clock=lambda: "2026-06-01T10:00:00",
    )


def test_graph_pauses_at_human_review_then_books_on_approve(tmp_path):
    graph = _graph(Ledger(tmp_path / "l.jsonl"))
    config = {"configurable": {"thread_id": "t1"}}
    paused = graph.invoke({"document": _doc(), "errors": []}, config)
    # Graf zatrzymal sie na human_review -> jeszcze nie zaksiegowano.
    assert paused.get("booking") is None
    final = graph.invoke(Command(resume="approve"), config)
    assert final["booking"].booking_id == "MOCK-FV/1"


def test_graph_skips_human_review_for_already_booked_duplicate(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    inv = _invoice()
    # faktura juz zaksiegowana wczesniej (wpis w ledger)
    ledger.append(
        LedgerEntry(
            number=inv.number,
            seller_nip=inv.seller.nip,
            seller_name=inv.seller.name,
            total_gross=str(inv.total_gross),
            booking_id="PRZED",
            booked_at="2026-06-01T09:00:00",
        )
    )
    graph = _graph(ledger)
    config = {"configurable": {"thread_id": "dup1"}}
    result = graph.invoke({"document": _doc(), "errors": []}, config)
    # duplikat: graf NIE zatrzymuje sie na bramce (brak interrupt) i NIE ksieguje
    assert result.get("__interrupt__") is None
    assert result.get("booking") is None
    assert result["validation"].is_duplicate is True


def test_graph_does_not_book_on_reject(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    graph = _graph(ledger)
    config = {"configurable": {"thread_id": "t2"}}
    graph.invoke({"document": _doc(), "errors": []}, config)
    final = graph.invoke(Command(resume="reject"), config)
    assert final.get("booking") is None
    assert ledger.entries() == []


def _foreign_invoice() -> Invoice:
    inv = _invoice()
    inv.seller = Party(name="Foreign Ltd", country="GB", vat_id="GB1")
    inv.total_vat = Decimal("0.00")
    inv.total_gross = Decimal("1000.00")
    inv.currency = "GBP"
    inv.lines[0].vat = Decimal("0.00")
    inv.lines[0].vat_rate = Decimal("0.00")
    inv.lines[0].gross = Decimal("1000.00")
    return inv


def test_foreign_invoice_runs_through_reason_exception(tmp_path):
    enriched = Classification(
        treatment=TaxTreatment.IMPORT_TOWAROW,
        country_bucket=CountryBucket.POZA_UE,
        confidence=0.9,
        rationale_pl="towar wg sedziego",
    )
    from invoicer.adapters.fake_embedder import DeterministicEmbedder
    from invoicer.adapters.in_memory_legal_store import InMemoryLegalStore
    from invoicer.rag.corpus import Chunk
    from invoicer.rag.query import build_retrieval_query

    inv = _foreign_invoice()
    chunk = Chunk(
        source_id="s",
        article_ref="art. 28b",
        title="t",
        url="u",
        kind="ustawa",
        text=build_retrieval_query(inv),
    )
    store = InMemoryLegalStore.from_chunks([chunk], DeterministicEmbedder(dim=64))
    graph = build_invoice_graph(
        extractor=StubExtractor(inv),
        ledger=Ledger(tmp_path / "l.jsonl"),
        sink=MockSubiektSink(),
        reasoner=StubExceptionReasoner(enriched),
        store=store,
        clock=lambda: "2026-06-01T10:00:00",
    )
    config = {"configurable": {"thread_id": "f1"}}
    paused = graph.invoke({"document": _doc(), "errors": []}, config)
    # retrieval -> context -> reason_exception (sedzia wzbogaca); verify_grounding nie zmienia
    # treatment
    assert paused["classification"].treatment == TaxTreatment.IMPORT_TOWAROW
    assert paused["classification"].rationale_pl == "towar wg sedziego"
    final = graph.invoke(Command(resume="approve"), config)
    assert final["booking"].booking_id == "MOCK-FV/1"


def test_foreign_invoice_grounded_when_citation_supported(tmp_path):
    from invoicer.adapters.fake_embedder import DeterministicEmbedder
    from invoicer.adapters.in_memory_legal_store import InMemoryLegalStore
    from invoicer.models import Citation, GroundingStatus
    from invoicer.rag.corpus import Chunk
    from invoicer.rag.query import build_retrieval_query

    inv = _foreign_invoice()
    chunk_text = build_retrieval_query(inv)  # zawiera m.in. "Kraj sprzedawcy: GB"
    chunk = Chunk(
        source_id="vat-art-28b",
        article_ref="art. 28b ust. 1",
        title="t",
        url="u",
        kind="ustawa",
        text=chunk_text,
    )
    store = InMemoryLegalStore.from_chunks([chunk], DeterministicEmbedder(dim=64))
    grounded = Classification(
        treatment=TaxTreatment.IMPORT_USLUG,
        country_bucket=CountryBucket.POZA_UE,
        confidence=0.8,
        rationale_pl="art. 28b -> miejsce swiadczenia w PL",
        citations=[
            Citation(
                source_id="vat-art-28b",
                article_ref="art. 28b ust. 1",
                quoted_span="Kraj sprzedawcy: GB",
            )
        ],
    )
    graph = build_invoice_graph(
        extractor=StubExtractor(inv),
        ledger=Ledger(tmp_path / "l.jsonl"),
        sink=MockSubiektSink(),
        reasoner=StubExceptionReasoner(grounded),
        store=store,
        clock=lambda: "2026-06-01T10:00:00",
    )
    config = {"configurable": {"thread_id": "grd"}}
    paused = graph.invoke({"document": _doc(), "errors": []}, config)
    assert paused["classification"].grounding_status == GroundingStatus.GROUNDED
    assert paused["classification"].confidence == 0.8  # cytat poparty -> brak capa
    # grounding_status widoczny dla czlowieka w payloadzie interrupt
    assert paused["__interrupt__"][0].value["grounding_status"] == "grounded"
    final = graph.invoke(Command(resume="approve"), config)
    assert final["booking"].booking_id == "MOCK-FV/1"


def test_pl_invoice_skips_reason_exception(tmp_path):
    # Sedzia, ktory by "zepsul" klasyfikacje, NIE powinien byc wolany dla PL.
    poison = Classification(
        treatment=TaxTreatment.INNE, country_bucket=CountryBucket.PL, confidence=0.1
    )
    graph = build_invoice_graph(
        extractor=StubExtractor(_invoice()),
        ledger=Ledger(tmp_path / "l.jsonl"),
        sink=MockSubiektSink(),
        reasoner=StubExceptionReasoner(poison),
        clock=lambda: "2026-06-01T10:00:00",
    )
    config = {"configurable": {"thread_id": "p1"}}
    paused = graph.invoke({"document": _doc(), "errors": []}, config)
    assert paused["classification"].treatment == TaxTreatment.KRAJOWA  # sedzia NIE wolany
