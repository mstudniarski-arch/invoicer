from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from collections.abc import Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from invoicer.approval_links import verify_decision
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


def compute_twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Podpis X-Twilio-Signature: base64(HMAC-SHA1(auth_token, url + posortowane k+v)).

    Zgodne ze specyfikacja Twilio (form-encoded POST): do pelnego URL-a docelowego
    doczepia sie wartosci parametrow posortowane po kluczu (klucz+wartosc, bez separatorow),
    a calosc podpisuje HMAC-SHA1 tokenem konta i koduje base64.
    """
    signed = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode("utf-8"), signed.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def create_inbound_app(
    graph,
    registry,
    *,
    resume=resume_document,
    on_resume_failure: Callable[[str, Exception], None] | None = None,
    twilio_auth_token: str | None = None,
    public_url: str | None = None,
    link_secret: str | None = None,
) -> FastAPI:
    """FastAPI z webhookiem Twilio (POST /whatsapp/inbound).

    Twilio wola endpoint przy odpowiedzi WhatsApp (form: From, Body). Parsuje TAK/NIE,
    bierze najstarszy pending thread dla numeru (registry) i wznawia graf (resume).
    `resume` wstrzykiwany (CI: fake; domyslnie resume_document).

    Walidacja podpisu: gdy podano `twilio_auth_token` ORAZ `public_url`, kazde zadanie
    musi miec poprawny naglowek X-Twilio-Signature (inaczej 403). Chroni PUBLICZNY endpoint
    przed sfalszowanym 'TAK', ktory zaksiegowalby realny koszt. Bez tych dwoch wartosci
    walidacja jest wylaczona (lokalne/CI). `public_url` musi byc dokladnym, publicznym URL-em
    pod ktory Twilio wysyla webhook (z https), bo to on jest czescia podpisywanego ciagu.
    """
    app = FastAPI()
    enforce_signature = bool(twilio_auth_token and public_url)

    @app.post("/whatsapp/inbound", response_model=None)
    async def inbound(request: Request) -> Response | dict:
        form = await request.form()
        params = {k: str(v) for k, v in form.items()}
        if enforce_signature:
            sig = request.headers.get("X-Twilio-Signature", "")
            expected = compute_twilio_signature(twilio_auth_token, public_url, params)
            if not (sig and hmac.compare_digest(expected, sig)):
                _logger.warning("odrzucono webhook: niepoprawny/brak podpisu Twilio")
                return JSONResponse({"status": "invalid_signature"}, status_code=403)
        decision = parse_decision(params.get("Body", ""))
        if decision is None:
            return {"status": "ignored"}
        thread_id = registry.resolve_oldest(params.get("From", ""))
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

    def _page(title: str, body: str, *, status: int = 200) -> HTMLResponse:
        html = (
            "<!doctype html><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Invoicer</title>"
            "<div style='font-family:system-ui,sans-serif;max-width:30rem;margin:16vh auto;"
            "text-align:center;padding:0 1rem'>"
            f"<h1 style='font-size:1.7rem;margin:.2rem 0'>{title}</h1>"
            f"<p style='color:#555;font-size:1rem'>{body}</p></div>"
        )
        return HTMLResponse(html, status_code=status)

    def _handle_decision(thread_id: str, decision: str, token: str) -> HTMLResponse:
        # Tap-to-approve: link niesie thread_id, token HMAC autoryzuje DOKLADNIE te decyzje.
        if not link_secret or not verify_decision(link_secret, thread_id, decision, token):
            _logger.warning("odrzucono link decyzji: niepoprawny token (%s)", decision)
            return _page("❌ Niepoprawny link", "Token nieprawidlowy lub wygasl.", status=403)
        try:
            resume(graph, thread_id=thread_id, decision=decision)
        except Exception as exc:  # noqa: BLE001 - przyjazna strona zamiast 500; bez PII
            _logger.error("resume(link) nieudany dla %s: %s", thread_id, redact_pii(str(exc)))
            if on_resume_failure is not None:
                on_resume_failure(thread_id, exc)
            return _page(
                "⚠️ Juz przetworzone",
                "Ta faktura zostala juz obsluzona albo wystapil blad. Sprawdz Fakturownie.",
            )
        if decision == "approve":
            return _page("✅ Zatwierdzono", "Faktura trafila do ksiegowania (Fakturownia).")
        return _page("❌ Odrzucono", "Faktura nie zostala zaksiegowana.")

    @app.get("/approve/{thread_id}", response_class=HTMLResponse)
    def approve(thread_id: str, t: str = "") -> HTMLResponse:
        return _handle_decision(thread_id, "approve", t)

    @app.get("/reject/{thread_id}", response_class=HTMLResponse)
    def reject(thread_id: str, t: str = "") -> HTMLResponse:
        return _handle_decision(thread_id, "reject", t)

    return app
