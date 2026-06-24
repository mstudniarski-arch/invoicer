"""Serwer Planu B: webhook Twilio (odpowiedz TAK/NIE) wznawia graf i ksieguje.

Uruchamia FastAPI z POST /whatsapp/inbound na tym SAMYM trwalym grafie i rejestrze
(wspolny plik SQLite co scripts/whatsapp_approval.py). Odpowiedz WhatsApp:
  TAK -> resume(approve) -> wezel `book` (sink)      NIE -> resume(reject)

Sink (co robi "TAK"):
  domyslnie  MockSubiektSink  — ksiegowanie pozorne (bezpieczne do testow),
  INVOICER_SINK=fakturownia    — REALNA faktura w Fakturowni (wymaga FAKTUROWNIA_*).

Uzycie:
    set -a; source .env; set +a
    PYTHONPATH=src uv run python scripts/whatsapp_webhook.py        # mock (domyslnie)
    INVOICER_SINK=fakturownia PYTHONPATH=src uv run python scripts/whatsapp_webhook.py

Potem wystaw publiczny URL i wpisz go w Twilio:
    ngrok http 8000
    Twilio Console -> Messaging -> Sandbox -> "When a message comes in":
        https://<twoj-subdomena>.ngrok-free.app/whatsapp/inbound   (metoda POST)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn

from invoicer.adapters.claude_extractor import ClaudeVisionExtractor
from invoicer.adapters.claude_reasoner import ClaudeExceptionReasoner
from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.approvals import PendingApprovals
from invoicer.graph.build import build_invoice_graph
from invoicer.ledger import Ledger
from invoicer.runner import persistent_checkpointer
from invoicer.security import install_redaction
from invoicer.webhook import create_inbound_app

_DB = "invoicer_state.sqlite"  # ten sam plik co scripts/whatsapp_approval.py


def _sink():
    if os.getenv("INVOICER_SINK", "").lower() == "fakturownia":
        from invoicer.adapters.fakturownia import build_fakturownia_sink

        print("SINK: Fakturownia (REALNE ksiegowanie po 'TAK')")
        return build_fakturownia_sink()
    print("SINK: MockSubiekt (ksiegowanie pozorne — bezpieczne)")
    return MockSubiektSink()


def main() -> None:
    install_redaction(logging.getLogger("invoicer"))  # logi invoicer.* z redakcja PII
    # extractor/reasoner sa leniwe i NIE sa wolane przy resume (po human_review leci tylko `book`),
    # wiec klucz ANTHROPIC_API_KEY nie jest tu potrzebny — graf musi tylko miec te sama strukture.
    graph = build_invoice_graph(
        extractor=ClaudeVisionExtractor(),
        reasoner=ClaudeExceptionReasoner(),
        ledger=Ledger(Path("ledger.jsonl")),
        sink=_sink(),
        checkpointer=persistent_checkpointer(_DB),
    )
    registry = PendingApprovals(_DB)
    app = create_inbound_app(graph, registry)

    port = int(os.getenv("PORT", "8000"))
    print(f"Webhook: POST /whatsapp/inbound na :{port} — wystaw publicznie (ngrok http {port}).")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
