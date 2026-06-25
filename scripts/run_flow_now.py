"""Caly flow NA ZADANIE (jeden proces): zaciag/PDF -> ekstrakcja -> bramka ->
WhatsApp -> poll Twilio o Twoja odpowiedz TAK/NIE -> wznowienie -> ksiegowanie.

Samodzielny: NIE wymaga wdrozonej apki ani webhooka (odpytuje Twilio REST).
Ksieguje WYLACZNIE po Twoim 'TAK'; 'NIE'/timeout = nic nie ksieguje.
INVOICER_SINK=fakturownia => realna faktura po TAK (inaczej MockSubiekt).

Uzycie (invoicer jest pod src/):
    set -a; source .env; set +a
    PYTHONPATH=src uv run python scripts/run_flow_now.py            # tryb Gmail (dzisiejsze faktury)
    PYTHONPATH=src uv run python scripts/run_flow_now.py fv.pdf     # tryb pliku (konkretny PDF)
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from invoicer.adapters.claude_extractor import ClaudeVisionExtractor
from invoicer.adapters.claude_reasoner import ClaudeExceptionReasoner
from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.adapters.twilio_whatsapp import TwilioError, build_twilio_whatsapp_channel
from invoicer.graph.build import build_invoice_graph
from invoicer.ledger import Ledger
from invoicer.models import InvoiceDocument
from invoicer.runner import (
    document_from_upload,
    persistent_checkpointer,
    resume_document,
    start_document,
)
from invoicer.webhook import parse_decision

SID = os.environ["TWILIO_ACCOUNT_SID"]
TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
APPROVER = os.environ["APPROVER_WHATSAPP_TO"]
SANDBOX = os.environ["TWILIO_WHATSAPP_FROM"]
TIMEOUT_MIN = 15
POLL_SEC = 10
_DB = "invoicer_state.sqlite"


def _sink():
    if os.getenv("INVOICER_SINK", "").lower() == "fakturownia":
        from invoicer.adapters.fakturownia import build_fakturownia_sink

        return build_fakturownia_sink()
    return MockSubiektSink()


def _documents() -> list[InvoiceDocument]:
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        return [document_from_upload(path.name, path.read_bytes())]
    from invoicer.adapters.claude_detector import ClaudeInvoiceDetector
    from invoicer.adapters.gmail import GmailAdapter
    from invoicer.adapters.gmail_auth import gmail_service_from_token
    from invoicer.runner import fetch_invoice_documents

    service = gmail_service_from_token(Path(os.getenv("GMAIL_TOKEN", "token.json")))
    sender = os.environ["GMAIL_SENDER_FILTER"]
    return fetch_invoice_documents(GmailAdapter(service), ClaudeInvoiceDetector(), sender)


def _wait_for_decision(client: httpx.Client, since: datetime):
    """Czeka (poll Twilio) na odpowiedz inbound od APPROVER po `since`; zwraca ('approve'/'reject', body) lub (None, None)."""
    url = f"https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json"
    deadline = since + timedelta(minutes=TIMEOUT_MIN)
    while datetime.now(timezone.utc) < deadline:
        r = client.get(
            url, params={"To": SANDBOX, "From": APPROVER, "PageSize": 20}, auth=(SID, TOKEN)
        )
        r.raise_for_status()
        for m in r.json().get("messages", []):
            if m.get("direction") != "inbound":
                continue
            stamp = m.get("date_sent") or m.get("date_created")
            when = parsedate_to_datetime(stamp) if stamp else None
            if when is None or when < since:
                continue
            decision = parse_decision(m.get("body") or "")
            if decision:
                return decision, (m.get("body") or "")
        time.sleep(POLL_SEC)
    return None, None


def main() -> None:
    graph = build_invoice_graph(
        extractor=ClaudeVisionExtractor(),
        reasoner=ClaudeExceptionReasoner(),
        ledger=Ledger(Path("ledger.jsonl")),
        sink=_sink(),
        checkpointer=persistent_checkpointer(_DB),
    )
    channel = build_twilio_whatsapp_channel()
    docs = _documents()
    if not docs:
        print("Brak faktur (dzis nic nie znaleziono / nie wykryto faktury).")
        return

    with httpx.Client(timeout=30.0) as client:
        for doc in docs:
            thread_id = f"now-{uuid.uuid4()}"
            print(f"\n[{doc.filename}] ekstrakcja + bramka...", flush=True)
            payload = start_document(graph, doc, thread_id=thread_id)
            if payload is None:
                print(f"[{doc.filename}] graf nie zatrzymal sie na bramce — pomijam.", flush=True)
                continue
            since = datetime.now(timezone.utc)
            try:
                channel.request_approval(payload)
            except TwilioError as exc:
                print(f"[{doc.filename}] BLAD wysylki WhatsApp: {exc}", flush=True)
                continue
            print(
                f"[{doc.filename}] wyslano: {payload['seller']} / NIP {payload.get('seller_nip') or '—'} / "
                f"{payload['total_gross']} {payload['currency']}. Odpowiedz TAK/NIE (max {TIMEOUT_MIN} min)...",
                flush=True,
            )
            decision, body = _wait_for_decision(client, since)
            if decision is None:
                print(f"[{doc.filename}] TIMEOUT — brak odpowiedzi, nie zaksiegowano.", flush=True)
                continue
            print(f"[{doc.filename}] odpowiedz: '{body.strip()}' -> {decision}", flush=True)
            try:
                state = resume_document(graph, thread_id=thread_id, decision=decision)
            except RuntimeError as exc:
                print(f"[{doc.filename}] RESUME przerwany: {exc}", flush=True)
                continue
            if decision != "approve":
                print(f"[{doc.filename}] ODRZUCONO — nic nie zaksiegowano.", flush=True)
                continue
            booking = state.get("booking")
            if booking is not None:
                print(f"[{doc.filename}] ZAKSIEGOWANO: numer/id = {booking.booking_id}", flush=True)
            else:
                print(
                    f"[{doc.filename}] APPROVE, ale brak booking w stanie — sprawdz logi/sink.",
                    flush=True,
                )


if __name__ == "__main__":
    main()
