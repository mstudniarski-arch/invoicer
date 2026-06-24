"""Caly workflow -> request akceptacji na WhatsApp (Plan A: wychodzace).

Pobiera dzisiejsze faktury z Gmaila (albo bierze plik PDF podany w argv),
przepuszcza przez graf do bramki human_review i wysyla request akceptacji
na WhatsApp przez Twilio. ZATRZYMUJE sie na bramce — NIC nie ksieguje.
(Zatwierdzenie odpowiedzia TAK/NIE domyka webhook /whatsapp/inbound, Plan B.)

Uzycie (invoicer jest pod src/, wiec PYTHONPATH=src):
    set -a; source .env; set +a          # zaladuj env (repo nie uzywa python-dotenv)
    PYTHONPATH=src uv run python scripts/whatsapp_approval.py        # tryb Gmail (dzis)
    PYTHONPATH=src uv run python scripts/whatsapp_approval.py fv.pdf  # tryb pliku (test wiringu)

Wymaga env: ANTHROPIC_API_KEY, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
TWILIO_WHATSAPP_FROM, APPROVER_WHATSAPP_TO; w trybie Gmail dodatkowo
token.json (po authorize_gmail) + GMAIL_SENDER_FILTER.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from invoicer.adapters.claude_extractor import ClaudeVisionExtractor
from invoicer.adapters.claude_reasoner import ClaudeExceptionReasoner
from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.adapters.twilio_whatsapp import build_twilio_whatsapp_channel
from invoicer.approvals import PendingApprovals
from invoicer.graph.build import build_invoice_graph
from invoicer.ledger import Ledger
from invoicer.models import InvoiceDocument
from invoicer.runner import (
    document_from_upload,
    fetch_invoice_documents,
    persistent_checkpointer,
    request_invoice_approval,
)

_DB = "invoicer_state.sqlite"  # wspolny plik: checkpointer grafu + rejestr pending


def _documents() -> list[InvoiceDocument]:
    # 1) plik podany w argv -> uzyj go (szybki test wiringu, bez czekania na maila)
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        return [document_from_upload(path.name, path.read_bytes())]

    # 2) inaczej: pobierz dzisiejsze .pdf z Gmaila i zostaw tylko wykryte jako faktura
    from invoicer.adapters.claude_detector import ClaudeInvoiceDetector
    from invoicer.adapters.gmail import GmailAdapter
    from invoicer.adapters.gmail_auth import gmail_service_from_token

    service = gmail_service_from_token(Path(os.getenv("GMAIL_TOKEN", "token.json")))
    source = GmailAdapter(service)
    sender = os.environ["GMAIL_SENDER_FILTER"]
    return fetch_invoice_documents(source, ClaudeInvoiceDetector(), sender)


def main() -> None:
    graph = build_invoice_graph(
        extractor=ClaudeVisionExtractor(),
        reasoner=ClaudeExceptionReasoner(),
        ledger=Ledger(Path("ledger.jsonl")),
        sink=MockSubiektSink(),  # nieosiagalny: stop na bramce = brak ksiegowania
        checkpointer=persistent_checkpointer(_DB),
    )
    channel = build_twilio_whatsapp_channel()
    registry = PendingApprovals(_DB)
    phone = os.environ["APPROVER_WHATSAPP_TO"]

    docs = _documents()
    if not docs:
        print("Brak faktur do akceptacji (dzis nic nie znaleziono / nie wykryto faktury).")
        return

    for doc in docs:
        thread_id = f"wa-{uuid.uuid4()}"
        payload = request_invoice_approval(
            graph, channel, registry, doc, thread_id=thread_id, phone=phone
        )
        if payload is None:
            print(f"[{doc.filename}] graf nie zatrzymal sie na bramce (brak interrupt) — pomijam.")
            continue
        print(
            f"[{doc.filename}] wyslano na WhatsApp · thread={thread_id} · "
            f"{payload['seller']} / NIP {payload.get('seller_nip') or '—'} / "
            f"{payload['total_gross']} {payload['currency']}"
        )

    print("\nStop na bramce — nic nie zaksiegowano. Odpowiedz TAK/NIE wznawia graf (Plan B).")


if __name__ == "__main__":
    main()
