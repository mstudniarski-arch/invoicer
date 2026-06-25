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
from invoicer.processed import ProcessedDocuments, document_key
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


def _existing_booking_id(ledger: Ledger, number: str, nip: str | None, name: str) -> str | None:
    """Zwraca booking_id z ledger dla danej faktury (po kluczu duplikatu) lub None."""
    for entry in ledger.entries():
        same_id = entry.seller_nip == nip if nip else entry.seller_name == name
        if entry.number == number and same_id:
            return entry.booking_id
    return None


def main() -> None:
    ledger = Ledger(Path("ledger.jsonl"))
    graph = build_invoice_graph(
        extractor=ClaudeVisionExtractor(),
        reasoner=ClaudeExceptionReasoner(),
        ledger=ledger,
        sink=_sink(),
        checkpointer=persistent_checkpointer(_DB),
    )
    channel = build_twilio_whatsapp_channel()
    processed = ProcessedDocuments(_DB)
    docs = _documents()
    if not docs:
        print("Brak faktur (dzis nic nie znaleziono / nie wykryto faktury).")
        return
    print(
        f"INFO: dedup ON ({len(docs)} dok); duplikaty pomijane PRZED i PO ekstrakcji.",
        flush=True,
    )
    stats = {
        "dedup": 0,
        "duplicate": 0,
        "sent": 0,
        "booked": 0,
        "rejected": 0,
        "timeout": 0,
        "error": 0,
    }

    with httpx.Client(timeout=30.0) as client:
        for doc in docs:
            key = document_key(doc)
            if processed.seen(key):
                # dedup PRZED ekstrakcja — nie placimy za ponowny Claude na juz obsluzonej fakturze
                print(f"[{doc.filename}] juz obsluzony wczesniej — pomijam (dedup).", flush=True)
                stats["dedup"] += 1
                continue
            thread_id = f"now-{uuid.uuid4()}"
            print(f"\n[{doc.filename}] ekstrakcja + bramka...", flush=True)
            payload = start_document(graph, doc, thread_id=thread_id)
            if payload is None:
                # graf pominal — wyciagnij STATE i powiedz dlaczego (z booking_id z ledger)
                state = graph.get_state({"configurable": {"thread_id": thread_id}}).values
                inv = state.get("invoice")
                val = state.get("validation")
                if val is not None and val.is_duplicate and inv is not None:
                    bid = _existing_booking_id(ledger, inv.number, inv.seller.nip, inv.seller.name)
                    print(
                        f"[{doc.filename}] DUPLIKAT: faktura {inv.number} (NIP "
                        f"{inv.seller.nip or '—'}) juz zaksiegowana"
                        f"{f' (booking_id={bid})' if bid else ''} — pomijam.",
                        flush=True,
                    )
                    stats["duplicate"] += 1
                else:
                    flags = [c.name for c in val.checks if c.status.value == "fail"] if val else []
                    print(
                        f"[{doc.filename}] graf zakonczyl bez bramki (validation flags={flags}) "
                        f"— pomijam.",
                        flush=True,
                    )
                    stats["error"] += 1
                processed.mark(key, "skipped")
                continue
            # Defense-in-depth: niezaleznie od decyzji grafu, jesli wyekstrahowany numer+NIP
            # jest duplikatem w ledger — NIE wysylaj WhatsApp. Chroni przed przypadkami gdy
            # graf z jakiegokolwiek powodu puscil duplikat (stary kod, zalegly checkpoint itp.).
            if ledger.is_duplicate(payload["number"], payload.get("seller_nip"), payload["seller"]):
                bid = _existing_booking_id(
                    ledger, payload["number"], payload.get("seller_nip"), payload["seller"]
                )
                print(
                    f"[{doc.filename}] DUPLIKAT (defense): {payload['number']} "
                    f"juz w ledger{f' (booking_id={bid})' if bid else ''} — NIE wysylam WhatsApp.",
                    flush=True,
                )
                processed.mark(key, "skipped")
                stats["duplicate"] += 1
                continue
            since = datetime.now(timezone.utc)
            try:
                channel.request_approval(payload)
            except TwilioError as exc:
                print(f"[{doc.filename}] BLAD wysylki WhatsApp: {exc}", flush=True)
                stats["error"] += 1
                continue
            stats["sent"] += 1
            print(
                f"[{doc.filename}] wyslano: {payload['seller']} / NIP {payload.get('seller_nip') or '—'} / "
                f"{payload['total_gross']} {payload['currency']}. Odpowiedz TAK/NIE (max {TIMEOUT_MIN} min)...",
                flush=True,
            )
            decision, body = _wait_for_decision(client, since)
            if decision is None:
                print(f"[{doc.filename}] TIMEOUT — brak odpowiedzi, nie zaksiegowano.", flush=True)
                stats["timeout"] += 1
                continue
            print(f"[{doc.filename}] odpowiedz: '{body.strip()}' -> {decision}", flush=True)
            try:
                state = resume_document(graph, thread_id=thread_id, decision=decision)
            except RuntimeError as exc:
                print(f"[{doc.filename}] RESUME przerwany: {exc}", flush=True)
                stats["error"] += 1
                continue
            if decision != "approve":
                print(f"[{doc.filename}] ODRZUCONO — nic nie zaksiegowano.", flush=True)
                processed.mark(key, "rejected")  # jawne NIE -> nie ofiarowuj ponownie
                stats["rejected"] += 1
                continue
            booking = state.get("booking")
            if booking is not None:
                print(f"[{doc.filename}] ZAKSIEGOWANO: numer/id = {booking.booking_id}", flush=True)
                processed.mark(key, "done")
                stats["booked"] += 1
            else:
                print(
                    f"[{doc.filename}] APPROVE, ale brak booking w stanie — sprawdz logi/sink.",
                    flush=True,
                )
                stats["error"] += 1

    print(
        f"\nRAZEM ({len(docs)} dok): "
        f"zaksiegowane={stats['booked']}, duplikaty={stats['duplicate']}, "
        f"juz obsluzone={stats['dedup']}, odrzucone={stats['rejected']}, "
        f"timeout={stats['timeout']}, bledy={stats['error']}, "
        f"wyslane (oczekujace)={stats['sent'] - stats['booked'] - stats['rejected'] - stats['timeout']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
