import pytest

from invoicer.adapters.stub_approval import StubApprovalChannel
from invoicer.ports import ApprovalChannel

# _PAYLOAD: minimalny podzbior kluczy — pelny ksztalt patrz nodes.human_review
_PAYLOAD = {
    "number": "FV/1",
    "seller": "ACME",
    "seller_nip": "5260001246",
    "total_gross": "1230.00",
    "currency": "PLN",
    "treatment": "krajowa",
}


def test_stub_records_calls():
    ch = StubApprovalChannel()
    ch.request_approval(_PAYLOAD)
    assert ch.sent == [_PAYLOAD]


def test_stub_satisfies_approval_channel_protocol():
    assert isinstance(StubApprovalChannel(), ApprovalChannel)


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[dict] = []

    def post(self, url, *, data=None, auth=None):
        self.calls.append({"url": url, "data": data, "auth": auth})
        return self._response


def _channel(response):
    from invoicer.adapters.twilio_whatsapp import TwilioWhatsAppChannel

    client = _FakeClient(response)
    ch = TwilioWhatsAppChannel(
        client,
        account_sid="AC123",
        auth_token="tok",
        from_whatsapp="whatsapp:+14155238886",
        to_whatsapp="whatsapp:+48500100200",
    )
    return ch, client


def test_format_message_has_seller_nip_amount():
    from invoicer.adapters.twilio_whatsapp import format_approval_message

    msg = format_approval_message(_PAYLOAD)
    assert "ACME" in msg
    assert "5260001246" in msg
    assert "1230.00 PLN" in msg
    assert "TAK" in msg and "NIE" in msg


def test_request_approval_posts_to_twilio():
    ch, client = _channel(_FakeResponse(201))
    ch.request_approval(_PAYLOAD)
    call = client.calls[0]
    assert call["url"] == "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
    assert call["auth"] == ("AC123", "tok")
    assert call["data"]["From"] == "whatsapp:+14155238886"
    assert call["data"]["To"] == "whatsapp:+48500100200"
    assert "5260001246" in call["data"]["Body"]


def test_request_approval_raises_and_redacts_on_error():
    from invoicer.adapters.twilio_whatsapp import TwilioError

    ch, _ = _channel(_FakeResponse(401, text="blad: token dla NIP 5260001246, mail x@y.pl"))
    with pytest.raises(TwilioError) as exc:
        ch.request_approval(_PAYLOAD)
    msg = str(exc.value)
    assert "401" in msg
    assert "5260001246" not in msg
    assert "x@y.pl" not in msg


def test_twilio_channel_satisfies_protocol():
    ch, _ = _channel(_FakeResponse(201))
    assert isinstance(ch, ApprovalChannel)


def test_format_message_shows_dash_when_seller_nip_none():
    from invoicer.adapters.twilio_whatsapp import format_approval_message

    msg = format_approval_message({**_PAYLOAD, "seller_nip": None})
    assert "NIP: —" in msg
    assert "None" not in msg
