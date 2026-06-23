from __future__ import annotations

from fastapi import FastAPI, Form

from invoicer.runner import resume_document

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


def create_inbound_app(graph, registry, *, resume=resume_document) -> FastAPI:
    """FastAPI z webhookiem Twilio (POST /whatsapp/inbound).

    Twilio wola endpoint przy odpowiedzi WhatsApp (form: From, Body). Parsuje TAK/NIE,
    bierze najstarszy pending thread dla numeru (registry) i wznawia graf (resume).
    `resume` wstrzykiwany (CI: fake; domyslnie resume_document).
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
        resume(graph, thread_id=thread_id, decision=decision)
        return {"status": "resumed", "decision": decision, "thread_id": thread_id}

    return app
