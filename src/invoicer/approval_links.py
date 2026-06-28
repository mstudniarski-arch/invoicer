"""Podpisane linki akceptacji (tap-to-approve) zamiast odpowiedzi na webhook Twilio.

Wiadomosc WhatsApp dostaje dwa linki celujace w nasza apke: /approve/{thread_id} i
/reject/{thread_id}, kazdy z tokenem HMAC-SHA256 nad (thread_id, decision). Bez wlasciwego
tokenu link nie zadziala (403), wiec nikt nie zaksieguje faktury zgadujac thread_id.
"""

from __future__ import annotations

import hashlib
import hmac


def sign_decision(secret: str, thread_id: str, decision: str) -> str:
    """Token HMAC-SHA256 (hex) nad 'thread_id:decision' — wiaze link z konkretna decyzja."""
    msg = f"{thread_id}:{decision}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_decision(secret: str, thread_id: str, decision: str, token: str) -> bool:
    """Stalo-czasowe porownanie tokenu z linku z oczekiwanym (chroni przed timing-attack)."""
    expected = sign_decision(secret, thread_id, decision)
    return hmac.compare_digest(expected, token or "")


def build_decision_links(base_url: str, thread_id: str, secret: str) -> dict[str, str]:
    """Para linkow {'approve': ..., 'reject': ...} z podpisanymi tokenami dla thread_id."""
    base = base_url.rstrip("/")
    return {
        "approve": f"{base}/approve/{thread_id}?t={sign_decision(secret, thread_id, 'approve')}",
        "reject": f"{base}/reject/{thread_id}?t={sign_decision(secret, thread_id, 'reject')}",
    }
