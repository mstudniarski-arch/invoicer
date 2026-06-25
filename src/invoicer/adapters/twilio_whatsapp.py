from __future__ import annotations

import os
import re

from invoicer.security import redact_pii

_TWILIO_SID = re.compile(r"\bAC[0-9a-fA-F]{10,32}\b")


class TwilioError(RuntimeError):
    """Blad wysylki przez Twilio (status != 2xx). Komunikat ma PII zredagowane."""


def format_approval_message(payload: dict) -> str:
    """Tresc requestu akceptacji na WhatsApp: sprzedawca, NIP, kwota + instrukcja TAK/NIE."""
    return (
        f"🧾 Faktura {payload['number']}\n"
        f"Od: {payload['seller']}\n"
        f"NIP: {payload.get('seller_nip') or '—'}\n"
        f"Kwota: {payload['total_gross']} {payload['currency']}\n"
        f"Traktowanie: {payload.get('treatment', '—')}\n"
        f"Odpowiedz TAK (zatwierdz) lub NIE (odrzuc)."
    )


class TwilioWhatsAppChannel:
    """ApprovalChannel: wysyla request akceptacji jako wiadomosc WhatsApp przez Twilio REST.

    `client` wstrzykiwany (CI: fake; live: httpx.Client) z `post(url, *, data, auth) -> resp`.
    `auth_token` nigdy nie trafia do logow; bledy idą przez redact_pii.
    """

    def __init__(
        self,
        client,
        *,
        account_sid: str,
        auth_token: str,
        from_whatsapp: str,
        to_whatsapp: str,
    ) -> None:
        self._client = client
        self._sid = account_sid
        self._token = auth_token
        self._from = from_whatsapp
        self._to = to_whatsapp

    def request_approval(self, payload: dict) -> None:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json"
        data = {"From": self._from, "To": self._to, "Body": format_approval_message(payload)}
        resp = self._client.post(url, data=data, auth=(self._sid, self._token))
        if not 200 <= resp.status_code < 300:
            # url zawiera SID — NIE wkladamy go do bledu (redact_pii i tak redaguje SID)
            snippet = redact_pii(str(resp.text))[:500]
            raise TwilioError(f"Twilio POST -> {resp.status_code}: {snippet}")

    def notify(self, text: str) -> None:
        """Wysyla dowolna wiadomosc WhatsApp (alert/notyfikacja) do skonfigurowanego approvera."""
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json"
        data = {"From": self._from, "To": self._to, "Body": text}
        resp = self._client.post(url, data=data, auth=(self._sid, self._token))
        if not 200 <= resp.status_code < 300:
            snippet = _TWILIO_SID.sub("[REDACTED_SID]", redact_pii(str(resp.text)))[:500]
            if self._sid in snippet:
                snippet = snippet.replace(self._sid, "[REDACTED_SID]")
            raise TwilioError(f"Twilio POST -> {resp.status_code}: {snippet}")


def build_twilio_whatsapp_channel() -> TwilioWhatsAppChannel:
    """Buduje kanal z env (TWILIO_ACCOUNT_SID/AUTH_TOKEN/WHATSAPP_FROM, APPROVER_WHATSAPP_TO)."""
    import httpx

    return TwilioWhatsAppChannel(
        httpx.Client(timeout=30.0),
        account_sid=os.environ["TWILIO_ACCOUNT_SID"],
        auth_token=os.environ["TWILIO_AUTH_TOKEN"],
        from_whatsapp=os.environ["TWILIO_WHATSAPP_FROM"],
        to_whatsapp=os.environ["APPROVER_WHATSAPP_TO"],
    )
