import os

import pytest

from invoicer.adapters.twilio_whatsapp import build_twilio_whatsapp_channel

pytestmark = pytest.mark.skipif(
    not (
        os.getenv("TWILIO_ACCOUNT_SID")
        and os.getenv("TWILIO_AUTH_TOKEN")
        and os.getenv("TWILIO_WHATSAPP_FROM")
        and os.getenv("APPROVER_WHATSAPP_TO")
    ),
    reason="wymaga TWILIO_* + APPROVER_WHATSAPP_TO (test live)",
)


def test_sends_real_whatsapp_approval_request():
    payload = {
        "number": "FV/LIVE/1",
        "seller": "ACME Test",
        "seller_nip": "5260001246",
        "total_gross": "1230.00",
        "currency": "PLN",
        "treatment": "krajowa",
    }
    build_twilio_whatsapp_channel().request_approval(payload)  # brak wyjatku = wyslane
