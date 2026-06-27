"""Offline demo: legal-grounded corrective RAG (BEZ kluczy / bez DB).

Ta sama faktura zagraniczna (UK SaaS, bez VAT), trzy stany groundingu w wezle
reason_exception + verify_grounding:
  1) brak prawa w bazie    -> ABSTENTION (weak, cap pewnosci, flaga do czlowieka)
  2) cytat poparty zrodlem -> GROUNDED   (cytat z art., pewnosc zachowana)
  3) cytat zmyslony        -> UNSUPPORTED (faithfulness lapie, cap pewnosci, flaga)

Uruchom: PYTHONPATH=src uv run python scripts/rag_demo.py
"""

from __future__ import annotations

import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from invoicer.adapters.fake_embedder import DeterministicEmbedder
from invoicer.adapters.in_memory_legal_store import InMemoryLegalStore
from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.adapters.stub_extractor import StubExtractor
from invoicer.adapters.stub_reasoner import StubExceptionReasoner
from invoicer.graph.build import build_invoice_graph
from invoicer.ledger import Ledger
from invoicer.models import (
    Citation,
    Classification,
    CountryBucket,
    Invoice,
    InvoiceDocument,
    LineItem,
    Party,
    TaxTreatment,
)
from invoicer.rag.corpus import Chunk
from invoicer.rag.query import build_retrieval_query
from invoicer.runner import start_document


def _foreign_invoice() -> Invoice:
    line = LineItem(
        description="Subskrypcja SaaS (UK)",
        quantity=Decimal("1"),
        unit_net=Decimal("1000.00"),
        vat_rate=Decimal("0.00"),
        net=Decimal("1000.00"),
        vat=Decimal("0.00"),
        gross=Decimal("1000.00"),
    )
    return Invoice(
        seller=Party(name="Foreign Ltd", country="GB", vat_id="GB1"),
        buyer=Party(name="Klient", nip="5260001246", country="PL"),
        number="INV/UK/1",
        issue_date=date(2026, 1, 1),
        currency="GBP",
        lines=[line],
        total_net=Decimal("1000.00"),
        total_vat=Decimal("0.00"),
        total_gross=Decimal("1000.00"),
        extraction_confidence=0.95,
    )


def _doc() -> InvoiceDocument:
    return InvoiceDocument(
        sender="demo", received_at=datetime(2026, 1, 1), filename="uk.pdf", content=b"%PDF"
    )


def _run(label: str, *, store, reasoner, tmp: str) -> None:
    graph = build_invoice_graph(
        extractor=StubExtractor(_foreign_invoice()),
        ledger=Ledger(Path(tmp) / f"{label}.jsonl"),
        sink=MockSubiektSink(),
        store=store,
        reasoner=reasoner,
        clock=lambda: "2026-01-01T10:00:00",
    )
    payload = start_document(graph, _doc(), thread_id=label)
    print(f"\n=== {label} ===")
    print(f"  treatment        : {payload['treatment']}")
    print(f"  grounding_status : {payload['grounding_status']}")
    print(f"  citations        : {payload['citations']}")
    print(f"  must_confirm     : {payload['must_confirm']}")


def main() -> None:
    tmp = tempfile.mkdtemp()
    inv = _foreign_invoice()
    chunk = Chunk(
        source_id="vat-art-28b",
        article_ref="art. 28b ust. 1",
        title="VAT 28b",
        url="https://isap.sejm.gov.pl/",
        kind="ustawa",
        text=build_retrieval_query(inv),
    )
    seeded = InMemoryLegalStore.from_chunks([chunk], DeterministicEmbedder(dim=64))
    empty = InMemoryLegalStore(DeterministicEmbedder(dim=64))

    grounded = Classification(
        treatment=TaxTreatment.IMPORT_USLUG,
        country_bucket=CountryBucket.POZA_UE,
        confidence=0.85,
        rationale_pl="art. 28b -> miejsce swiadczenia w PL",
        citations=[
            Citation(
                source_id="vat-art-28b",
                article_ref="art. 28b ust. 1",
                quoted_span="Kraj sprzedawcy: GB",
            )
        ],
    )
    fabricated = grounded.model_copy(
        update={
            "citations": [
                Citation(
                    source_id="vat-art-28b",
                    article_ref="art. 28b ust. 1",
                    quoted_span="zdanie ktorego nie ma w zrodle",
                )
            ]
        }
    )

    print("Faktura zagraniczna (UK SaaS, bez VAT). Te same dane, 3 stany groundingu:")
    _run("1-ABSTENTION-brak-prawa", store=empty, reasoner=StubExceptionReasoner(grounded), tmp=tmp)
    _run(
        "2-GROUNDED-cytat-poparty",
        store=seeded,
        reasoner=StubExceptionReasoner(grounded),
        tmp=tmp,
    )
    _run(
        "3-UNSUPPORTED-zmyslony",
        store=seeded,
        reasoner=StubExceptionReasoner(fabricated),
        tmp=tmp,
    )
    print("\n(Wszystkie 3 sciezki koncza sie na bramce czlowieka - nic nie ksieguje sie auto.)")


if __name__ == "__main__":
    main()
