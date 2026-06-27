from __future__ import annotations

import json
import os
import re
import time

from invoicer.security import redact_pii

_TWILIO_SID = re.compile(r"\bAC[0-9a-fA-F]{10,32}\b")

# Statusy wiadomosci Twilio (https://www.twilio.com/docs/messaging/api/message-resource#message-status-values)
_DELIVERED = {"delivered", "read"}
_FAILED = {"failed", "undelivered"}


class TwilioError(RuntimeError):
    """Blad wysylki przez Twilio (status != 2xx). Komunikat ma PII zredagowane."""


class TwilioUndelivered(TwilioError):
    """Twilio PRZYJAL wiadomosc (201), ale WhatsApp jej NIE dostarczyl.

    Najczestsza przyczyna: error_code 63016 — wiadomosc free-form poza 24h oknem WhatsApp.
    Approver musi najpierw napisac cokolwiek do numeru sandbox, by otworzyc okno na nowo.
    """

    def __init__(self, status: str | None, error_code, message: str) -> None:
        self.status = status
        self.error_code = error_code
        super().__init__(message)


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


def _redact_sid(text: str, sid: str) -> str:
    """Redaguje PII oraz Twilio SID (z url-a/body) zanim komunikat trafi do logow/Sentry."""
    snippet = _TWILIO_SID.sub("[REDACTED_SID]", redact_pii(str(text)))[:500]
    return snippet.replace(sid, "[REDACTED_SID]") if sid in snippet else snippet


class TwilioWhatsAppChannel:
    """ApprovalChannel: wysyla request akceptacji jako wiadomosc WhatsApp przez Twilio REST.

    `client` wstrzykiwany (CI: fake; live: httpx.Client) z `post(url, *, data, auth) -> resp`
    oraz `get(url, *, auth) -> resp` (poll statusu dostarczenia).
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

    def _messages_url(self) -> str:
        return f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json"

    def _post_message(self, body: str) -> str | None:
        """Wysyla wiadomosc; zwraca message SID (do pollingu statusu) lub None gdy brak w body."""
        data = {"From": self._from, "To": self._to, "Body": body}
        resp = self._client.post(self._messages_url(), data=data, auth=(self._sid, self._token))
        if not 200 <= resp.status_code < 300:
            raise TwilioError(
                f"Twilio POST -> {resp.status_code}: {_redact_sid(resp.text, self._sid)}"
            )
        try:
            return json.loads(resp.text or "{}").get("sid")
        except ValueError:
            return None

    def _confirm_delivery(self, sid: str, *, attempts: int, interval: float, sleep) -> None:
        """Odpytuje Twilio o status wiadomosci `sid` az do stanu terminalnego.

        201 z POST oznacza tylko 'przyjete do kolejki' — realne dostarczenie WhatsApp jest
        asynchroniczne. Bez tego sprawdzenia flow falszywie loguje "wyslano" i czeka 15 min.
        Rzuca TwilioUndelivered gdy status=failed/undelivered (np. 63016 = poza 24h oknem).
        Po wyczerpaniu prob bez stanu terminalnego — wraca cicho (nie blokuje na queued/sent).
        """
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages/{sid}.json"
        for attempt in range(attempts):
            resp = self._client.get(url, auth=(self._sid, self._token))
            if not 200 <= resp.status_code < 300:
                return  # nie potwierdzilismy, ale brak odczytu statusu nie jest bledem dostarczenia
            try:
                body = json.loads(resp.text or "{}")
            except ValueError:
                return
            status = body.get("status")
            error_code = body.get("error_code")
            if status in _FAILED or error_code:
                hint = (
                    " — wiadomosc poza 24h oknem WhatsApp; approver musi najpierw napisac "
                    "cokolwiek do numeru sandbox, aby otworzyc okno"
                    if str(error_code) == "63016"
                    else ""
                )
                raise TwilioUndelivered(
                    status,
                    error_code,
                    f"WhatsApp niedostarczony: status={status} error_code={error_code}{hint}",
                )
            if status in _DELIVERED:
                return
            if attempt < attempts - 1:
                sleep(interval)

    def request_approval(
        self,
        payload: dict,
        *,
        confirm_delivery: bool = True,
        poll_attempts: int = 6,
        poll_interval: float = 2.0,
        sleep=time.sleep,
    ) -> None:
        sid = self._post_message(format_approval_message(payload))
        if confirm_delivery and sid:
            self._confirm_delivery(sid, attempts=poll_attempts, interval=poll_interval, sleep=sleep)

    def notify(self, text: str) -> None:
        """Wysyla dowolna wiadomosc WhatsApp (alert/notyfikacja) do skonfigurowanego approvera."""
        self._post_message(text)


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
