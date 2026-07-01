"""Narrated, OFFLINE przebieg calego flow — do nauki "jak to dziala od srodka".

Buduje graf na FAKE'ach (StubExtractor + MockSubiekt, bez Gmaila/Claude/Twilio/API key),
przepuszcza jeden dokument i DRUKUJE stan po kazdym wezle, pokazujac dwie fazy:
  faza 1: extract -> validate -> classify -> (galaz zagr.) -> human_review = PAUZA (interrupt)
  faza 2: Command(resume="approve") -> book -> END   (tu odpala sie mark_read)

Uzycie (uruchamialny bezposrednio dzieki bootstrapowi sys.path):
    uv run scripts/debug_flow.py            # faktura PL (prosta sciezka)
    uv run scripts/debug_flow.py --foreign  # faktura zagraniczna (galaz RAG/sedzia)

Aby PRZEJSC LINIJKA PO LINIJCE: postaw breakpoint w wezle (np. src/invoicer/graph/nodes.py
w `book`) i odpal ten plik pod debuggerem PyCharm/pdb. Ten skrypt to MAPA przebiegu;
debugger to spacer krok po kroku.
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

# Wycisz halas checkpointera LangGraph (ostrzezenia "Deserializing unregistered type"),
# zeby narracja flow byla czytelna — to tylko info checkpointera, nieistotne dla nauki.
logging.getLogger("langgraph.checkpoint.serde.jsonplus").setLevel(logging.ERROR)

# Uruchamianie jako skrypt: projekt nie jest instalowany jako pakiet (pytest uzywa
# pythonpath=src) — dokladamy `src` recznie, zeby `import invoicer` zadzialal.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from langgraph.types import Command  # noqa: E402

from invoicer.adapters.fake_embedder import DeterministicEmbedder  # noqa: E402
from invoicer.adapters.in_memory_legal_store import InMemoryLegalStore  # noqa: E402
from invoicer.adapters.mock_subiekt import MockSubiektSink  # noqa: E402
from invoicer.adapters.stub_extractor import StubExtractor  # noqa: E402
from invoicer.adapters.stub_reasoner import StubExceptionReasoner  # noqa: E402
from invoicer.graph.build import build_invoice_graph  # noqa: E402
from invoicer.ledger import Ledger  # noqa: E402
from invoicer.models import (  # noqa: E402
    Classification,
    CountryBucket,
    Invoice,
    InvoiceDocument,
    LineItem,
    Party,
    TaxTreatment,
)
from invoicer.rag.corpus import Chunk  # noqa: E402
from invoicer.rag.query import build_retrieval_query  # noqa: E402


def _pl_invoice() -> Invoice:
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
        buyer=Party(name="Moja Firma sp. z o.o.", nip="1234567890", country="PL"),
        number="FV/DEBUG/1",
        issue_date=date(2026, 6, 1),
        currency="PLN",
        lines=[line],
        total_net=Decimal("1000.00"),
        total_vat=Decimal("230.00"),
        total_gross=Decimal("1230.00"),
        extraction_confidence=0.95,
    )


def _foreign_invoice() -> Invoice:
    inv = _pl_invoice()
    inv.seller = Party(name="Foreign Ltd", country="GB", vat_id="GB123456789")
    inv.number = "INV/DEBUG/2"
    inv.currency = "GBP"
    inv.total_vat = Decimal("0.00")
    inv.total_gross = Decimal("1000.00")
    inv.lines[0].vat = Decimal("0.00")
    inv.lines[0].vat_rate = Decimal("0.00")
    inv.lines[0].gross = Decimal("1000.00")
    return inv


def _doc() -> InvoiceDocument:
    # message_id ustawiony, zeby w `book` odpalil sie mark_read (jak ze zrodla Gmail).
    return InvoiceDocument(
        sender="ksiegowa@dostawca.pl",
        received_at=datetime(2026, 6, 1),
        filename="faktura.pdf",
        content=b"%PDF-1.4 fake",
        message_id="MSG-DEBUG-1",
    )


def _summarize(update: dict) -> str:
    """Czytelne podsumowanie czastkowej aktualizacji stanu zwroconej przez wezel."""
    bits = [f"keys={list(update.keys())}"]
    inv = update.get("invoice")
    if inv is not None:
        bits.append(f"invoice={inv.number} seller={inv.seller.country}")
    val = update.get("validation")
    if val is not None:
        flags = [f"{c.name}:{c.status.value}" for c in val.checks]
        bits.append(f"validation ok={val.ok} dup={val.is_duplicate} checks={flags}")
    cls = update.get("classification")
    if cls is not None:
        bits.append(
            f"classification={cls.treatment.value}/{cls.country_bucket.value} "
            f"conf={cls.confidence} grounding={cls.grounding_status.value}"
        )
    book = update.get("booking")
    if book is not None:
        bits.append(f"booking_id={book.booking_id} sink={book.sink}")
    return "  ".join(bits)


def _stream(graph, payload, config, title: str) -> None:
    print(f"\n===== {title} =====")
    for step in graph.stream(payload, config, stream_mode="updates"):
        for node, update in step.items():
            if node == "__interrupt__":
                print("  ⏸  INTERRUPT (graf czeka na decyzje czlowieka)")
                continue
            print(f"  ▶ [{node}] {_summarize(update or {})}")


def main() -> None:
    foreign = "--foreign" in sys.argv[1:]
    invoice = _foreign_invoice() if foreign else _pl_invoice()

    store = None
    reasoner = None
    if foreign:
        # Zasil RAG, zeby galaz zagraniczna miala kontekst; "sedzia" wzbogaca klasyfikacje.
        chunk = Chunk(
            source_id="vat-art-28b",
            article_ref="art. 28b ust. 1",
            title="Miejsce swiadczenia uslug",
            url="ustawa://vat/28b",
            kind="ustawa",
            text=build_retrieval_query(invoice),
        )
        store = InMemoryLegalStore.from_chunks([chunk], DeterministicEmbedder(dim=64))
        reasoner = StubExceptionReasoner(
            Classification(
                treatment=TaxTreatment.IMPORT_USLUG,
                country_bucket=CountryBucket.POZA_UE,
                confidence=0.8,
                rationale_pl="art. 28b -> miejsce swiadczenia w PL (import uslug)",
            )
        )

    ledger = Ledger(Path("/tmp/debug_flow_ledger.jsonl"))
    Path("/tmp/debug_flow_ledger.jsonl").unlink(missing_ok=True)

    graph = build_invoice_graph(
        extractor=StubExtractor(invoice),
        reasoner=reasoner,
        store=store,
        ledger=ledger,
        sink=MockSubiektSink(),
        clock=lambda: "2026-06-01T10:00:00",
        # mark_read podpiety na print, zeby bylo widac moment oznaczania maila (faza 2, w `book`).
        mark_read=lambda mid: print(f"  ✉  mark_read: usuwam UNREAD z message_id={mid}"),
    )

    print(f"GRAF ({'ZAGRANICZNA' if foreign else 'PL'} faktura {invoice.number}):")
    try:  # ASCII jest ladniejsze, ale wymaga grandalf; mermaid to czysty tekst bez zaleznosci
        print(graph.get_graph().draw_ascii())
    except ImportError:
        print(graph.get_graph().draw_mermaid())

    config = {"configurable": {"thread_id": "debug-flow-1"}}
    _stream(graph, {"document": _doc(), "errors": []}, config, "FAZA 1: extract -> human_review")

    snap = graph.get_state(config)
    print(f"\n  PAUZA. Nastepny wezel do wykonania: {snap.next}")
    if snap.tasks and snap.tasks[0].interrupts:
        payload = snap.tasks[0].interrupts[0].value
        print(f"  Payload interrupt (to widzi zatwierdzajacy): {payload}")

    _stream(graph, Command(resume="approve"), config, "FAZA 2: resume('approve') -> book -> END")

    final = graph.get_state(config).values
    booking = final.get("booking")
    print(f"\nKONIEC. booking_id = {booking.booking_id if booking else None}")
    print(f"Wpisy w ledger: {len(ledger.entries())}")


if __name__ == "__main__":
    main()
