from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import FastAPI, Form

from invoicer.runner import resume_document
from invoicer.security import redact_pii

_logger = logging.getLogger("invoicer.webhook")

_APPROVE = {"tak", "yes", "approve", "1", "t"}
_REJECT = {"nie", "no", "reject", "2", "n"}


def parse_decision(body: str) -> str | None:
    """Mapuje tresc odpowiedzi WhatsApp na decyzje: 'approve' / 'reject' / None (nierozpoznane)."""
    token = body.strip().lower()
    if token in _APPROVE:
        return "approve"
    if token in _REJECT:
        return "reject"
    return None


def create_inbound_app(
    graph,
    registry,
    *,
    resume=resume_document,
    on_resume_failure: Callable[[str, Exception], None] | None = None,
) -> FastAPI:
    """FastAPI z webhookiem Twilio (POST /whatsapp/inbound).

    Twilio wola endpoint przy odpowiedzi WhatsApp (form: From, Body). Parsuje TAK/NIE,
    bierze najstarszy pending thread dla numeru (registry) i wznawia graf (resume).
    `resume` wstrzykiwany (CI: fake; domyslnie resume_document).

    Uwaga: walidacja podpisu Twilio (X-Twilio-Signature) celowo pominieta w MVP (spec sek. 7);
    endpoint wznawia tylko ISTNIEJACE pending dla danego numeru.
    """
    app = FastAPI()

    @app.post("/whatsapp/inbound")
    def inbound(From: str = Form(...), Body: str = Form(...)) -> dict:
        decision = parse_decision(Body)
        if decision is None:
            return {"status": "ignored"}
        thread_id = registry.resolve_oldest(From)
        if thread_id is None:
            return {"status": "no_pending"}
        try:
            resume(graph, thread_id=thread_id, decision=decision)
        except Exception as exc:  # noqa: BLE001 - webhook musi zwrocic 2xx (brak retry-storm Twilio)
            _logger.error("resume nieudany dla %s: %s", thread_id, redact_pii(str(exc)))
            if on_resume_failure is not None:
                on_resume_failure(thread_id, exc)
            return {"status": "resume_failed", "thread_id": thread_id}
        return {"status": "resumed", "decision": decision, "thread_id": thread_id}

    return app
